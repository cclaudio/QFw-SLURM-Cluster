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


class DashboardService:
    HOST_ACTIONS = {
        "cluster-build": ("./do_build.sh",),
        "cluster-start": ("./do_startup.sh",),
        "cluster-stop": ("./do_stop.sh",),
        "cluster-restart": ("./do_restart.sh",),
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
        payload = aggregate_state(collect_all(self.runner))
        payload["operations"] = self.store.operations()
        payload["experiments"] = self.store.experiments()
        payload["identities"] = list(IDENTITIES)
        return payload

    def diagnostic_state(self) -> dict[str, Any]:
        return {"schema": "qfw-dashboard-v1", **diagnostics(self.runner).payload()}

    def submit_action(
        self,
        action: str,
        identity: str,
        target: str = "cluster",
        request_id: str = "",
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
            argv = (
                "scontrol",
                "update",
                f"NodeName={target}",
                "State=DRAIN" if action == "node-drain" else "State=RESUME",
                "Reason=qfw-dashboard" if action == "node-drain" else "",
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
            result = self.runner.host(argv, timeout=3600) if host else self.runner.cluster(
                operation.identity, argv, timeout=600
            )
            operation.return_code = result.returncode
            operation.output = (result.stdout + result.stderr).splitlines()
            operation.status = "succeeded" if result.returncode == 0 else "failed"
        except Exception as error:
            operation.output = [str(error)]
            operation.return_code = 1
            operation.status = "failed"
        operation.completed_at = utc_now()
        self.store.save_operation(operation)
        for line in operation.output:
            self.store.append_event({
                "kind": "log",
                "component": operation.action,
                "instance": operation.target,
                "identity": operation.identity,
                "severity": "error" if operation.return_code else "info",
                "operation_id": operation.operation_id,
                "message": line,
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
        if not _SAFE_NAME.fullmatch(example):
            raise ValueError("invalid example name")
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
        experiment = Experiment(
            experiment_id=str(uuid.uuid4()),
            identity=identity,
            backend=backend,
            example=example,
            allocation_mode=mode,
        )
        self.store.save_experiment(experiment)
        thread = threading.Thread(
            target=self._run_experiment,
            args=(experiment, shots, nodes),
            daemon=True,
            name=f"qfw-experiment-{experiment.experiment_id}",
        )
        self._threads[experiment.experiment_id] = thread
        thread.start()
        return experiment

    def _run_experiment(self, experiment: Experiment, shots: int, nodes: int) -> None:
        experiment.status = "submitting"
        self.store.save_experiment(experiment)
        allocation = (
            f"--nodes={nodes} --ntasks={nodes}"
            if experiment.allocation_mode == "normal"
            else f"--nodes=1 --ntasks=1 : --nodes={nodes} --ntasks={nodes}"
        )
        script = (
            "set -euo pipefail; "
            "export QFW_SHARED_ROOT=/workspace/qfw-container-base; "
            "export QFW_RUN_BASE_DIR=${HOME}/qfw-runs; "
            "mkdir -p ${QFW_RUN_BASE_DIR}; "
            "source /opt/openqse/qfw/bin/qfw-activate "
            "--venv /opt/openqse/qfw-venv; "
            "cd ${QFW_SHARE_DIR}/examples; "
            f"./{experiment.example}.sh --service-mode site "
            f"--backend {experiment.backend} --shots {shots}; "
            "qfw-deactivate"
        )
        wrap = (
            "sbatch --parsable "
            f"{allocation} --job-name=qfw-{experiment.example} "
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

    def events(self, cursor: int, limit: int) -> dict[str, Any]:
        return self.store.events(cursor=cursor, limit=limit)

    def command_preview(self, request: dict[str, Any]) -> str:
        identity = str(request.get("identity", "user-a"))
        backend = str(request.get("backend", "nwqsim"))
        mode = str(request.get("allocation_mode", "normal"))
        nodes = int(request.get("nodes", 1))
        quantum = [
            f"--qpm-service={backend}",
            f"--circ-count={int(request.get('circ_count', 1))}",
            f"--max-qubits={int(request.get('max_qubits', 5))}",
            f"--max-depth={int(request.get('max_depth', 100))}",
            f"--shots={int(request.get('shots', 16))}",
        ]
        classical = [f"--nodes={nodes}", f"--ntasks={nodes}"]
        if mode == "heterogeneous":
            classical = ["--nodes=1", "--ntasks=1", ":", *classical]
        return " ".join(["salloc", *quantum, *classical, f"# identity={identity}"])
