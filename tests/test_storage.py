import io

import pytest

from gen.storage import read_metadata


def test_read_metadata():

    f = io.StringIO("one\n---\ntwo\n")
    m = list(read_metadata(f))
    assert m == []
    assert f.read() == "one\n---\ntwo\n"

    f = io.StringIO("---\none\n---\ntwo\n")
    m = list(read_metadata(f))
    assert m == ['one\n']
    assert f.read() == "two\n"

    f = io.StringIO("---\none\n")
    with pytest.raises(ValueError):
        list(read_metadata(f))
    assert f.read() == "---\none\n"
