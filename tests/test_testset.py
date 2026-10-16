"""The committed tests/data/synthetic files must equal what `ailab synth testset` generates."""

import json
from pathlib import Path

from typer.testing import CliRunner

from ailab.cli import app
from ailab.testset import ID_PREFIX, build_cases, read_testset

DATA = Path(__file__).parent / "data" / "synthetic"


def test_committed_files_are_in_sync_with_generator():
    for c in build_cases():
        assert (DATA / f"{c.case_id}.json").read_bytes() == c.raw() + b"\n", c.case_id
    manifest = json.loads((DATA / "manifest.json").read_text())
    assert [e["case_id"] for e in manifest["cases"]] == [c.case_id for c in build_cases()]


def test_deterministic_and_unique_ids():
    a, b = build_cases(), build_cases()
    assert [c.raw() for c in a] == [c.raw() for c in b]
    ok = [c.series_id for c in a if c.expect == "accept"]
    assert len(ok) == len(set(ok)) and all(i.startswith(ID_PREFIX) for i in ok)


def test_read_testset_roundtrip():
    got = read_testset(DATA)
    assert [c.case_id for c in got] == [c.case_id for c in build_cases()]


def test_cli_synth_single_and_testset(tmp_path):
    r = CliRunner().invoke(
        app, ["synth", "--kind", "count", "--weeks", "30", "--out", str(tmp_path / "x.csv")]
    )
    assert r.exit_code == 0 and (tmp_path / "x.csv").exists()
    r = CliRunner().invoke(app, ["synth", "testset", "--out", str(tmp_path / "ts")])
    assert r.exit_code == 0 and (tmp_path / "ts" / "manifest.json").exists()
