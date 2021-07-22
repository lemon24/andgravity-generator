import mistune.directives
from mistune import escape
from mistune import escape_html
from mistune import escape_url
from mistune.directives import Directive
from pygments import highlight
from pygments.formatters import get_formatter_by_name
from pygments.lexers import get_lexer_by_name
from pygments.lexers import guess_lexer, get_lexer_for_filename
from slugify import slugify
from pygments.util import ClassNotFound 

def do_highlight(code, lang, options=None):
    options = options or {}
    try:
        lexer = get_lexer_by_name(lang, **options)
    except ValueError:
        try:
            lexer = guess_lexer(code, **options)
        except ValueError:
            lexer = get_lexer_by_name('text', **options)
    formatter = get_formatter_by_name('html', **options)
    return highlight(code, lexer, formatter), lexer.name


def parselinenos(spec: str, total: int) -> list:
    """parselinenos('2,4-6,8-', 9) -> [1, 3, 4, 5, 7, 8]"""
    # from sphinx.util
    items = list()
    parts = spec.split(',')
    for part in parts:
        try:
            begend = part.strip().split('-')
            if ['', ''] == begend:
                raise ValueError
            elif len(begend) == 1:
                items.append(int(begend[0]) - 1)
            elif len(begend) == 2:
                start = int(begend[0] or 1)  # left half open (cf. -10)
                end = int(begend[1] or max(start, total))  # right half open (cf. 10-)
                if start > end:  # invalid range (cf. 10-1)
                    raise ValueError
                items.extend(range(start - 1, end))
            else:
                raise ValueError
        except Exception as exc:
            raise ValueError('invalid line number spec: %r' % spec) from exc

    return items


def parse_block_code_options(info: str) -> dict:
    """Parse a highlighting options string to Pygments HTML formatter option.

    The options are named after those of the Sphinx code-block directive.
    Unlike Sphinx directives, they are space-separated "key(=value)" pairs.

    All options:

        <lang> linenos emphasize-lines=2,4-6,8- lineno-start=10

    Unknown options are ignored.

    """
    lang, *rest = info.split(None, 1)

    options = {'language': lang}

    rest = rest and rest[0]
    if rest:
        rest = rest.strip()
    if not rest:
        return options

    for part in rest.split():
        key, sep, value = part.partition('=')
        options[key] = value
        
    return options


def to_pygments_options(options, line_count):
    rv = {}

    if 'linenos' in options:
        rv['linenos'] = options['linenos'].lower() not in (
            'n',
            'no',
            'false',
            'off',
        )

    if 'emphasize-lines' in options:
        rv['hl_lines'] = [
            lineno + 1
            for lineno in parselinenos(options['emphasize-lines'], line_count)
        ]

    if 'lineno-start' in options:
        rv['linenostart'] = int(options['lineno-start'])
        rv.setdefault('linenos', True)

    # disable caption for now;
    # we don't know how to parse quoted literals (so it must be a single word),
    # and filename results in
    #   <div class="highlight"><span class="filename">file.py</span>
    # which is really hard to style
    """
    if 'caption' in options:
        rv['filename'] = options.pop('caption')
    """

    return rv


def render_highlighed_code(code, options):
    options = dict(options)
    lang = options.pop('language')
    pygments_options = {'wrapcode': True}
    pygments_options.update(to_pygments_options(options, len(code.splitlines())))

    # To generate a unique (and stable-ish) id to use as lineanchors,
    # we need to know how many code blocks with the same filename
    # there have been in this document; I don't know how to do that
    # (we want to pass some state around for the document,
    # like the toc directive does).

    html, data_lang = do_highlight(code, lang, pygments_options)

    linenos_pre_index = html.index('<pre>')
    try:
        code_pre_index = html.index('<pre>', linenos_pre_index + 1)
    except ValueError:
        code_pre_index, linenos_pre_index = linenos_pre_index, None

    # wrapcode doesn't work for the linenos, so we add it by hand
    if linenos_pre_index:
        html = html.replace('<pre>', '<pre class="code"><code>', 1)
        html = html.replace('</pre>', '</code></pre>', 1)

    html = html.replace(
        '<pre>', '<pre class="code" data-lang="' + escape_html(data_lang) + '">', 1
    )

    # add .code-container to the outermost element
    if linenos_pre_index:
        html = html.replace(
            '<table class="highlighttable">',
            '<table class="highlighttable code-container">',
        )
    else:
        html = html.replace(
            '<div class="highlight">', '<div class="highlight code-container">'
        )

    return html


def render_plain_code(code):
    return (
        '<pre class="code code-container"><code>' + escape(code) + '</code></pre>\n'
    )


class MyRenderer(mistune.HTMLRenderer):
    def __init__(self, *args, url_rewriters=(), **kwargs):
        super().__init__(*args, **kwargs)
        self._url_rewriters = list(url_rewriters)

    # BEGIN code highlighting mixin

    def block_code(self, code, info=None):
        if info is not None:
            info = info.strip()
        if not info:
            return render_plain_code(code)
        options = parse_block_code_options(info)
        return render_highlighed_code(code, options)

    # END code highlighting mixin

    # BEGIN table custom class mixin

    def table(self, text):
        return '<table class="table">\n' + text + '</table>\n'

    # END table custom class mixin

    # BEGIN url rewriting mixin

    def _rewrite_url(self, url, text):
        for rewriter in self._url_rewriters:
            rv = rewriter(url, text)
            if rv:
                url, text = rv
        return url, text

    def link(self, link, text=None, title=None):
        link, text = self._rewrite_url(link, text)
        return super().link(link, text, title)

    def image(self, src, alt="", title=None):
        src, _ = self._rewrite_url(src, alt)
        rv = super().image(src, alt, title)
        rv = rv.replace('<img ', '<img class="img-responsive" ')
        return rv

    # END url rewriting mixin


def record_toc_heading(text, level, state):
    existing_tids = set(t[0] for t in state['toc_headings'])
    slug = slugify(text)

    tid = slug
    counter = 1
    while tid in existing_tids:
        tid = slug + '-' + str(counter)
        counter += 1

    state['toc_headings'].append((tid, text, level))
    return {'type': 'theading', 'text': text, 'params': (level, tid)}


def render_html_theading(text, level, tid):
    level = level + 1
    tag = 'h' + str(level)

    headerlink = (
        '<span class="headerlink"> <a href="#'
        + escape_url(tid)
        + '" title="permalink">#</a></span>'
    )

    return '<' + tag + ' id="' + tid + '">' + text + headerlink + '</' + tag + '>\n'


def plugin_toc_fix(md):
    md.block.tokenize_heading = record_toc_heading
    if md.renderer.NAME == 'html':
        md.renderer.register('theading', render_html_theading)


def render_html_footnote_item(text, key, index):
    i = str(index)
    back = ' <a href="#fnref-' + i + '" class="footnote"><sup>[return]</sup></a>'

    text = text.rstrip()
    if text.endswith('</p>'):
        text = text[:-4] + back + '</p>'
    else:
        text = text + back
    return '<li id="fn-' + i + '">' + text + '</li>\n'


def plugin_footnotes_fix(md):
    if md.renderer.NAME == 'html':
        md.renderer.register('footnote_item', render_html_footnote_item)


class LiteralInclude(Directive):
    
    def __init__(self, load_lines):
        self.load_lines = load_lines

    def parse(self, block, m, state):
        options = dict(self.parse_options(m))

        path = m.group('value')
        text = self.parse_text(m)
        # TODO handle
        assert not text.strip(), text

        # TODO handle
        lines = self.load_lines(path)

        lines_option = options.pop('lines', '').strip()
        if lines_option:
            only_lines = parselinenos(lines_option, len(lines))
            
            line_distances = {only_lines[i+1] - only_lines[i] for i in range(len(only_lines) - 1)}

            # TODO: handle
            assert line_distances == {1}, f"lines must be contiguous; {lines_option}"
        
            # TODO: handle indexerror
            lines = [lines[i] for i in only_lines]
            
            options.setdefault('lineno-start', only_lines[0] + 1)

        if 'language' not in options:
            try:
                options['language'] = get_lexer_for_filename(path).name
            except ClassNotFound:
                pass

        return {
            'type': 'literalinclude',
            'raw': ''.join(lines),
            'params': (options,)
        }

    def __call__(self, md):
        self.register_directive(md, 'literalinclude')
        if md.renderer.NAME == 'html':
            md.renderer.register('literalinclude', render_html_literalinclude)
        elif md.renderer.NAME == 'ast':
            assert False, "no AST renderer for literalinclude"



def render_html_literalinclude(text, options):
    if 'language' not in options:
        return render_plain_code(text) + '\n'
    return render_highlighed_code(text, options) + '\n'
    

def make_markdown(url_rewriters, load_literal_include_lines=None):
    return mistune.create_markdown(
        renderer=MyRenderer(escape=False, url_rewriters=url_rewriters),
        plugins=[
            'strikethrough',
            'footnotes',
            'table',
            'task_lists',
            'def_list',
            mistune.directives.DirectiveToc(),
            mistune.directives.Admonition(),
            plugin_toc_fix,
            plugin_footnotes_fix,
            LiteralInclude(load_literal_include_lines),
        ],
    )



if __name__ == '__main__':
    
    with open('tests/data/md/09-literalinclude.in') as f:
        text = f.read()

    def rewrite(url, text):
        return url.upper(), text or 'default'   
    
    def load_lines(path):
        return [s + '\n' for s in 'one two three four five'.split()]

    md = make_markdown([rewrite], load_lines)
    
    print(md(text))
    
    
