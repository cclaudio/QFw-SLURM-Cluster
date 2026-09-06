"""Dashboard capabilities independent of browser rendering."""

from __future__ import annotations

import json
import base64
import re
import shlex
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .collectors import (
    collect_all,
    diagnostics,
    inventory_status,
    service_plane_status,
)
from .models import Experiment, Operation, aggregate_state, utc_now
from .logs import LogSource, SOURCES, read_source
from .runner import CommandRunner, IDENTITIES
from .store import DashboardStore

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
EXAMPLES = {
    "init-test",
    "qiskit-simple",
    "ghz-qiskit",
    "ghz-pennylane",
    "pennylane",
    "qaoa",
    "qiskit-vqe",
    "supermarq",
    "chemistry",
}
EXAMPLE_SCRIPTS = {
    "init-test": "qfw_init_test.sh",
    "qiskit-simple": "qfw_qiskit_simple.sh",
    "ghz-qiskit": "qfw_ghz.sh",
    "ghz-pennylane": "qfw_ghz.sh",
    "pennylane": "qfw_pennylane.sh",
    "qaoa": "qfw_qaoa.sh",
    "qiskit-vqe": "qfw_qiskit_vqe.sh",
    "supermarq": "qfw_supermarq.sh",
    "chemistry": "qfw_chem_app.sh",
}


class DashboardService:
    _ACTIVATE = (
        "export QFW_SHARED_ROOT=/workspace/qfw-container-base; "
        "export QFW_SITE_CONFIG=/etc/openqse/qfw/site.yaml; "
        "source /opt/openqse/qfw/bin/qfw-activate "
        "--venv /opt/openqse/qfw-venv >/dev/null; "
    )
    HOST_ACTIONS = {
        "cluster-build": ("./do_build.sh",),
        "cluster-start": ("./do_startup.sh",),
        "cluster-stop": ("./do_stop.sh",),
        "cluster-restart": ("./do_restart.sh",),
        "cluster-recreate": (
            "/bin/bash", "-lc",
            "./do_stop.sh delete && ./do_build.sh && ./do_startup.sh",
        ),
    }
    SERVICE_ACTIONS: dict[str, tuple[tuple[str, ...], str]] = {
        "services-start": (("qfw-site-services", "start"), "slurmctld"),
        "services-stop": (("qfw-site-services", "stop"), "slurmctld"),
        "services-restart": ((
            "bash", "-lc",
            "qfw-site-services stop; qfw-site-services start",
        ), "slurmctld"),
        "services-status": (("qfw-site-services", "status"), "slurmctld"),
        "directory-start": ((
            "bash", "-lc", _ACTIVATE
            + "qfw-dir-svc start --scope site "
            "--run-dir /var/lib/qfw-site-services/directory "
            "--site-config /etc/openqse/qfw/site.yaml --timeout 300",
        ), "slurmctld"),
        "directory-stop": ((
            "bash", "-lc", _ACTIVATE
            + "qfw-dir-svc stop "
            "--run-dir /var/lib/qfw-site-services/directory",
        ), "slurmctld"),
        "directory-restart": ((
            "bash", "-lc", _ACTIVATE
            + "qfw-dir-svc stop "
            "--run-dir /var/lib/qfw-site-services/directory; "
            "qfw-dir-svc start --scope site "
            "--run-dir /var/lib/qfw-site-services/directory "
            "--site-config /etc/openqse/qfw/site.yaml --timeout 300",
        ), "slurmctld"),
        "nwqsim-start": ((
            "bash", "-lc", _ACTIVATE
            + "export QFW_SIMULATOR_NODES="
            "nwqsim-head,nwqsim-worker-1,nwqsim-worker-2; "
            "qfw-qpm-svc start --scope site "
            "--run-dir /var/lib/qfw-site-services/qpm/nwqsim "
            "--site-config /etc/openqse/qfw/site.yaml "
            "--service-id nwqsim --timeout 300",
        ), "nwqsim-head"),
        "nwqsim-stop": ((
            "bash", "-lc", _ACTIVATE
            + "qfw-qpm-svc stop "
            "--run-dir /var/lib/qfw-site-services/qpm/nwqsim",
        ), "nwqsim-head"),
        "nwqsim-restart": ((
            "bash", "-lc", _ACTIVATE
            + "qfw-qpm-svc stop "
            "--run-dir /var/lib/qfw-site-services/qpm/nwqsim; "
            "export QFW_SIMULATOR_NODES="
            "nwqsim-head,nwqsim-worker-1,nwqsim-worker-2; "
            "qfw-qpm-svc start --scope site "
            "--run-dir /var/lib/qfw-site-services/qpm/nwqsim "
            "--site-config /etc/openqse/qfw/site.yaml "
            "--service-id nwqsim --timeout 300",
        ), "nwqsim-head"),
        "iqm-start": ((
            "bash", "-lc", _ACTIVATE
            + "qfw-qpm-svc start --scope site "
            "--run-dir /var/lib/qfw-site-services/qpm/iqm-ornl-20q "
            "--site-config /etc/openqse/qfw/site.yaml "
            "--service-id iqm-ornl-20q --timeout 300",
        ), "iqm-head"),
        "iqm-stop": ((
            "bash", "-lc", _ACTIVATE
            + "qfw-qpm-svc stop "
            "--run-dir /var/lib/qfw-site-services/qpm/iqm-ornl-20q",
        ), "iqm-head"),
        "iqm-restart": ((
            "bash", "-lc", _ACTIVATE
            + "qfw-qpm-svc stop "
            "--run-dir /var/lib/qfw-site-services/qpm/iqm-ornl-20q; "
            "qfw-qpm-svc start --scope site "
            "--run-dir /var/lib/qfw-site-services/qpm/iqm-ornl-20q "
            "--site-config /etc/openqse/qfw/site.yaml "
            "--service-id iqm-ornl-20q --timeout 300",
        ), "iqm-head"),
        "gateway-restart": ((
            "bash", "-lc",
            "pid=$(pgrep -f '^/opt/openqse/qfw/bin/defwp -m "
            "qfw_slurm_gateway ' | head -n 1); "
            "test -n \"${pid}\"; kill -KILL \"${pid}\"; "
            "for i in $(seq 1 60); do "
            "timeout 1 bash -c '</dev/tcp/slurmctld/18095' && exit 0; "
            "sleep 1; done; exit 1",
        ), "slurmctld"),
    }

    def __init__(self, cluster_root: Path, state_root: Path) -> None:
        self.cluster_root = cluster_root.resolve()
        self.runner = CommandRunner(self.cluster_root)
        self.store = DashboardStore(state_root / "qfw-slurm-cluster")
        self._threads: dict[str, threading.Thread] = {}
        self._state_lock = threading.Lock()
        self._state_cache: dict[str, Any] | None = None
        self._state_cached_at = 0.0
        self._examples_cache: list[dict[str, Any]] | None = None

    def state(self) -> dict[str, Any]:
        with self._state_lock:
            now = time.monotonic()
            if self._state_cache is not None and now - self._state_cached_at < 2:
                return self._state_cache
            self._refresh_experiments()
            payload = aggregate_state(collect_all(self.runner))
            payload["operations"] = self.store.operations()
            payload["experiments"] = self.store.experiments()
            payload["identities"] = list(IDENTITIES)
            payload["examples"] = self.examples()
            self._state_cache = payload
            self._state_cached_at = time.monotonic()
            return payload

    def examples(self) -> list[dict[str, Any]]:
        if self._examples_cache is not None:
            return self._examples_cache
        directory = "/opt/openqse/qfw/share/qfw/examples"
        records: list[dict[str, Any]] = []
        for name, script in EXAMPLE_SCRIPTS.items():
            result = self.runner.cluster(
                "root", ("test", "-x", f"{directory}/{script}"), timeout=3
            )
            if result.returncode == 0:
                records.append({
                    "name": name,
                    "script": script,
                    "backends": ["nwqsim"] if name == "qiskit-vqe"
                    else ["nwqsim", "iqm"],
                    "hardware_risk": name != "init-test",
                })
        self._examples_cache = records
        return records

    def _refresh_experiments(self) -> None:
        terminal = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL"}
        for stored in self.store.experiments():
            if stored.get("completed_at") or not stored.get("slurm_job_id"):
                continue
            fields = {
                key: value for key, value in stored.items()
                if key in Experiment.__dataclass_fields__
            }
            experiment = Experiment(**fields)
            previous_status = experiment.status
            status = self.runner.cluster(
                experiment.identity,
                (
                    "sacct", "--noheader", "--parsable2",
                    "--jobs", experiment.slurm_job_id,
                    "--format=JobIDRaw,State,ExitCode,NodeList",
                ),
            )
            if status.returncode:
                continue
            first = next((line for line in status.stdout.splitlines() if line), "")
            values = first.split("|")
            slurm_records = []
            for line in status.stdout.splitlines():
                item = line.split("|")
                if not item or not item[0]:
                    continue
                item += [""] * (4 - len(item))
                slurm_records.append({
                    "job_id": item[0], "state": item[1].split()[0],
                    "exit_code": item[2], "nodes": item[3],
                    "kind": "step" if "." in item[0] else "job",
                })
            slurm_state = values[1].split()[0].upper() if len(values) > 1 else ""
            if slurm_state and slurm_state not in terminal:
                experiment.status = slurm_state.lower()
                if experiment.status != previous_status:
                    experiment.timeline.append({
                        "timestamp": utc_now(), "phase": experiment.status,
                        "component": "slurm", "job_id": experiment.slurm_job_id,
                    })
                self.store.save_experiment(experiment)
                if experiment.status != previous_status:
                    self.store.append_event({
                        "kind": "progress", "component": "slurm",
                        "identity": experiment.identity,
                        "experiment_id": experiment.experiment_id,
                        "job_id": experiment.slurm_job_id,
                        "severity": "info", "message": experiment.status,
                    })
                continue
            if slurm_state in terminal:
                artifact = experiment.artifacts[0] if experiment.artifacts else ""
                output = self.runner.cluster(
                    experiment.identity,
                    ("tail", "-n", "1000", artifact),
                ) if artifact else None
                output_text = output.stdout if output else ""
                records: list[dict[str, Any]] = []
                match = re.search(
                    r"^Summary JSONL: (.+)$", output_text, re.MULTILINE
                )
                if match:
                    summary_path = match.group(1).strip()
                    summary = self.runner.cluster(
                        experiment.identity, ("cat", summary_path)
                    )
                    if summary.returncode == 0:
                        for line in summary.stdout.splitlines():
                            try:
                                record = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(record, dict):
                                records.append(record)
                        experiment.artifacts.append(summary_path)
                for log_path in re.findall(r"\blog=([^\s]+)", output_text):
                    if log_path in experiment.artifacts:
                        continue
                    case_log = self.runner.cluster(
                        experiment.identity, ("tail", "-c", "262144", log_path)
                    )
                    if case_log.returncode != 0:
                        continue
                    experiment.artifacts.append(log_path)
                    driver_records = self._tagged_json(
                        case_log.stdout, "QFW_SLURM_DRIVER_RESULT"
                    )
                    records.extend(driver_records)
                    for record in driver_records:
                        reservation_value = record.get("reservation_id")
                        service_value = record.get("backend")
                        if reservation_value is not None and service_value:
                            reservation = str(reservation_value)
                            service = str(service_value)
                            pair = [service, reservation]
                            if pair not in experiment.reservations:
                                experiment.reservations.append(pair)
                    if not experiment.reservations:
                        reservation_match = re.search(
                            r"\breservation_id\s*[=:]\s*([0-9]+)",
                            case_log.stdout,
                        )
                        if reservation_match:
                            experiment.reservations.append([
                                experiment.backend,
                                reservation_match.group(1),
                            ])
                terminal_success = any(
                    record.get("kind") == "wrapper"
                    and record.get("event") == "finish"
                    and record.get("status") in {"ok", "success"}
                    and record.get("rc") == 0
                    and record.get("teardown_rc", 0) == 0
                    for record in records
                )
                experiment.status = "succeeded" if (
                    slurm_state == "COMPLETED" and terminal_success
                ) else "failed"
                experiment.result = {
                    "slurm_state": slurm_state,
                    "terminal_result": terminal_success,
                    "failure_classification": self._failure_classification(
                        slurm_state, terminal_success, records
                    ),
                    "exit_code": values[2] if len(values) > 2 else "",
                    "records": records,
                    "slurm_records": slurm_records,
                    "output_tail": output_text[-8000:],
                }
                experiment.completed_at = utc_now()
                experiment.timeline.append({
                    "timestamp": experiment.completed_at,
                    "phase": experiment.status,
                    "component": "application",
                    "job_id": experiment.slurm_job_id,
                    "reservation_release": "terminal",
                })
                self.store.save_experiment(experiment)
                self.store.append_event({
                    "kind": "progress", "component": "application",
                    "identity": experiment.identity,
                    "experiment_id": experiment.experiment_id,
                    "job_id": experiment.slurm_job_id,
                    "severity": "info" if terminal_success else "error",
                    "message": (
                        f"{experiment.status}; Slurm cleanup and reservation "
                        "release reached terminal state"
                    ),
                })
                self.store.audit({
                    "identity": experiment.identity,
                    "host_identity": "electroboy-service",
                    "action": "experiment-complete",
                    "target": experiment.backend,
                    "request_id": experiment.experiment_id,
                    "job_id": experiment.slurm_job_id,
                    "completed_at": experiment.completed_at,
                    "outcome": experiment.status,
                })

    @staticmethod
    def _failure_classification(
        slurm_state: str,
        terminal_success: bool,
        records: list[dict[str, Any]],
    ) -> str:
        if terminal_success:
            return "none"
        if slurm_state == "CANCELLED":
            return "cancellation"
        if slurm_state == "TIMEOUT":
            return "timeout"
        text = json.dumps(records).lower()
        if any(value in text for value in ("provider", "iqm error", "job failed")):
            return "provider"
        if slurm_state != "COMPLETED":
            return "slurm"
        if not records:
            return "incomplete"
        return "application"

    @staticmethod
    def _tagged_json(text: str, tag: str) -> list[dict[str, Any]]:
        decoder = json.JSONDecoder()
        records: list[dict[str, Any]] = []
        offset = 0
        while True:
            position = text.find(tag, offset)
            if position < 0:
                return records
            start = text.find("{", position + len(tag))
            if start < 0:
                return records
            try:
                value, length = decoder.raw_decode(text[start:])
            except json.JSONDecodeError:
                offset = start + 1
                continue
            if isinstance(value, dict):
                records.append(value)
            offset = start + length

    def diagnostic_state(self) -> dict[str, Any]:
        return {"schema": "qfw-dashboard-v1", **diagnostics(self.runner).payload()}

    def submit_action(
        self,
        action: str,
        identity: str,
        target: str = "cluster",
        request_id: str = "",
        reason: str = "qfw-dashboard",
    ) -> Operation:
        if identity not in IDENTITIES:
            raise ValueError("unsupported identity")
        if action in self.HOST_ACTIONS:
            if identity != "root":
                raise PermissionError("host cluster actions require root selection")
            argv = self.HOST_ACTIONS[action]
            container = None
        elif action in self.SERVICE_ACTIONS:
            if identity != "root":
                raise PermissionError("service actions require root selection")
            if action in {"directory-stop", "directory-restart"}:
                plane = service_plane_status(self.runner)
                active = [
                    str(item.get("component")) for item in plane.records
                    if item.get("component") in {"nwqsim", "iqm"}
                    and item.get("state") == "ready"
                ]
                if active:
                    raise RuntimeError(
                        "stop managed QPMs before changing the directory service: "
                        + ", ".join(active)
                    )
            argv, container = self.SERVICE_ACTIONS[action]
        elif action in {"node-drain", "node-resume"}:
            if identity != "root" or not _SAFE_NAME.fullmatch(target):
                raise PermissionError("valid node and root selection required")
            if len(reason) > 200 or any(character in reason for character in "\r\n"):
                raise ValueError("invalid node reason")
            argv = (
                "scontrol",
                "update",
                f"NodeName={target}",
                "State=DRAIN" if action == "node-drain" else "State=RESUME",
                f"Reason={reason}" if action == "node-drain" else "",
            )
            argv = tuple(item for item in argv if item)
            container = "slurmctld"
        else:
            raise ValueError(f"unsupported action: {action}")
        if request_id:
            for existing in self.store.operations():
                if existing.get("request_id") == request_id:
                    return Operation(**{
                        key: value for key, value in existing.items()
                        if key in Operation.__dataclass_fields__
                    })
        operation = Operation(
            operation_id=str(uuid.uuid4()),
            action=action,
            identity=identity,
            target=target,
            request_id=request_id,
        )
        self.store.save_operation(operation)
        thread = threading.Thread(
            target=self._run_operation,
            args=(operation, argv, container),
            daemon=True,
            name=f"qfw-dashboard-{operation.operation_id}",
        )
        self._threads[operation.operation_id] = thread
        thread.start()
        return operation

    def _run_operation(
        self, operation: Operation, argv: tuple[str, ...], container: str | None
    ) -> None:
        operation.status = "running"
        operation.started_at = utc_now()
        self.store.save_operation(operation)
        self.store.audit({
            "identity": operation.identity,
            "host_identity": "electroboy-service",
            "action": operation.action,
            "target": operation.target,
            "request_id": operation.request_id,
            "operation_id": operation.operation_id,
            "started_at": operation.started_at,
            "outcome": "started",
        })
        try:
            command = argv if container is None else self.runner.cluster_argv(
                operation.identity, argv, container=container
            )
            def on_line(line: str) -> None:
                operation.output.append(line)
                self.store.save_operation(operation)
                self.store.append_event({
                    "kind": "log",
                    "component": operation.action,
                    "instance": operation.target,
                    "identity": operation.identity,
                    "severity": "info",
                    "operation_id": operation.operation_id,
                    "message": line,
                })
            result = self.runner.stream_host(command, on_line)
            operation.return_code = result.returncode
            operation.status = "succeeded" if result.returncode == 0 else "failed"
        except Exception as error:
            operation.output = [str(error)]
            operation.return_code = 1
            operation.status = "failed"
        operation.completed_at = utc_now()
        self.store.save_operation(operation)
        if operation.return_code:
            self.store.append_event({
                "kind": "progress",
                "component": operation.action,
                "instance": operation.target,
                "identity": operation.identity,
                "severity": "error",
                "operation_id": operation.operation_id,
                "message": f"command exited with {operation.return_code}",
            })
        self.store.audit({
            "identity": operation.identity,
            "host_identity": "electroboy-service",
            "action": operation.action,
            "target": operation.target,
            "request_id": operation.request_id,
            "operation_id": operation.operation_id,
            "started_at": operation.started_at,
            "completed_at": operation.completed_at,
            "outcome": operation.status,
        })

    def submit_experiment(self, request: dict[str, Any]) -> Experiment:
        identity = str(request.get("identity", ""))
        backend = str(request.get("backend", ""))
        example = str(request.get("example", ""))
        mode = str(request.get("allocation_mode", "normal"))
        if identity not in IDENTITIES:
            raise ValueError("unsupported identity")
        if backend not in {"nwqsim", "iqm"}:
            raise ValueError("backend must be nwqsim or iqm")
        if example not in EXAMPLES:
            raise ValueError("unsupported QFw example")
        if mode not in {"normal", "heterogeneous"}:
            raise ValueError("invalid allocation mode")
        if backend == "iqm" and request.get("submit_real_hardware") is not True:
            raise PermissionError("real IQM submission requires explicit confirmation")
        shots = int(request.get("shots", 16))
        if shots < 1 or shots > (256 if backend == "iqm" else 65536):
            raise ValueError("shots outside permitted range")
        nodes = int(request.get("nodes", 1))
        if nodes < 1 or nodes > 8:
            raise ValueError("nodes outside permitted range")
        tasks = self._bounded(request, "tasks", nodes, 1, 128)
        launcher_nodes = self._bounded(request, "launcher_nodes", 1, 1, 8)
        launcher_tasks = self._bounded(
            request, "launcher_tasks", launcher_nodes, 1, 128
        )
        time_minutes = int(request.get("time_minutes", 15 if backend == "iqm" else 45))
        maximum_minutes = 15 if backend == "iqm" else 240
        if time_minutes < 1 or time_minutes > maximum_minutes:
            raise ValueError("time_minutes outside permitted range")
        chemistry_app = str(request.get("chemistry_app", ""))
        if example == "chemistry":
            if not chemistry_app.startswith("/workspace/") or "\n" in chemistry_app:
                raise ValueError("chemistry requires an absolute /workspace application path")
        requirements = {
            "circ_count": self._bounded(request, "circ_count", 1, 1, 1000000),
            "max_qubits": self._bounded(request, "max_qubits", 5, 1, 10000),
            "max_depth": self._bounded(request, "max_depth", 100, 1, 10000000),
            "max_one_q_gates": self._bounded(
                request, "max_one_q_gates", 0, 0, 100000000
            ),
            "max_two_q_gates": self._bounded(
                request, "max_two_q_gates", 0, 0, 100000000
            ),
            "max_measurements": self._bounded(
                request, "max_measurements", 0, 0, 100000000
            ),
            "workload_kind": str(request.get("workload_kind", "quantum")),
            "shots": shots,
            "nodes": nodes,
            "tasks": tasks,
            "launcher_nodes": launcher_nodes,
            "launcher_tasks": launcher_tasks,
            "partition": self._optional_name(request, "partition", "normal"),
            "account": self._optional_name(request, "account"),
            "qos": self._optional_name(request, "qos"),
            "time_minutes": time_minutes,
            "chemistry_app": chemistry_app,
        }
        if requirements["workload_kind"] not in {"quantum", "hybrid"}:
            raise ValueError("workload_kind must be quantum or hybrid")
        experiment = Experiment(
            experiment_id=str(uuid.uuid4()),
            identity=identity,
            backend=backend,
            example=example,
            allocation_mode=mode,
            manifest={
                "schema": "qfw-experiment-manifest-v1",
                "identity": identity,
                "backend": backend,
                "example": example,
                "allocation_mode": mode,
                "requirements": requirements,
                "retry_of": str(request.get("retry_of", "")),
                "cluster_revision": self._revision(self.cluster_root),
                "electroboy_revision": self._revision(
                    self.cluster_root / "dashboard/external/electroboy"
                ),
                "backend_configuration": {
                    "service_id": "nwqsim" if backend == "nwqsim"
                    else "iqm-ornl-20q",
                    "site_config": "/etc/openqse/qfw/site.yaml",
                },
                "deployment_inventory": inventory_status(self.runner).records,
            },
            timeline=[{
                "timestamp": utc_now(), "phase": "created",
                "component": "dashboard",
            }],
        )
        self.store.save_experiment(experiment)
        self.store.audit({
            "identity": identity,
            "host_identity": "electroboy-service",
            "action": "experiment-submit",
            "target": backend,
            "request_id": experiment.experiment_id,
            "hardware": backend == "iqm",
            "outcome": "accepted",
        })
        thread = threading.Thread(
            target=self._run_experiment,
            args=(experiment, requirements),
            daemon=True,
            name=f"qfw-experiment-{experiment.experiment_id}",
        )
        self._threads[experiment.experiment_id] = thread
        thread.start()
        return experiment

    def _revision(self, path: Path) -> str:
        result = self.runner.host(("git", "-C", str(path), "rev-parse", "HEAD"))
        return result.stdout.strip() if result.returncode == 0 else "unknown"

    @staticmethod
    def _bounded(
        request: dict[str, Any], name: str, default: int, minimum: int, maximum: int
    ) -> int:
        value = int(request.get(name, default))
        if value < minimum or value > maximum:
            raise ValueError(f"{name} outside permitted range")
        return value

    @staticmethod
    def _optional_name(
        request: dict[str, Any], name: str, default: str = ""
    ) -> str:
        value = str(request.get(name, default)).strip()
        if value and not _SAFE_NAME.fullmatch(value):
            raise ValueError(f"invalid {name}")
        return value

    def _run_experiment(
        self, experiment: Experiment, requirements: dict[str, Any]
    ) -> None:
        experiment.status = "submitting"
        experiment.timeline.append({
            "timestamp": utc_now(), "phase": "submitting",
            "component": "slurm",
        })
        self.store.save_experiment(experiment)
        shared_allocation = [f"--partition={requirements['partition']}"]
        for name in ("account", "qos"):
            if requirements[name]:
                shared_allocation.append(f"--{name}={requirements[name]}")
        application_allocation = [
            *shared_allocation,
            f"--nodes={requirements['nodes']}",
            f"--ntasks={requirements['tasks']}",
        ]
        if experiment.allocation_mode == "normal":
            allocation_args = application_allocation
        else:
            allocation_args = [
                *shared_allocation,
                f"--nodes={requirements['launcher_nodes']}",
                f"--ntasks={requirements['launcher_tasks']}",
                ":",
                *application_allocation,
            ]
        qpu = "nwqsim" if experiment.backend == "nwqsim" else "ornl-iqm-20q"
        quantum_fields = [
            f"--qpu={qpu}",
            f"--workload-kind={requirements['workload_kind']}",
            f"--circ-count={requirements['circ_count']}",
            f"--max-qubits={requirements['max_qubits']}",
            f"--max-depth={requirements['max_depth']}",
            f"--max-shots={requirements['shots']}",
        ]
        for name in ("max_one_q_gates", "max_two_q_gates", "max_measurements"):
            if requirements[name]:
                quantum_fields.append(f"--{name.replace('_', '-')}={requirements[name]}")
        quantum_options = " ".join(quantum_fields)
        chemistry_environment = ""
        if experiment.example == "chemistry":
            chemistry_app = str(requirements["chemistry_app"])
            chemistry_environment = (
                f"QFW_CHEM_APP_DIR={shlex.quote(str(Path(chemistry_app).parent))} "
                f"QFW_RUN_ALL_CHEM_APP={shlex.quote(chemistry_app)} "
            )
        script = (
            "set -euo pipefail; "
            "export QFW_SHARED_ROOT=/workspace/qfw-container-base; "
            "export QFW_RUN_BASE_DIR=${HOME}/qfw-runs; "
            "mkdir -p ${QFW_RUN_BASE_DIR}; "
            "source /opt/openqse/qfw/bin/qfw-activate "
            "--venv /opt/openqse/qfw-venv; "
            "cd ${QFW_SHARE_DIR}/examples; "
            f"{chemistry_environment}QFW_RUN_ALL_TESTS={experiment.example} "
            f"QFW_RUN_ALL_SHOTS={requirements['shots']} ./qfw_run_all.sh "
            f"--service-mode site --backend {experiment.backend}; "
            "qfw-deactivate"
        )
        output_path = (
            f"/workspace/home/{experiment.identity}/"
            f"qfw-{experiment.experiment_id}.out"
        )
        argv = (
            "sbatch", "--parsable", *shlex.split(quantum_options),
            *allocation_args, f"--job-name=qfw-{experiment.example}",
            f"--time={requirements['time_minutes']}",
            f"--output={output_path}",
            f"--wrap={shlex.join(('bash', '-lc', script))}",
        )
        experiment.manifest["command"] = shlex.join(argv)
        experiment.manifest["output_path"] = output_path
        experiment.manifest["submitted_at"] = utc_now()
        result = self.runner.cluster(
            experiment.identity, argv, timeout=30
        )
        if result.returncode:
            experiment.status = "failed"
            experiment.result = {
                "failure_classification": "submission",
                "error": result.stderr or result.stdout,
            }
            experiment.completed_at = utc_now()
            experiment.timeline.append({
                "timestamp": experiment.completed_at, "phase": "failed",
                "component": "slurm", "classification": "submission",
            })
        else:
            experiment.slurm_job_id = result.stdout.strip().split(";")[0]
            experiment.status = "submitted"
            experiment.artifacts = [
                output_path
            ]
            experiment.timeline.append({
                "timestamp": utc_now(), "phase": "submitted",
                "component": "slurm", "job_id": experiment.slurm_job_id,
            })
        self.store.save_experiment(experiment)
        self.store.append_event({
            "kind": "progress",
            "component": "application",
            "identity": experiment.identity,
            "experiment_id": experiment.experiment_id,
            "job_id": experiment.slurm_job_id,
            "severity": "error" if result.returncode else "info",
            "message": experiment.status,
        })

    def cancel_experiment(self, experiment_id: str, identity: str) -> None:
        matches = [
            item for item in self.store.experiments()
            if item.get("experiment_id") == experiment_id
        ]
        if not matches:
            raise KeyError("experiment not found")
        experiment = matches[0]
        if identity != "root" and identity != experiment.get("identity"):
            raise PermissionError("experiment is owned by another identity")
        job_id = str(experiment.get("slurm_job_id", ""))
        if job_id:
            result = self.runner.cluster(identity, ("scancel", job_id))
            if result.returncode:
                raise RuntimeError(result.stderr or result.stdout)
        self.store.append_event({
            "kind": "progress",
            "component": "slurm",
            "identity": str(experiment.get("identity", identity)),
            "experiment_id": experiment_id,
            "job_id": job_id,
            "severity": "warning",
            "message": "cancellation requested",
        })
        self.store.audit({
            "identity": identity,
            "host_identity": "electroboy-service",
            "action": "experiment-cancel",
            "target": experiment_id,
            "request_id": job_id,
            "outcome": "requested",
        })

    def retry_experiment(
        self, experiment_id: str, identity: str, submit_real_hardware: bool
    ) -> Experiment:
        source = self._owned_experiment(experiment_id, identity)
        manifest = source.get("manifest", {})
        requirements = manifest.get("requirements", {})
        request = {
            "identity": identity,
            "backend": source.get("backend"),
            "example": source.get("example"),
            "allocation_mode": source.get("allocation_mode"),
            **requirements,
            "submit_real_hardware": submit_real_hardware,
            "retry_of": experiment_id,
        }
        return self.submit_experiment(request)

    def compare_results(
        self, experiment_ids: list[str], identity: str
    ) -> dict[str, Any]:
        if len(experiment_ids) != 2 or experiment_ids[0] == experiment_ids[1]:
            raise ValueError("comparison requires two different experiments")
        records = [self._owned_experiment(item, identity) for item in experiment_ids]
        signatures = [
            (record.get("backend"), record.get("example")) for record in records
        ]
        if signatures[0] != signatures[1]:
            raise ValueError("experiments must use the same backend and example")
        metrics = [self._result_metrics(record) for record in records]
        keys = sorted(set(metrics[0]) | set(metrics[1]))
        return {
            "schema": "qfw-dashboard-comparison-v1",
            "experiments": experiment_ids,
            "signature": {"backend": signatures[0][0], "example": signatures[0][1]},
            "metrics": [
                {"name": key, "left": metrics[0].get(key), "right": metrics[1].get(key)}
                for key in keys
            ],
        }

    def _owned_experiment(self, experiment_id: str, identity: str) -> dict[str, Any]:
        if identity not in IDENTITIES:
            raise ValueError("unsupported identity")
        match = next((
            item for item in self.store.experiments()
            if item.get("experiment_id") == experiment_id
        ), None)
        if match is None:
            raise KeyError("experiment not found")
        if identity != "root" and match.get("identity") != identity:
            raise PermissionError("experiment is owned by another identity")
        return match

    @staticmethod
    def _result_metrics(experiment: dict[str, Any]) -> dict[str, Any]:
        records = experiment.get("result", {}).get("records", [])
        example = next((
            item for item in records if item.get("kind") == "example"
        ), {})
        metrics = example.get("metrics", {})
        return metrics if isinstance(metrics, dict) else {}

    def shell_context(self, identity: str, target: str) -> dict[str, str]:
        if identity not in IDENTITIES:
            raise ValueError("unsupported identity")
        if not _SAFE_NAME.fullmatch(target):
            raise ValueError("invalid shell target")
        service_nodes = {"iqm-head", "nwqsim-head", "nwqsim-worker-1",
                         "nwqsim-worker-2", "slurmdbd", "slurmrestd", "mysql"}
        if target in service_nodes and identity != "root":
            raise PermissionError("service-node shells require root selection")
        home = "/root" if identity == "root" else f"/workspace/home/{identity}"
        if target == "slurmctld":
            return {"identity": identity, "target": target, "home": home}
        allowed = {"slurmctld"}
        if identity == "root":
            containers = self.runner.host((
                "docker", "compose", "ps", "--services", "--status", "running",
            ))
            if containers.returncode == 0:
                allowed.update(containers.stdout.split())
        else:
            jobs = self.runner.cluster(identity, (
                "squeue", "--noheader", "--user", identity,
                "--states=RUNNING", "--format=%N",
            ))
            if jobs.returncode == 0:
                for node_list in jobs.stdout.split():
                    hosts = self.runner.cluster(
                        identity, ("scontrol", "show", "hostnames", node_list)
                    )
                    if hosts.returncode == 0:
                        allowed.update(hosts.stdout.split())
        if target not in allowed:
            raise PermissionError("target is not allocated to the selected identity")
        return {"identity": identity, "target": target, "home": home}

    def events(self, cursor: int, limit: int, identity: str) -> dict[str, Any]:
        if identity not in IDENTITIES:
            raise ValueError("unsupported identity")
        return {
            "schema": "qfw-dashboard-event-page-v1",
            **self.store.events(cursor=cursor, limit=limit, identity=identity),
        }

    def logs(
        self, source: str, cursor: int, limit: int, identity: str,
        instance: str = "",
    ) -> dict[str, Any]:
        if source == "application":
            experiment = self._owned_experiment(instance, identity)
            artifacts = experiment.get("artifacts", [])
            if not artifacts:
                raise KeyError("experiment output is not available")
            owner = str(experiment.get("identity"))
            prefix = f"/workspace/home/{owner}/"
            path = str(artifacts[0])
            if not path.startswith(prefix):
                raise PermissionError("application output is outside its home")
            selected = LogSource(
                "application", instance, "slurmctld", path, visibility=owner
            )
        elif source in SOURCES:
            selected = SOURCES[source]
        else:
            raise ValueError("unknown log source")
        return read_source(
            self.runner, selected, identity=identity,
            cursor=max(0, cursor), limit=max(1, min(limit, 500)),
        )

    def artifact(
        self, experiment_id: str, index: int, identity: str
    ) -> dict[str, Any]:
        experiment = self._owned_experiment(experiment_id, identity)
        artifacts = experiment.get("artifacts", [])
        if index < 0 or index >= len(artifacts):
            raise KeyError("artifact not found")
        owner = str(experiment.get("identity"))
        path = str(artifacts[index])
        if not path.startswith(f"/workspace/home/{owner}/"):
            raise PermissionError("artifact is outside the experiment home")
        reader = (
            "import base64, pathlib, sys; "
            "p=pathlib.Path(sys.argv[1]); "
            "data=p.read_bytes(); "
            "assert len(data) <= 8388608, 'artifact exceeds 8 MiB'; "
            "print(base64.b64encode(data).decode('ascii'))"
        )
        result = self.runner.cluster(
            owner, ("python3", "-c", reader, path), timeout=15
        )
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        try:
            data = base64.b64decode(result.stdout.strip(), validate=True)
        except ValueError as error:
            raise RuntimeError("artifact reader returned invalid data") from error
        return {
            "schema": "qfw-dashboard-artifact-v1",
            "experiment_id": experiment_id,
            "identity": owner,
            "path": path,
            "name": Path(path).name,
            "size": len(data),
            "content_base64": base64.b64encode(data).decode("ascii"),
        }

    def command_preview(self, request: dict[str, Any]) -> str:
        identity = str(request.get("identity", "user-a"))
        backend = str(request.get("backend", "nwqsim"))
        mode = str(request.get("allocation_mode", "normal"))
        nodes = int(request.get("nodes", 1))
        if identity not in IDENTITIES:
            raise ValueError("unsupported identity")
        if backend not in {"nwqsim", "iqm"}:
            raise ValueError("backend must be nwqsim or iqm")
        if mode not in {"normal", "heterogeneous"}:
            raise ValueError("invalid allocation mode")
        if nodes < 1 or nodes > 8:
            raise ValueError("nodes outside permitted range")
        tasks = self._bounded(request, "tasks", nodes, 1, 128)
        launcher_nodes = self._bounded(request, "launcher_nodes", 1, 1, 8)
        launcher_tasks = self._bounded(
            request, "launcher_tasks", launcher_nodes, 1, 128
        )
        shots = self._bounded(
            request, "shots", 16, 1, 256 if backend == "iqm" else 65536
        )
        time_minutes = self._bounded(
            request, "time_minutes", 15 if backend == "iqm" else 45,
            1, 15 if backend == "iqm" else 240,
        )
        workload = str(request.get("workload_kind", "quantum"))
        if workload not in {"quantum", "hybrid"}:
            raise ValueError("workload_kind must be quantum or hybrid")
        quantum = [
            f"--qpu={'nwqsim' if backend == 'nwqsim' else 'ornl-iqm-20q'}",
            f"--workload-kind={workload}",
            f"--circ-count={self._bounded(request, 'circ_count', 1, 1, 1000000)}",
            f"--max-qubits={self._bounded(request, 'max_qubits', 5, 1, 10000)}",
            f"--max-depth={self._bounded(request, 'max_depth', 100, 1, 10000000)}",
            f"--max-shots={shots}",
        ]
        for name in ("max_one_q_gates", "max_two_q_gates", "max_measurements"):
            value = self._bounded(request, name, 0, 0, 100000000)
            if value:
                quantum.append(f"--{name.replace('_', '-')}={value}")
        partition = self._optional_name(request, "partition", "normal")
        shared = [f"--partition={partition}"]
        for name in ("account", "qos"):
            value = self._optional_name(request, name)
            if value:
                shared.append(f"--{name}={value}")
        classical = [*shared, f"--nodes={nodes}", f"--ntasks={tasks}"]
        if mode == "heterogeneous":
            classical = [
                *shared,
                f"--nodes={launcher_nodes}",
                f"--ntasks={launcher_tasks}",
                ":",
                *classical,
            ]
        return shlex.join((
            "sbatch", *quantum, *classical, f"--time={time_minutes}",
            "--wrap=bash -lc '<QFw activation and example command>'",
        )) + f" # identity={identity}"
