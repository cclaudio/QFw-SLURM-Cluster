from pathlib import Path
from unittest.mock import patch

import pytest

from qfw_slurm_dashboard.runner import CommandResult
from qfw_slurm_dashboard.service import DashboardService
from qfw_slurm_dashboard.models import Operation


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
        "tasks": 4,
        "launcher_nodes": 2,
        "launcher_tasks": 2,
        "account": "science",
        "qos": "debug",
    })
    assert "--qpu=nwqsim" in preview
    assert "--circ-count=3" in preview
    assert "--max-shots=64" in preview
    assert "--max-one-q-gates" not in preview
    assert "--account=science --qos=debug" in preview
    assert "--nodes=2 --ntasks=2 : --partition=normal" in preview
    assert "--nodes=2 --ntasks=4" in preview
    assert "identity=user-c" in preview
    assert preview.startswith("sbatch ")


def test_submission_passes_wrap_as_one_argument(tmp_path) -> None:
    dashboard = service(tmp_path)
    with patch("qfw_slurm_dashboard.service.threading.Thread", ImmediateThread):
        with patch.object(dashboard.runner, "cluster") as cluster:
            cluster.return_value = CommandResult(("sbatch",), 0, "42\n", "")
            experiment = dashboard.submit_experiment({
                "identity": "user-a",
                "backend": "nwqsim",
                "example": "qiskit-simple",
            })
    argv = cluster.call_args.args[1]
    wrap = next(item for item in argv if item.startswith("--wrap="))
    assert wrap.startswith("--wrap=bash -lc ")
    assert "${QFW_SHARE_DIR}/examples" in wrap
    assert experiment.slurm_job_id == "42"


def test_refresh_accepts_structured_ok_terminal_record(tmp_path) -> None:
    dashboard = service(tmp_path)
    from qfw_slurm_dashboard.models import Experiment
    stored = Experiment("exp", "user-a", "nwqsim", "qiskit-simple", "normal")
    stored.slurm_job_id = "42"
    stored.artifacts = ["/workspace/home/user-a/output"]
    dashboard.store.save_experiment(stored)
    summary = {
        "schema": "qfw-example-wrapper-v1", "kind": "wrapper",
        "event": "finish", "status": "ok", "rc": 0, "teardown_rc": 0,
    }
    responses = iter((
        CommandResult(("sacct",), 0, "42|COMPLETED|0:0\n", ""),
        CommandResult(("tail",), 0, "Summary JSONL: /tmp/summary\n", ""),
        CommandResult(("cat",), 0, __import__("json").dumps(summary) + "\n", ""),
    ))
    with patch.object(dashboard.runner, "cluster", side_effect=lambda *a, **k: next(responses)):
        dashboard._refresh_experiments()
    assert dashboard.store.experiments()[0]["status"] == "succeeded"


def test_individual_qpm_action_runs_on_service_node(tmp_path) -> None:
    dashboard = service(tmp_path)
    with patch("qfw_slurm_dashboard.service.threading.Thread", ImmediateThread):
        with patch.object(dashboard.runner, "stream_host") as host:
            host.return_value = CommandResult(("docker",), 0, "ready", "")
            operation = dashboard.submit_action(
                "service-start", "root", target="nwqsim"
            )
    argv = host.call_args.args[0]
    assert "slurmctld" in argv
    assert "qfw-site-services start --target nwqsim" in argv[-1]
    assert operation.status == "succeeded"


def test_service_action_rejects_unknown_target(tmp_path) -> None:
    dashboard = service(tmp_path)
    with pytest.raises(ValueError, match="unsupported service target"):
        dashboard.submit_action("service-stop", "root", target="other")


def test_operation_abort_terminates_running_process_group(tmp_path) -> None:
    dashboard = service(tmp_path)
    operation = Operation("op", "service-start", "root", "all", status="running")
    dashboard.store.save_operation(operation)
    process = type("Process", (), {"pid": 42, "poll": lambda self: None})()
    dashboard._processes[operation.operation_id] = process
    with patch("qfw_slurm_dashboard.service.os.getpgid", return_value=84):
        with patch("qfw_slurm_dashboard.service.os.killpg") as killpg:
            result = dashboard.abort_operation(operation.operation_id, "root")
    killpg.assert_called_once()
    assert result.status == "aborting"


def test_regular_shell_target_must_be_allocated(tmp_path) -> None:
    dashboard = service(tmp_path)
    with patch.object(dashboard.runner, "cluster") as cluster:
        cluster.return_value = CommandResult(("squeue",), 0, "", "")
        with pytest.raises(PermissionError, match="not allocated"):
            dashboard.shell_context("user-a", "c1")
    assert dashboard.shell_context("user-a", "slurmctld")["home"] == (
        "/workspace/home/user-a"
    )


def test_regular_shell_cannot_target_service_node(tmp_path) -> None:
    with pytest.raises(PermissionError, match="service-node"):
        service(tmp_path).shell_context("user-a", "iqm-head")


def test_compare_requires_compatible_owned_results(tmp_path) -> None:
    from qfw_slurm_dashboard.models import Experiment
    dashboard = service(tmp_path)
    for identifier, duration in (("left", 1.0), ("right", 2.0)):
        item = Experiment(identifier, "user-a", "nwqsim", "qiskit-simple", "normal")
        item.result = {"records": [{
            "kind": "example", "metrics": {"duration": duration},
        }]}
        dashboard.store.save_experiment(item)
    comparison = dashboard.compare_results(["left", "right"], "user-a")
    assert comparison["metrics"] == [
        {"name": "duration", "left": 1.0, "right": 2.0}
    ]
    with pytest.raises(PermissionError):
        dashboard.compare_results(["left", "right"], "user-b")


def test_artifact_download_uses_captured_owner(tmp_path) -> None:
    import base64
    from qfw_slurm_dashboard.models import Experiment
    dashboard = service(tmp_path)
    experiment = Experiment(
        "exp", "user-a", "nwqsim", "qiskit-simple", "normal"
    )
    experiment.artifacts = ["/workspace/home/user-a/result.json"]
    dashboard.store.save_experiment(experiment)
    encoded = base64.b64encode(b"result\n").decode()
    with patch.object(dashboard.runner, "cluster") as cluster:
        cluster.return_value = CommandResult(("python3",), 0, encoded, "")
        payload = dashboard.artifact("exp", 0, "user-a")
    assert payload["size"] == 7
    assert payload["name"] == "result.json"
    assert cluster.call_args.args[0] == "user-a"
    with pytest.raises(PermissionError):
        dashboard.artifact("exp", 0, "user-b")


def test_preview_rejects_unbounded_hardware_request(tmp_path) -> None:
    with pytest.raises(ValueError, match="time_minutes"):
        service(tmp_path).command_preview({
            "identity": "user-a", "backend": "iqm", "time_minutes": 16,
        })


def test_chemistry_requires_workspace_application_path(tmp_path) -> None:
    with pytest.raises(ValueError, match="chemistry"):
        service(tmp_path).submit_experiment({
            "identity": "user-a", "backend": "nwqsim",
            "example": "chemistry", "chemistry_app": "relative.py",
        })


def test_chemistry_submission_sets_application_directory(tmp_path) -> None:
    dashboard = service(tmp_path)
    with patch("qfw_slurm_dashboard.service.threading.Thread", ImmediateThread):
        with patch.object(dashboard.runner, "cluster") as cluster:
            cluster.return_value = CommandResult(("sbatch",), 0, "42\n", "")
            dashboard.submit_experiment({
                "identity": "user-a",
                "backend": "iqm",
                "example": "chemistry",
                "chemistry_app": "/workspace/apps/chemistry/run.py",
                "submit_real_hardware": True,
            })
    argv = cluster.call_args.args[1]
    wrap = next(item for item in argv if item.startswith("--wrap="))
    assert "QFW_CHEM_APP_DIR=/workspace/apps/chemistry" in wrap
    assert "QFW_RUN_ALL_CHEM_APP=/workspace/apps/chemistry/run.py" in wrap


def test_tagged_driver_results_recover_reservation() -> None:
    records = DashboardService._tagged_json(
        'noise QFW_SLURM_DRIVER_RESULT {"reservation_id":"8","backend":"nwqsim"}',
        "QFW_SLURM_DRIVER_RESULT",
    )
    assert records == [{"reservation_id": "8", "backend": "nwqsim"}]


def test_refresh_recovers_reservation_from_application_log(tmp_path) -> None:
    from qfw_slurm_dashboard.models import Experiment
    dashboard = service(tmp_path)
    stored = Experiment("exp", "user-a", "iqm", "chemistry", "normal")
    stored.slurm_job_id = "42"
    stored.artifacts = ["/workspace/home/user-a/output"]
    dashboard.store.save_experiment(stored)
    summary = {
        "kind": "wrapper", "event": "finish", "status": "ok",
        "rc": 0, "teardown_rc": 0,
    }
    responses = iter((
        CommandResult(("sacct",), 0, "42|COMPLETED|0:0\n", ""),
        CommandResult(
            ("tail",), 0,
            "log=/workspace/home/user-a/case.log\n"
            "Summary JSONL: /workspace/home/user-a/summary.jsonl\n", "",
        ),
        CommandResult(("cat",), 0, __import__("json").dumps(summary), ""),
        CommandResult(("tail",), 0, "Estimator reservation_id=17\n", ""),
    ))
    with patch.object(
        dashboard.runner, "cluster", side_effect=lambda *a, **k: next(responses)
    ):
        dashboard._refresh_experiments()
    assert dashboard.store.experiments()[0]["reservations"] == [["iqm", "17"]]


@pytest.mark.parametrize(
    ("slurm_state", "terminal", "records", "expected"),
    [
        ("COMPLETED", True, [], "none"),
        ("CANCELLED", False, [], "cancellation"),
        ("TIMEOUT", False, [], "timeout"),
        ("COMPLETED", False, [], "incomplete"),
        ("COMPLETED", False, [{"error": "provider failed"}], "provider"),
        ("FAILED", False, [{"error": "application"}], "slurm"),
    ],
)
def test_failure_classification(slurm_state, terminal, records, expected) -> None:
    assert DashboardService._failure_classification(
        slurm_state, terminal, records
    ) == expected
