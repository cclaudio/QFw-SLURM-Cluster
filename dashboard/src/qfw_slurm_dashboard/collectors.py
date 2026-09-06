"""Independent collectors for Docker, Slurm, and the QFw service plane."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
        raw_records = _json_objects(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        return _unavailable("docker", f"malformed Docker JSON: {error}")
    records = [{
        "kind": "container",
        "name": item.get("Name") or item.get("Names") or item.get("Service"),
        "service": item.get("Service", ""),
        "image": item.get("Image", ""),
        "state": item.get("State", ""),
        "status": item.get("Status", ""),
        "health": item.get("Health", ""),
    } for item in raw_records]
    running = all(str(item.get("state", "")).lower() == "running" for item in records)
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
    controller = runner.cluster("root", ("scontrol", "ping"))
    database = runner.cluster(
        "root", ("sacctmgr", "--noheader", "--parsable2", "show", "cluster")
    )
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
        {
            "kind": "controller", "name": "slurmctld",
            "state": "ready" if controller.returncode == 0 else "unavailable",
            "detail": (controller.stdout or controller.stderr).strip(),
        },
        {
            "kind": "database", "name": "slurmdbd",
            "state": "ready" if database.returncode == 0 else "unavailable",
            "detail": (database.stdout or database.stderr).strip(),
        },
    ] + [
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
    status = "ready" if controller.returncode == 0 and database.returncode == 0 \
        else "degraded"
    return SourceState("slurm", status, utc_now(), records)


def _qfw_json(
    runner: CommandRunner, name: str, record_key: str, argv: tuple[str, ...]
) -> SourceState:
    result = runner.cluster("root", argv, timeout=20)
    if result.returncode:
        message = result.stderr or result.stdout
        stopped_markers = ("not running", "not ready", "state not found")
        status = "stopped" if any(
            marker in message.lower() for marker in stopped_markers
        ) else "unavailable"
        return SourceState(name, status, utc_now(), error=message[:1000])
    try:
        envelopes = _json_objects(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        return _unavailable(name, f"malformed {name} JSON: {error}")
    if len(envelopes) != 1 or not isinstance(envelopes[0].get(record_key), list):
        return _unavailable(name, f"{name} JSON lacks {record_key} array")
    records = [item for item in envelopes[0][record_key] if isinstance(item, dict)]
    errors = envelopes[0].get("errors", [])
    return SourceState(
        name, "degraded" if errors else "ready", utc_now(), records,
        error="; ".join(str(item) for item in errors)[:1000],
    )


def service_status(runner: CommandRunner) -> SourceState:
    return _qfw_json(runner, "services", "services", (
        "bash", "-lc",
        "export QFW_SHARED_ROOT=/workspace/qfw-container-base; "
        "source /opt/openqse/qfw/bin/qfw-activate "
        "--venv /opt/openqse/qfw-venv >/dev/null && qfw-sinfo --json",
    ))


def allocation_status(runner: CommandRunner) -> SourceState:
    return _qfw_json(runner, "allocations", "jobs", (
        "bash", "-lc",
        "export QFW_SHARED_ROOT=/workspace/qfw-container-base; "
        "source /opt/openqse/qfw/bin/qfw-activate "
        "--venv /opt/openqse/qfw-venv >/dev/null && qfw-squeue --json",
    ))


def service_plane_status(runner: CommandRunner) -> SourceState:
    result = runner.cluster("root", ("qfw-site-services", "status"), timeout=12)
    output = f"{result.stdout}\n{result.stderr}"
    headings = (
        ("directory", "Directory service"),
        ("nwqsim", "NWQSim QPM"),
        ("iqm", "IQM QPM"),
        ("gateway", "QFw Slurm gateway"),
    )
    records: list[dict[str, Any]] = []
    lines = output.splitlines()
    for component, heading in headings:
        index = next(
            (position for position, line in enumerate(lines) if line.startswith(heading)),
            -1,
        )
        detail = ""
        if index >= 0:
            end = next((
                position for position in range(index + 1, len(lines))
                if any(lines[position].startswith(value) for _, value in headings)
            ), len(lines))
            detail = "\n".join(lines[index + 1:end])
        failed = result.returncode != 0 and (not detail or any(
            marker in detail.lower()
            for marker in ("not found", "not-ready", "traceback", "refused", "failed")
        ))
        records.append({
            "component": component,
            "state": "stopped" if failed else "ready",
            "detail": detail[-1000:],
        })
        if component == "nwqsim":
            dvm_ready = '"role": "prte-dvm"' in detail \
                and '"state": "ready"' in detail
            records.append({
                "component": "dvm", "state": "ready" if dvm_ready else "stopped",
                "detail": "NWQSim PRTE DVM",
            })
    ready = sum(item["state"] == "ready" for item in records)
    status = "ready" if ready == len(records) else "degraded" if ready else "stopped"
    return SourceState("service-plane", status, utc_now(), records)


def inventory_status(runner: CommandRunner) -> SourceState:
    records: list[dict[str, Any]] = []
    cluster_revision = runner.host(("git", "rev-parse", "HEAD"))
    electroboy_revision = runner.host((
        "git", "-C", "dashboard/external/electroboy", "rev-parse", "HEAD",
    ))
    records.extend((
        {
            "kind": "revision", "component": "QFw-SLURM-Cluster",
            "value": cluster_revision.stdout.strip() or "unknown",
        },
        {
            "kind": "revision", "component": "ElectroBoy",
            "value": electroboy_revision.stdout.strip() or "unknown",
        },
    ))
    images = runner.host(("docker", "compose", "images", "--format", "json"))
    if images.returncode == 0:
        try:
            for item in _json_objects(images.stdout):
                records.append({
                    "kind": "image",
                    "component": item.get("Service") or item.get("ContainerName"),
                    "value": item.get("ID") or item.get("Repository"),
                    "tag": item.get("Tag", ""),
                })
        except (TypeError, json.JSONDecodeError):
            pass
    commands = (
        ("Slurm", ("slurmctld", "-V")),
        ("MUNGE", ("munge", "--version")),
        ("Python", ("python3", "--version")),
        ("PRTE", ("prte", "--version")),
        ("libfabric", ("fi_info", "--version")),
    )
    for component, argv in commands:
        result = runner.cluster("root", argv, timeout=5)
        records.append({
            "kind": "version", "component": component,
            "value": (result.stdout or result.stderr).splitlines()[0]
            if (result.stdout or result.stderr).splitlines() else "unavailable",
        })
    for component, path in (
        ("QFw site", "/etc/openqse/qfw/site.yaml"),
        ("qfw-slurm", "/etc/qfw-slurm/plugin.conf"),
    ):
        result = runner.cluster("root", ("sha256sum", path), timeout=5)
        fingerprint = result.stdout.split()[0] if result.returncode == 0 else "unavailable"
        records.append({
            "kind": "configuration", "component": component,
            "path": path, "value": fingerprint,
        })
    ready = cluster_revision.returncode == 0 and electroboy_revision.returncode == 0
    return SourceState(
        "inventory", "ready" if ready else "degraded", utc_now(), records
    )


def diagnostics(runner: CommandRunner) -> SourceState:
    checks: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("munge", "slurmctld", ("bash", "-lc", "munge -n | unmunge")),
        ("clock", "slurmctld", ("date", "--iso-8601=ns")),
        ("cpu", "slurmctld", ("nproc",)),
        ("mounts", "slurmctld", ("findmnt", "--json", "-T", "/workspace")),
        ("modules", "slurmctld", ("bash", "-lc", "module -t avail 2>&1")),
        (
            "gateway-connectivity", "slurmctld",
            ("bash", "-lc", "timeout 2 bash -c '</dev/tcp/slurmctld/18095'"),
        ),
        (
            "credential-readiness", "iqm-head",
            (
                "bash", "-lc",
                "test -r /etc/openqse/qfw/device/qpu-users.json",
            ),
        ),
        (
            "directory-connection-record", "slurmctld",
            (
                "test", "-s",
                "/workspace/qfw-container-base/qfw-site-services/"
                "directory-service.json",
            ),
        ),
        (
            "dvm-uri", "nwqsim-head",
            (
                "test", "-s",
                "/var/lib/qfw-site-services/qpm/nwqsim/prte_dvm/dvm-uri",
            ),
        ),
        (
            "service-registration", "slurmctld",
            (
                "bash", "-lc",
                "export QFW_SHARED_ROOT=/workspace/qfw-container-base; "
                "source /opt/openqse/qfw/bin/qfw-activate "
                "--venv /opt/openqse/qfw-venv >/dev/null; "
                "qfw-sinfo --json",
            ),
        ),
    )
    records: list[dict[str, Any]] = []
    for name, container, argv in checks:
        try:
            result = runner.cluster(
                "root", argv, container=container,
                timeout=20 if name == "service-registration" else 5,
            )
            check_status = "ready" if result.returncode == 0 else "failed"
            output = (result.stdout or result.stderr)[-2000:]
        except Exception as error:
            check_status = "failed"
            output = str(error)[-2000:]
        records.append(
            {
                "check": name,
                "node": container,
                "status": check_status,
                "output": output,
            }
        )
    clock_values = []
    for container in ("c1", "nwqsim-head", "iqm-head"):
        try:
            result = runner.cluster("root", (
                "bash", "-lc",
                "printf 'clock='; date +%s%N; printf 'cpus='; nproc; "
                "test $(nproc) -eq 4; findmnt -n -T /workspace >/dev/null; "
                "munge -n | unmunge >/dev/null",
            ), container=container, timeout=5)
            output = (result.stdout or result.stderr)[-2000:]
            check_status = "ready" if result.returncode == 0 else "failed"
        except Exception as error:
            output = str(error)[-2000:]
            check_status = "failed"
        match = next((
            line.split("=", 1)[1] for line in output.splitlines()
            if line.startswith("clock=")
        ), "")
        if match.isdigit():
            clock_values.append(int(match))
        records.append({
            "check": "node-baseline", "node": container,
            "status": check_status,
            "output": output,
        })
    skew = max(clock_values) - min(clock_values) if len(clock_values) > 1 else 0
    records.append({
        "check": "clock-skew", "node": "cluster",
        "status": "ready" if len(clock_values) == 3 and skew < 5_000_000_000
        else "failed",
        "output": f"maximum skew {skew} ns",
    })
    status = "ready" if all(item["status"] == "ready" for item in records) else "degraded"
    return SourceState("diagnostics", status, utc_now(), records)


COLLECTORS: tuple[Callable[[CommandRunner], SourceState], ...] = (
    docker_status,
    slurm_status,
    service_status,
    allocation_status,
    service_plane_status,
    inventory_status,
)


def collect_all(runner: CommandRunner) -> list[SourceState]:
    def collect(collector: Callable[[CommandRunner], SourceState]) -> SourceState:
        try:
            return collector(runner)
        except Exception as error:
            name = {
                docker_status: "docker",
                slurm_status: "slurm",
                service_status: "services",
                allocation_status: "allocations",
                service_plane_status: "service-plane",
                inventory_status: "inventory",
            }[collector]
            return _unavailable(name, str(error))

    with ThreadPoolExecutor(max_workers=len(COLLECTORS)) as pool:
        return list(pool.map(collect, COLLECTORS))
