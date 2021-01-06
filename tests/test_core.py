import io

import pytest
import yaml

from gen.core import load_metadata


def test_load_metadata():

    f = io.StringIO("one\n---\ntwo\n")
    m = load_metadata(f)
    assert m is None
    assert f.read() == "one\n---\ntwo\n"

    f = io.StringIO("---\none\n---\ntwo\n")
    m = load_metadata(f)
    assert m == 'one'
    assert f.read() == "two\n"

    f = io.StringIO("---\n[one\n---\ntwo\n")
    with pytest.raises(yaml.YAMLError):
        load_metadata(f)
    assert f.read() == "---\n[one\n---\ntwo\n"
    
    
