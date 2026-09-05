from pathlib import Path
from unittest.mock import patch

import pytest

from qfw_slurm_dashboard.runner import CommandResult
from qfw_slurm_dashboard.service import DashboardService


class ImmediateThread:
    def __init__(self, target, args, **kwargs):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


def service(tmp_path) -> DashboardService:
    return DashboardService(Path("."), tmp_path)


def test_host_action_requires_root(tmp_path) -> None:
    with pytest.raises(PermissionError, match="root"):
        service(tmp_path).submit_action("cluster-start", "user-a")


def test_action_is_idempotent_by_request_id(tmp_path) -> None:
    dashboard = service(tmp_path)
    with patch("qfw_slurm_dashboard.service.threading.Thread", ImmediateThread):
        with patch.object(dashboard.runner, "stream_host") as host:
            host.return_value = CommandResult(("./do_startup.sh",), 0, "ready", "")
            first = dashboard.submit_action(
                "cluster-start", "root", request_id="same-request"
            )
            second = dashboard.submit_action(
                "cluster-start", "root", request_id="same-request"
            )
    assert first.operation_id == second.operation_id
    assert host.call_count == 1


def test_hardware_submission_requires_confirmation(tmp_path) -> None:
    with pytest.raises(PermissionError, match="confirmation"):
        service(tmp_path).submit_experiment({
            "identity": "user-a",
            "backend": "iqm",
            "example": "qiskit-simple",
        })


def test_preview_covers_heterogeneous_quantum_request(tmp_path) -> None:
    preview = service(tmp_path).command_preview({
        "identity": "user-c",
        "backend": "nwqsim",
        "allocation_mode": "heterogeneous",
        "nodes": 2,
        "circ_count": 3,
        "max_qubits": 8,
        "max_depth": 200,
        "shots": 64,
    })
    assert "--qpu=nwqsim" in preview
    assert "--circ-count=3" in preview
    assert "--max-shots=64" in preview
    assert "--nodes=1 --ntasks=1 : --nodes=2 --ntasks=2" in preview
    assert "identity=user-c" in preview
