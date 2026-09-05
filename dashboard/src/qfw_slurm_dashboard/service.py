"""Dashboard capabilities independent of browser rendering."""

from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from .collectors import collect_all, diagnostics
from .models import Experiment, Operation, aggregate_state, utc_now
from .runner import CommandRunner, IDENTITIES
from .store import DashboardStore

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
EXAMPLES = {
    "init-test",
    "shim-smoke",
    "qiskit-simple",
    "ghz-qiskit",
    "ghz-pennylane",
    "pennylane",
    "qaoa",
    "qiskit-vqe",
    "supermarq",
    "chemistry",
}


class DashboardService:
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
    SERVICE_ACTIONS = {
        "services-start": ("qfw-site-services", "start"),
        "services-stop": ("qfw-site-services", "stop"),
        "services-restart": ("qfw-site-services", "restart"),
        "services-status": ("qfw-site-services", "status"),
    }

    def __init__(self, cluster_root: Path, state_root: Path) -> None:
        self.cluster_root = cluster_root.resolve()
        self.runner = CommandRunner(self.cluster_root)
        self.store = DashboardStore(state_root / "qfw-slurm-cluster")
        self._threads: dict[str, threading.Thread] = {}

    def state(self) -> dict[str, Any]:
        self._refresh_experiments()
        payload = aggregate_state(collect_all(self.runner))
        payload["operations"] = self.store.operations()
        payload["experiments"] = self.store.experiments()
        payload["identities"] = list(IDENTITIES)
        return payload

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
            status = self.runner.cluster(
                experiment.identity,
                (
                    "sacct", "--noheader", "--parsable2",
                    "--jobs", experiment.slurm_job_id,
                    "--format=JobIDRaw,State,ExitCode",
                ),
            )
            if status.returncode:
                continue
            first = next((line for line in status.stdout.splitlines() if line), "")
            values = first.split("|")
            slurm_state = values[1].split()[0].upper() if len(values) > 1 else ""
            if slurm_state and slurm_state not in terminal:
                experiment.status = slurm_state.lower()
                self.store.save_experiment(experiment)
                continue
            if slurm_state in terminal:
                artifact = experiment.artifacts[0] if experiment.artifacts else ""
                output = self.runner.cluster(
                    experiment.identity,
                    ("tail", "-n", "1000", artifact),
                ) if artifact else None
                terminal_success = bool(
                    output and output.returncode == 0
                    and '"event": "finish"' in output.stdout
                    and '"status": "success"' in output.stdout
                )
                experiment.status = "succeeded" if (
                    slurm_state == "COMPLETED" and terminal_success
                ) else "failed"
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
                experiment.result = {
                    "slurm_state": slurm_state,
                    "terminal_result": terminal_success,
                    "exit_code": values[2] if len(values) > 2 else "",
                    "records": records,
                    "output_tail": output_text[-8000:],
                }
                experiment.completed_at = utc_now()
                self.store.save_experiment(experiment)

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
            host = True
        elif action in self.SERVICE_ACTIONS:
            if identity != "root":
                raise PermissionError("service actions require root selection")
            argv = self.SERVICE_ACTIONS[action]
            host = False
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
            host = False
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
            args=(operation, argv, host),
            daemon=True,
            name=f"qfw-dashboard-{operation.operation_id}",
        )
        self._threads[operation.operation_id] = thread
        thread.start()
        return operation

    def _run_operation(
        self, operation: Operation, argv: tuple[str, ...], host: bool
    ) -> None:
        operation.status = "running"
        operation.started_at = utc_now()
        self.store.save_operation(operation)
        self.store.audit({
            "identity": operation.identity,
            "action": operation.action,
            "target": operation.target,
            "request_id": operation.request_id,
            "outcome": "started",
        })
        try:
            command = argv if host else self.runner.cluster_argv(
                operation.identity, argv
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
            "action": operation.action,
            "target": operation.target,
            "request_id": operation.request_id,
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
                "cluster_revision": self._revision(self.cluster_root),
                "electroboy_revision": self._revision(
                    self.cluster_root / "dashboard/external/electroboy"
                ),
            },
        )
        self.store.save_experiment(experiment)
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

    def _run_experiment(
        self, experiment: Experiment, requirements: dict[str, Any]
    ) -> None:
        experiment.status = "submitting"
        self.store.save_experiment(experiment)
        allocation = (
            f"--nodes={requirements['nodes']} --ntasks={requirements['nodes']}"
            if experiment.allocation_mode == "normal"
            else "--nodes=1 --ntasks=1 : "
            f"--nodes={requirements['nodes']} --ntasks={requirements['nodes']}"
        )
        qpu = "nwqsim" if experiment.backend == "nwqsim" else "ornl-iqm-20q"
        quantum_options = " ".join((
            f"--qpu={qpu}",
            f"--workload-kind={requirements['workload_kind']}",
            f"--circ-count={requirements['circ_count']}",
            f"--max-qubits={requirements['max_qubits']}",
            f"--max-depth={requirements['max_depth']}",
            f"--max-shots={requirements['shots']}",
            f"--max-one-q-gates={requirements['max_one_q_gates']}",
            f"--max-two-q-gates={requirements['max_two_q_gates']}",
            f"--max-measurements={requirements['max_measurements']}",
        ))
        script = (
            "set -euo pipefail; "
            "export QFW_SHARED_ROOT=/workspace/qfw-container-base; "
            "export QFW_RUN_BASE_DIR=${HOME}/qfw-runs; "
            "mkdir -p ${QFW_RUN_BASE_DIR}; "
            "source /opt/openqse/qfw/bin/qfw-activate "
            "--venv /opt/openqse/qfw-venv; "
            "cd ${QFW_SHARE_DIR}/examples; "
            f"QFW_RUN_ALL_TESTS={experiment.example} "
            f"QFW_RUN_ALL_SHOTS={requirements['shots']} ./qfw_run_all.sh "
            f"--service-mode site --backend {experiment.backend}; "
            "qfw-deactivate"
        )
        wrap = (
            "sbatch --parsable "
            f"{quantum_options} {allocation} --job-name=qfw-{experiment.example} "
            f"--output=${{HOME}}/qfw-{experiment.experiment_id}.out "
            f"--wrap={json.dumps(script)}"
        )
        result = self.runner.cluster(
            experiment.identity, ("bash", "-lc", wrap), timeout=30
        )
        if result.returncode:
            experiment.status = "failed"
            experiment.result = {"error": result.stderr or result.stdout}
            experiment.completed_at = utc_now()
        else:
            experiment.slurm_job_id = result.stdout.strip().split(";")[0]
            experiment.status = "submitted"
            experiment.artifacts = [
                f"/workspace/home/{experiment.identity}/qfw-{experiment.experiment_id}.out"
            ]
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

    def events(self, cursor: int, limit: int, identity: str) -> dict[str, Any]:
        if identity not in IDENTITIES:
            raise ValueError("unsupported identity")
        return self.store.events(cursor=cursor, limit=limit, identity=identity)

    def command_preview(self, request: dict[str, Any]) -> str:
        identity = str(request.get("identity", "user-a"))
        backend = str(request.get("backend", "nwqsim"))
        mode = str(request.get("allocation_mode", "normal"))
        nodes = int(request.get("nodes", 1))
        quantum = [
            f"--qpu={'nwqsim' if backend == 'nwqsim' else 'ornl-iqm-20q'}",
            f"--workload-kind={request.get('workload_kind', 'quantum')}",
            f"--circ-count={int(request.get('circ_count', 1))}",
            f"--max-qubits={int(request.get('max_qubits', 5))}",
            f"--max-depth={int(request.get('max_depth', 100))}",
            f"--max-shots={int(request.get('shots', 16))}",
            f"--max-one-q-gates={int(request.get('max_one_q_gates', 0))}",
            f"--max-two-q-gates={int(request.get('max_two_q_gates', 0))}",
            f"--max-measurements={int(request.get('max_measurements', 0))}",
        ]
        classical = [f"--nodes={nodes}", f"--ntasks={nodes}"]
        if mode == "heterogeneous":
            classical = ["--nodes=1", "--ntasks=1", ":", *classical]
        return " ".join(["salloc", *quantum, *classical, f"# identity={identity}"])
