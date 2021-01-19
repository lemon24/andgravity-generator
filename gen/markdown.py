import mistune.directives
from mistune import escape
from mistune import escape_html
from mistune import escape_url
from pygments import highlight
from pygments.formatters import get_formatter_by_name
from pygments.lexers import get_lexer_by_name
from pygments.lexers import guess_lexer
from slugify import slugify


WIKI_PATTERN = r'\[\[' r'([\s\S]+?\|?[\s\S]+?)' r'\]\](?!\])'  # [[ link|title ]]


def parse_wiki(self, m, state):
    text = m.group(1)
    link, sep, title = text.partition('|')
    link = link.strip()
    title = title.strip()
    if not sep:
        title = None
    return 'wiki', link, title


def make_wiki_plugin(build_url):
    def render_html_wiki(link, title):
        link_href = escape_url(build_url(link))
        if not title:
            title = link
        return f'<a href="{link_href}">{title}</a>'

    def plugin_wiki(md):
        md.inline.register_rule('wiki', WIKI_PATTERN, parse_wiki)
        md.inline.rules.append('wiki')
        if md.renderer.NAME == 'html':
            md.renderer.register('wiki', render_html_wiki)

    return plugin_wiki


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


def parse_options(rest: str, code: str) -> dict:
    """Parse a highlighting options string to Pygments HTML formatter option.

    The options are named after those of the Sphinx code-block directive.
    Unlike Sphinx directives, they are space-separated "key(=value)" pairs.

    All options:

        linenos emphasize-lines=2,4-6,8- lineno-start=10

    Unknown options are ignored.

    """
    rest = rest.strip()
    if not rest:
        return {}

    options = {}
    for part in rest.split():
        key, sep, value = part.partition('=')
        options[key] = value

    rv = {}

    if 'linenos' in options:
        rv['linenos'] = options.pop('linenos').lower() not in (
            'n',
            'no',
            'false',
            'off',
        )

    if 'emphasize-lines' in options:
        rv['hl_lines'] = [
            lineno + 1
            for lineno in parselinenos(
                options.pop('emphasize-lines'), len(code.splitlines())
            )
        ]

    if 'lineno-start' in options:
        rv['linenostart'] = int(options.pop('lineno-start'))
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


class MyRenderer(mistune.HTMLRenderer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def block_code(self, code, info=None):
        if info is not None:
            info = info.strip()
        if info:
            return self._block_code_info(code, info)
        return self._block_code_noinfo(code)

    def _block_code_noinfo(self, code):
        return (
            '<pre class="code code-container"><code>' + escape(code) + '</code></pre>\n'
        )

    def _block_code_info(self, code, info):
        lang, *rest = info.split(None, 1)

        options = {'wrapcode': True}
        rest = rest and rest[0]
        if rest:
            options.update(parse_options(rest, code))

        # To generate a unique (and stable-ish) id to use as lineanchors,
        # we need to know how many code blocks with the same filename
        # there have been in this document; I don't know how to do that
        # (we want to pass some state around for the document,
        # like the toc directive does).

        html, data_lang = do_highlight(code, lang, options)

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

    def table(self, text):
        return '<table class="table">\n' + text + '</table>\n'


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


def make_markdown(build_url):
    return mistune.create_markdown(
        renderer=MyRenderer(escape=False),
        plugins=[
            'strikethrough',
            'footnotes',
            'table',
            'task_lists',
            # broken at the moment
            # 'def_list',
            mistune.directives.DirectiveToc(),
            mistune.directives.Admonition(),
            make_wiki_plugin(build_url),
            plugin_toc_fix,
            plugin_footnotes_fix,
        ],
    )
