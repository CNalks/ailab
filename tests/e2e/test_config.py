"""Configuration errors, container hardening (non-root, read-only fs) and logging."""

import pytest

from .harness import KEY, TARGET, run_to_exit


@pytest.mark.parametrize("key", ["short", "a" * 15, "x"])
def test_short_api_key_refuses_to_start(workdir, key):
    code, out = run_to_exit({"AILAB_API_KEY": key}, workdir)
    assert code != 0 and "at least 16" in out
    assert key not in out.replace("at least 16", "") or len(key) < 4


@pytest.mark.parametrize(
    "env",
    [
        {"AILAB_MAX_BODY_BYTES": "abc"},
        {"AILAB_MAX_BODY_BYTES": "5"},
        {"AILAB_MAX_SERIES": "0"},
        {"AILAB_MAX_OBSERVATIONS": "99999999"},
    ],
)
def test_bad_numeric_config_refuses_to_start(workdir, env):
    code, out = run_to_exit({"AILAB_API_KEY": KEY, **env}, workdir)
    assert code != 0 and "AILAB_" in out


def test_16_char_key_is_accepted_by_settings():
    from ailab.api.settings import Settings

    assert Settings(api_key="x" * 16).api_key == "x" * 16


@pytest.mark.skipif(TARGET != "container", reason="container only")
class TestContainer:
    def test_runs_as_non_root(self, svc):
        r = svc.exec("id", "-u")
        assert r.stdout.strip() == "10001"

    def test_root_filesystem_is_read_only(self, svc):
        r = svc.exec("sh", "-c", "touch /app-test 2>&1; echo rc=$?")
        assert "rc=0" not in r.stdout
        r = svc.exec("sh", "-c", "touch /tmp/ok && echo tmp-writable")
        assert "tmp-writable" in r.stdout
        r = svc.exec("sh", "-c", "touch /data/ok && echo data-writable")
        assert "data-writable" in r.stdout

    def test_no_capabilities(self, svc):
        r = svc.exec("sh", "-c", "grep CapEff /proc/1/status")
        assert r.stdout.split()[-1].strip("0") == ""

    def test_healthcheck_command_works(self, svc):
        r = svc.exec(
            "python",
            "-c",
            "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)",
        )
        assert r.returncode == 0

    def test_cli_inside_image(self, svc):
        r = svc.exec("ailab", "doctor")
        assert r.returncode == 0 and "FAIL" not in r.stdout
