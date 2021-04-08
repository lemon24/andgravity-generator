import os.path
from pathlib import Path

from click.testing import CliRunner

from gen.cli import cli

ROOT = Path(__file__).parent


def walk(path):
    for root, dirs, files in os.walk(path):
        for file in files:
            yield os.path.relpath(os.path.join(root, file), path)


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
    assert result.exit_code == 0

    expected_files = set(walk(expected_dir))
    output_files = set(walk(output_dir))

    assert expected_files == output_files

    for file in expected_files:
        with subtests.test(file):
            with expected_dir.joinpath(file).open() as f:
                expected = f.read()
            with output_dir.joinpath(file).open() as f:
                output = f.read()
            assert expected == output, file
