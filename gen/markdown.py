import re
from functools import lru_cache

import mistune.directives
from mistune import escape
from mistune import escape_html
from mistune import escape_url
from mistune.directives import Directive
from pygments import highlight
from pygments.formatters import get_formatter_by_name
from pygments.lexers import get_lexer_by_name
from pygments.lexers import guess_lexer
from pygments.lexers import guess_lexer_for_filename
from pygments.util import ClassNotFound
from slugify import slugify


def do_highlight(code, lang, options=None):
    options = options or {}
    return _do_highlight(code, lang, **options)


@lru_cache
def _do_highlight(code, lang, **options):
    # This is an optimization for interactive use (`gen serve`),
    # so a transient in-memory cache is fine.
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


FALSY_VALUES = ('n', 'no', 'false', 'off')


def to_pygments_options(options, line_count):
    rv = {}

    if 'linenos' in options:
        rv['linenos'] = options['linenos'].lower() not in FALSY_VALUES

    if 'emphasize-lines' in options:
        # tuple so it's hashable
        rv['hl_lines'] = tuple(
            lineno + 1
            for lineno in parselinenos(options['emphasize-lines'], line_count)
        )

    if 'lineno-start' in options:
        rv['linenostart'] = int(options['lineno-start'])
        rv.setdefault('linenos', True)

    if 'stripnl' in options:
        rv['stripnl'] = options['stripnl'].lower() not in FALSY_VALUES

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
        code_pre_index = html.index('<pre>', linenos_pre_index + 1)  # noqa
    except ValueError:
        code_pre_index, linenos_pre_index = linenos_pre_index, None  # noqa

    # wrapcode doesn't work for the linenos, so we add it by hand
    if linenos_pre_index:
        html = html.replace('<pre>', '<pre class="code"><code>', 1)
        html = html.replace('</pre>', '</code></pre>', 1)

    html = html.replace(
        '<pre>', '<pre class="code" data-lang="' + escape_html(data_lang) + '">', 1
    )

    # add .code-container to the outermost element
    # pygments >= 2.12 required
    html = html.replace(
        '<div class="highlight">', '<div class="highlight code-container">'
    )

    return html


def render_plain_code(code):
    return '<pre class="code code-container"><code>' + escape(code) + '</code></pre>\n'


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
        url_class = None
        for rewriter in self._url_rewriters:
            rv = rewriter(url, text)
            if rv:
                if len(rv) not in (2, 3):
                    raise RuntimeError(
                        f"unexpected rewriter output: {rewriter!r}: {rv!r}"
                    )
                url, text, *_ = rv
                if len(rv) > 2:
                    url_class = rv[2]
        return url, text, url_class

    def link(self, link, text=None, title=None):
        link, text, url_class = self._rewrite_url(link, text)
        rv = super().link(link, text, title)
        if url_class:
            rv = rv.replace('<a ', f'<a class="{url_class}" ')
        return rv

    def image(self, src, alt="", title=None):
        src, _, _ = self._rewrite_url(src, alt)
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
        '<span class="headerlink">&nbsp;<a href="#'
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

        try:
            lines = self.load_lines(path)
        except Exception as e:
            return {
                'type': 'block_error',
                'raw': f"could not open attachment: {type(e).__name__}: {e}",
            }

        lines_option = options.pop('lines', '').strip()
        ellipsis_option = options.pop('ellipsis', '').strip()
        if lines_option:
            only_lines = parselinenos(lines_option, len(lines))

            line_distances = {  # noqa
                only_lines[i + 1] - only_lines[i] for i in range(len(only_lines) - 1)
            }
            # TODO: use line_distances somehow (what was it for?)

            all_lines = lines
            lines = []
            prev_i = None
            for i in only_lines:
                # TODO: handle indexerror
                line = all_lines[i]

                # this messes with line numbers, a fix for that requires
                # https://github.com/pygments/pygments/issues/2322
                if ellipsis_option and prev_i is not None and i != prev_i + 1:
                    indent = re.match(r'^\s*', line).group(0)
                    lines.append(f'{indent}{ellipsis_option}\n')

                lines.append(line)
                prev_i = i

            options.setdefault('lineno-start', only_lines[0] + 1)

        file_text = ''.join(lines)

        if 'language' not in options:
            try:
                options['language'] = guess_language(path, file_text)
            except ClassNotFound:
                pass

        if 'stripnl' not in options:
            options['stripnl'] = 'n'

        return {'type': 'literalinclude', 'raw': file_text, 'params': (options,)}

    def __call__(self, md):
        self.register_directive(md, 'literalinclude')
        if md.renderer.NAME == 'html':
            md.renderer.register('literalinclude', render_html_literalinclude)
        elif md.renderer.NAME == 'ast':
            raise NotImplementedError("no AST renderer for literalinclude")


@lru_cache
def guess_language(path, text):
    # Guessing languages is unbearably slow for some reason.
    # This is an optimization for interactive use (`gen serve`),
    # so a transient in-memory cache is fine.
    # `gen freeze` has less granular per-node caches,
    # so caching this on disk may not help it that as much,
    # but we can give it a try later on.
    return guess_lexer_for_filename(path, text).name


def render_html_literalinclude(text, options):
    if 'language' not in options:
        return render_plain_code(text) + '\n'
    return render_highlighed_code(text, options) + '\n'


class Snippet(Directive):
    def __init__(self, render):
        self.render = render

    def parse(self, block, m, state):
        options = dict(self.parse_options(m))
        value = m.group('value')
        text = self.parse_text(m)

        # TODO: maybe extract some standard options
        try:
            raw = self.render(value, text, options)
        except Exception as e:
            return {
                'type': 'block_error',
                'raw': f"could not render snippet {value!r}: {type(e).__name__}: {e}",
            }

        return {'type': 'block_html', 'raw': raw}

    def __call__(self, md):
        self.register_directive(md, 'snippet')


class Subprocess(Directive):
    def __init__(self, name, args):
        self.name = name
        self.args = tuple(args)

    def parse(self, block, m, state):
        text = self.parse_text(m)

        p = subprocess_run(self.args, text)
        if p.returncode == 0:
            return {'type': 'block_html', 'raw': p.stdout}
        else:
            import shlex

            message = (
                f'<code>{escape(shlex.join(self.args))}</code> exited'
                f' with status <code>{p.returncode}</code>\n'
            )
            stdout = p.stdout.rstrip()
            if stdout:
                message += f'<pre>{escape(stdout)}</pre>\n'
            stderr = p.stderr.rstrip()
            if stderr:
                message += f'<pre>{escape(stderr)}</pre>\n'
            return {'type': 'block_error', 'raw': message}

    def __call__(self, md):
        self.register_directive(md, self.name)


@lru_cache
def subprocess_run(args, input):
    import subprocess

    return subprocess.run(args, input=input, capture_output=True, text=True)


def make_markdown(url_rewriters, load_literalinclude, render_snippet):
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
            LiteralInclude(load_literalinclude),
            Snippet(render_snippet),
            Subprocess('pikchr', ['pikchr', '--svg-only', '-']),
        ],
    )


if __name__ == '__main__':

    with open('tests/data/md/09-literalinclude.in') as f:
        text = f.read()

    def rewrite(url, text):
        return url.upper(), text or 'default'

    def load_lines(path):
        return [s + '\n' for s in ['one', 'two', 'three', 'four', 'five', '']]

    md = make_markdown([rewrite], load_lines)

    print(md(text))
