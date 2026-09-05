"""Allow-listed host and selected-identity cluster command execution."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Sequence

IDENTITIES = ("user-a", "user-b", "user-c", "root")
REGULAR_IDENTITIES = IDENTITIES[:3]


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    def __init__(self, cluster_root: Path, timeout: float = 30.0) -> None:
        self.cluster_root = cluster_root.resolve()
        self.timeout = timeout

    def host(self, argv: Sequence[str], timeout: float | None = None) -> CommandResult:
        completed = subprocess.run(
            list(argv),
            cwd=self.cluster_root,
            text=True,
            capture_output=True,
            timeout=timeout or self.timeout,
            check=False,
            env=os.environ.copy(),
        )
        return CommandResult(
            tuple(argv), completed.returncode, completed.stdout, completed.stderr
        )

    def cluster(
        self,
        identity: str,
        argv: Sequence[str],
        *,
        container: str = "slurmctld",
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        docker_argv = self.cluster_argv(identity, argv, container=container, cwd=cwd)
        return self.host(docker_argv, timeout=timeout)

    def cluster_argv(
        self,
        identity: str,
        argv: Sequence[str],
        *,
        container: str = "slurmctld",
        cwd: str | None = None,
    ) -> tuple[str, ...]:
        if identity not in IDENTITIES:
            raise ValueError(f"unsupported cluster identity: {identity}")
        home = "/root" if identity == "root" else f"/workspace/home/{identity}"
        workdir = cwd or home
        command = shlex.join(tuple(str(item) for item in argv))
        return (
            "docker",
            "exec",
            "--user",
            identity,
            "--workdir",
            workdir,
            "--env",
            f"HOME={home}",
            "--env",
            f"USER={identity}",
            "--env",
            f"LOGNAME={identity}",
            container,
            "/bin/bash",
            "-lc",
            command,
        )

    def stream_host(
        self, argv: Sequence[str], on_line: Callable[[str], None]
    ) -> CommandResult:
        process = subprocess.Popen(
            list(argv),
            cwd=self.cluster_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )
        output: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            value = line.rstrip("\n")
            output.append(value)
            on_line(value)
        returncode = process.wait()
        return CommandResult(tuple(argv), returncode, "\n".join(output), "")
