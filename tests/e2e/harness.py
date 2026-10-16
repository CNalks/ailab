"""E2E harness: start/stop a REAL service (``ailab serve`` subprocess or a container built from
the Dockerfile) and talk to it over HTTP. Nothing here imports the app (no TestClient)."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

TARGET = os.environ.get("AILAB_E2E_TARGET", "local")  # local | container
ENGINE = os.environ.get("AILAB_E2E_ENGINE") or ("podman" if shutil.which("podman") else "docker")
IMAGE = os.environ.get("AILAB_E2E_IMAGE", "ailab:local")
KEY = "e2e-key-0123456789abcdef"

# route templates (for the endpoint x scenario coverage table)
_ROUTES = [
    (
        r"^/api/v1/series/[^/]+/(data|observations|quality|lineage|forecast|policy|alerts|export"
        r"|recompute)$",
        "/api/v1/series/{id}/\\1",
    ),
    (r"^/api/v1/series/[^/]+$", "/api/v1/series/{id}"),
    (r"^/api/v1/alerts/rules/[^/]+$", "/api/v1/alerts/rules/{rule_id}"),
]


def route_of(method: str, path: str) -> str:
    path = path.split("?")[0]
    for pat, rep in _ROUTES:
        if re.match(pat, path):
            path = re.sub(pat, rep, path)
            break
    return f"{method} {path}"


@dataclass
class Recorder:
    current: str = ""
    hits: list = field(default_factory=list)  # (nodeid, route, status)
    timings: dict = field(default_factory=dict)  # label -> seconds (or dict)
    notes: dict = field(default_factory=dict)


REC = Recorder()


class Api(httpx.Client):
    """httpx client that records which endpoint each test touched."""

    def __init__(self, base_url: str, key: str | None = KEY, **kw):
        kw.setdefault("timeout", 600.0)
        super().__init__(base_url=base_url, **kw)
        self.key = key
        self.hdr = {"X-API-Key": key} if key else {}

    def request(self, method, url, **kw):  # type: ignore[override]
        r = super().request(method, url, **kw)
        REC.hits.append(
            (REC.current, route_of(method.upper(), httpx.URL(str(url)).path), r.status_code)
        )
        return r

    def w(self, method, url, **kw):
        """Request with the write key."""
        h = {**self.hdr, **kw.pop("headers", {})}
        return self.request(method, url, headers=h, **kw)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Instance:
    """One running service (local process or container) that can be stopped/restarted."""

    def __init__(self, name: str, env: dict[str, str], workdir: Path, persistent: bool = True):
        self.name = f"ailab-e2e-{name}-{uuid.uuid4().hex[:6]}"
        self.env = env
        self.workdir = workdir / name
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None
        self.logfile = self.workdir / f"{name}.log"
        self.volumes: list[str] = []
        self.start()

    # ---- lifecycle ---------------------------------------------------------------------
    def start(self) -> None:
        if TARGET == "container":
            self._start_container()
        else:
            self._start_local()
        self.wait_ready()

    def _start_local(self) -> None:
        env = {
            **os.environ,
            "AILAB_DB": str(self.workdir / "db.duckdb"),
            "AILAB_OUT": str(self.workdir / "out"),
            "NUMBA_CACHE_DIR": "/tmp/numba",
            **self.env,
        }
        for k in [k for k, v in env.items() if v is None]:
            del env[k]
        log = open(self.logfile, "ab")  # noqa: SIM115
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ailab.cli",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    def _start_container(self) -> None:
        if not self.volumes:
            self.volumes = [f"{self.name}-data", f"{self.name}-out"]
        if self._engine("container", "inspect", self.name).returncode == 0:
            self._engine("start", self.name)
            return
        cmd = [
            ENGINE,
            "run",
            "-d",
            "--name",
            self.name,
            "-p",
            f"127.0.0.1:{self.port}:8000",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "-v",
            f"{self.volumes[0]}:/data",
            "-v",
            f"{self.volumes[1]}:/out",
        ]
        for k, v in self.env.items():
            if v is not None:
                cmd += ["-e", f"{k}={v}"]
        cmd.append(IMAGE)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"container start failed: {r.stderr}")

    def _engine(self, *args: str, **kw):
        return subprocess.run([ENGINE, *args], capture_output=True, text=True, **kw)

    def wait_ready(self, timeout: float = 90.0) -> None:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if not self.running():
                raise RuntimeError(f"{self.name} exited:\n{self.logs()[-2000:]}")
            try:
                if httpx.get(self.url + "/ready", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError(f"{self.name} not ready after {timeout}s:\n{self.logs()[-2000:]}")

    def running(self) -> bool:
        if TARGET == "container":
            r = self._engine("inspect", "-f", "{{.State.Running}}", self.name)
            return r.stdout.strip() == "true"
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if TARGET == "container":
            self._engine("stop", "-t", "10", self.name)
        elif self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def kill(self) -> None:
        """SIGKILL (simulates a crash: no graceful shutdown)."""
        if TARGET == "container":
            self._engine("kill", self.name)
        elif self.proc:
            self.proc.kill()
            self.proc.wait(20)

    def restart(self, hard: bool = False) -> None:
        self.kill() if hard else self.stop()
        self.start()

    def recreate(self) -> None:
        """Container only: remove the container and run a new one on the same volumes."""
        self.stop()
        if TARGET == "container":
            self._engine("rm", "-f", self.name)
        self.start()

    def logs(self) -> str:
        if TARGET == "container":
            r = self._engine("logs", self.name)
            return r.stdout + r.stderr
        return self.logfile.read_text(errors="replace") if self.logfile.exists() else ""

    def exec(self, *cmd: str) -> subprocess.CompletedProcess:
        assert TARGET == "container"
        return self._engine("exec", self.name, *cmd)

    def close(self) -> None:
        try:
            self.stop()
        finally:
            if TARGET == "container":
                self._engine("rm", "-f", self.name)
                for v in self.volumes:
                    self._engine("volume", "rm", "-f", v)


def run_to_exit(env: dict[str, str], workdir: Path, timeout: float = 60.0) -> tuple[int, str]:
    """Start the service with ``env`` and expect it to exit by itself (config errors)."""
    if TARGET == "container":
        cmd = [ENGINE, "run", "--rm", "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL"]
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd.append(IMAGE)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    e = {**os.environ, "AILAB_DB": str(workdir / "x.duckdb"), **env}
    r = subprocess.run(
        [sys.executable, "-m", "ailab.cli", "serve", "--port", str(free_port())],
        env=e,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return r.returncode, r.stdout + r.stderr


def dumps(o) -> str:
    return json.dumps(o)
