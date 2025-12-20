import os.path
import subprocess
from glob import glob
from textwrap import dedent

import pytest

from gen.markdown import make_markdown


ROOT = os.path.dirname(__file__)


DATA_FILES = [
    (path, os.path.splitext(path)[0] + '.out')
    for path in sorted(glob(os.path.join(ROOT, 'data/md/*.in')))
]


def load_lines(path):
    return [s + '\n' for s in ['one', 'two', 'three', 'four', 'five', '']]


@pytest.fixture(scope="module", params=DATA_FILES, ids=lambda t: os.path.basename(t[0]))
def md_html(request):
    md, html = request.param
    with open(md) as mdf, open(html) as htmlf:
        return mdf.read(), htmlf.read()


ECHO_STDIN = """\
#!/bin/bash
echo "$@"
cat
"""


@pytest.fixture()
def pikchr(tmp_path, monkeypatch):
    bin = tmp_path / "bin"
    bin.mkdir()

    pikchr = bin / "pikchr"
    pikchr.write_text(ECHO_STDIN)
    pikchr.chmod(0o700)

    original_run = subprocess.run

    def run(*args, **kwargs):
        return original_run(
            *args, env={'PATH': f"{bin}:{os.environ['PATH']}"}, **kwargs
        )

    monkeypatch.setattr(subprocess, 'run', run)


def build_node_url(url, text):
    if url.startswith('node:'):
        return url.upper(), text or 'default'
    return None


def build_file_url(url, text):
    if url.startswith('attachment:'):
        return url.upper(), text or 'default'
    return None


def build_type_url(url, text):
    if url.startswith('type:'):
        return url.upper(), text or 'default', 'type'
    return None


def render_snippet(value, text, options):
    return f"snippet: {value}\noptions: {options}\ntext: {dedent(text)!r}"


def test_parts(md_html, pikchr):
    md, expected = md_html
    actual = make_markdown(
        [build_node_url, build_file_url, build_type_url], load_lines, render_snippet
    )(md)
    assert actual.rstrip() == expected.rstrip()
