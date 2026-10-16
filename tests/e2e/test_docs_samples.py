"""Every fenced code block tagged ```bash verify / ```python verify in README*.md and docs/*.md
is EXECUTED against the running service (env AILAB_URL, AILAB_API_KEY; the venv's `ailab` on PATH).

bash blocks of one file run together as one script (variables persist, like in a terminal);
python blocks run afterwards, each in its own interpreter. Blocks tagged plain ```bash / ```python
(docker run, installs, …) are not executed here; see docs/TESTING.md for how they are verified."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from .harness import KEY, Api, Instance

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted([*ROOT.glob("README*.md"), *(ROOT / "docs").glob("*.md")])
FENCE = re.compile(r"^```(bash|sh|python) verify[^\n]*\n(.*?)^```", re.S | re.M)


def blocks(path: Path):
    return [(m.group(1), m.group(2)) for m in FENCE.finditer(path.read_text())]


CASES = [(p, blocks(p)) for p in FILES if blocks(p)]


@pytest.fixture(scope="module")
def dsvc(workdir):
    """Own instance: the tutorial loads and deletes `ts_*` series, which would disturb other tests."""
    inst = Instance("docs", {"AILAB_API_KEY": KEY, "AILAB_PRELOAD_DEMO": "1"}, workdir)
    yield inst
    inst.close()


@pytest.fixture
def clean(dsvc):
    with Api(dsvc.url) as api:
        yield from _clean(api)


def _clean(api):
    def wipe():
        for m in api.get("/api/v1/series", params={"limit": 200}).json()["items"]:
            if m["series_id"].startswith(("tut_", "doc_")):
                api.w("DELETE", f"/api/v1/series/{m['series_id']}", params={"cascade": "true"})
        for r in api.get("/api/v1/alerts/rules").json():
            if r["rule_id"].startswith(("tut_", "doc_")):
                api.w("DELETE", f"/api/v1/alerts/rules/{r['rule_id']}")

    wipe()
    yield
    wipe()


def test_there_are_verified_samples():
    assert sum(len(b) for _, b in CASES) >= 12, "docs lost their verified code samples"


@pytest.mark.parametrize("path,blks", CASES, ids=[p.name for p, _ in CASES])
def test_doc_samples_run(dsvc, clean, path, blks):
    env = {
        **os.environ,
        "AILAB_URL": dsvc.url,
        "AILAB_API_KEY": KEY,
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    }
    sh = "\n".join(c for lang, c in blks if lang in ("bash", "sh"))
    if sh:
        r = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", sh],
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=ROOT,
        )
        assert r.returncode == 0, (
            f"{path.name} bash samples failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
        )
    for i, (_lang, code) in enumerate(b for b in blks if b[0] == "python"):
        r = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=ROOT,
        )
        assert r.returncode == 0, f"{path.name} python sample #{i} failed:\n{r.stderr[-2000:]}"
