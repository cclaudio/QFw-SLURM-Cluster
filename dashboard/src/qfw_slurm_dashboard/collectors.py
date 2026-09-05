"""Independent collectors for Docker, Slurm, and the QFw service plane."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .models import SourceState, utc_now
from .runner import CommandResult, CommandRunner


def _unavailable(name: str, error: str) -> SourceState:
    return SourceState(name, "unavailable", utc_now(), error=error[:1000])


def _json_objects(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return []
    try:
        value = json.loads(stripped)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
    except json.JSONDecodeError:
        pass
    records: list[dict[str, Any]] = []
    for line in stripped.splitlines():
        value = json.loads(line)
        if isinstance(value, dict):
            records.append(value)
    return records


def docker_status(runner: CommandRunner) -> SourceState:
    result = runner.host(("docker", "compose", "ps", "--format", "json"))
    if result.returncode:
        return _unavailable("docker", result.stderr or result.stdout)
    try:
        records = _json_objects(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        return _unavailable("docker", f"malformed Docker JSON: {error}")
    running = all(str(item.get("State", "")).lower() == "running" for item in records)
    status = "ready" if records and running else "stopped" if not records else "degraded"
    return SourceState("docker", status, utc_now(), records)


def _pipe_records(result: CommandResult, fields: tuple[str, ...]) -> list[dict[str, str]]:
    records = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        values = line.split("|")
        values += [""] * (len(fields) - len(values))
        records.append(dict(zip(fields, values, strict=False)))
    return records


def slurm_status(runner: CommandRunner) -> SourceState:
    nodes = runner.cluster(
        "root",
        ("sinfo", "--noheader", "--Node", "--format=%N|%P|%T|%E|%c|%m|%f"),
    )
    if nodes.returncode:
        return _unavailable("slurm", nodes.stderr or nodes.stdout)
    jobs = runner.cluster(
        "root",
        ("squeue", "--noheader", "--format=%i|%u|%T|%P|%N|%M|%R"),
    )
    if jobs.returncode:
        return _unavailable("slurm", jobs.stderr or jobs.stdout)
    records: list[dict[str, Any]] = [
        {"kind": "node", **item}
        for item in _pipe_records(
            nodes,
            ("node", "partition", "state", "reason", "cpus", "memory", "features"),
        )
    ]
    records.extend(
        {"kind": "job", **item}
        for item in _pipe_records(
            jobs,
            ("job_id", "user", "state", "partition", "nodes", "elapsed", "reason"),
        )
    )
    return SourceState("slurm", "ready", utc_now(), records)


def _qfw_json(runner: CommandRunner, name: str, argv: tuple[str, ...]) -> SourceState:
    result = runner.cluster("root", argv)
    if result.returncode:
        message = result.stderr or result.stdout
        status = "stopped" if "not running" in message.lower() else "unavailable"
        return SourceState(name, status, utc_now(), error=message[:1000])
    try:
        records = _json_objects(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        return _unavailable(name, f"malformed {name} JSON: {error}")
    return SourceState(name, "ready", utc_now(), records)


def service_status(runner: CommandRunner) -> SourceState:
    return _qfw_json(runner, "services", ("qfw-sinfo", "--json"))


def allocation_status(runner: CommandRunner) -> SourceState:
    return _qfw_json(runner, "allocations", ("qfw-squeue", "--json"))


def diagnostics(runner: CommandRunner) -> SourceState:
    checks: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("munge", ("munge", "-n")),
        ("clock", ("date", "--iso-8601=ns")),
        ("cpu", ("nproc",)),
        ("mounts", ("findmnt", "--json", "/workspace")),
        ("modules", ("bash", "-lc", "module -t avail 2>&1")),
    )
    records: list[dict[str, Any]] = []
    for name, argv in checks:
        result = runner.cluster("root", argv)
        records.append(
            {
                "check": name,
                "status": "ready" if result.returncode == 0 else "failed",
                "output": (result.stdout or result.stderr)[-2000:],
            }
        )
    status = "ready" if all(item["status"] == "ready" for item in records) else "degraded"
    return SourceState("diagnostics", status, utc_now(), records)


COLLECTORS: tuple[Callable[[CommandRunner], SourceState], ...] = (
    docker_status,
    slurm_status,
    service_status,
    allocation_status,
)


def collect_all(runner: CommandRunner) -> list[SourceState]:
    sources: list[SourceState] = []
    for collector in COLLECTORS:
        try:
            sources.append(collector(runner))
        except Exception as error:
            sources.append(_unavailable(collector.__name__, str(error)))
    return sources
