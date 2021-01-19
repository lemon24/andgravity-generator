import os.path
from glob import glob

import mistune
import pytest

from gen.markdown import make_markdown


ROOT = os.path.dirname(__file__)


DATA_FILES = [
    (path, os.path.splitext(path)[0] + '.out')
    for path in sorted(glob(os.path.join(ROOT, 'data/md/*.in')))
]


@pytest.fixture(scope="module", params=DATA_FILES, ids=lambda t: os.path.basename(t[0]))
def md_html(request):
    md, html = request.param
    with open(md) as mdf, open(html) as htmlf:
        return mdf.read(), htmlf.read()


def build_file_url(url):
    if url.startswith('attachment:'):
        return url.upper()
    return None


def test_parts(md_html):
    md, html = md_html
    assert make_markdown(str.upper, build_file_url)(md) == html
