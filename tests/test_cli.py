import os.path
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from gen.cli import cli

ROOT = Path(__file__).parent


def walk(path):
    for root, dirs, files in os.walk(path):
        for file in files:
            yield os.path.relpath(os.path.join(root, file), path)

def clean_trailing_whitespace(text):
    return ''.join(l.rstrip() + '\n' for l in text.rstrip().splitlines())


def test_freeze(tmp_path, subtests):
    input_dir = ROOT.joinpath('data/integration/in')
    expected_dir = ROOT.joinpath('data/integration/out')
    output_dir = tmp_path.joinpath('out')

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ['--project', str(input_dir), 'freeze', str(output_dir)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    expected_files = set(walk(expected_dir))
    output_files = set(walk(output_dir))

    assert expected_files == output_files, result.output

    for file in expected_files:
        with subtests.test(file):
            with expected_dir.joinpath(file).open() as f:
                expected = clean_trailing_whitespace(f.read())
            with output_dir.joinpath(file).open() as f:
                output = clean_trailing_whitespace(f.read())
            assert expected == output, file


BROKEN_LINKS_YAML = """\
one:
  internal-links:
    /inexistent-node: node not found
    /two#a-name-error: fragment not found
    /two#header-error: fragment not found
    /two#id-error: fragment not found
  markdown:
  - 'Unsupported directive: unknown-directive'
  - 'could not render snippet ''unknown-snippet'': TemplateNotFound: snippets/unknown-snippet.html'
"""

# including the error messages is a bit brittle, but eh...

@pytest.mark.filterwarnings('ignore:Nothing frozen')
def test_freeze_checks(tmp_path, subtests):
    input_dir = ROOT.joinpath('data/integration-checks/in')
    output_dir = tmp_path.joinpath('out')

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ['--project', str(input_dir), 'freeze', str(output_dir)],
        catch_exceptions=False,
    )
    assert result.exit_code == 1, result.output
    assert BROKEN_LINKS_YAML in result.output, result.output

    one_html = output_dir.joinpath('one.html').read_text()
    one_errors = yaml.safe_load(BROKEN_LINKS_YAML)['one']

    for error in one_errors['markdown']:
        assert error in one_html, error
