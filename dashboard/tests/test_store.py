from qfw_slurm_dashboard.models import Experiment, Operation
from qfw_slurm_dashboard.store import DashboardStore


def test_store_redacts_and_bounds_operation_output(tmp_path) -> None:
    store = DashboardStore(tmp_path)
    operation = Operation("op", "test", "user-a", "cluster")
    operation.output = [f"line {index}" for index in range(1100)]
    operation.output.append("api_key=secret")
    store.save_operation(operation)
    stored = store.operations()[0]
    assert len(stored["output"]) == 1000
    assert "secret" not in stored["output"][-1]


def test_event_cursor_reports_gap_after_retention_window(tmp_path) -> None:
    store = DashboardStore(tmp_path)
    for index in range(10002):
        store.append_event({"kind": "log", "message": str(index)})
    payload = store.events(cursor=0, limit=2, identity="root")
    assert payload["gap"] is True
    assert len(payload["events"]) == 2


def test_regular_identity_sees_only_its_events(tmp_path) -> None:
    store = DashboardStore(tmp_path)
    store.append_event({"kind": "log", "identity": "user-a", "message": "a"})
    store.append_event({"kind": "log", "identity": "user-b", "message": "b"})
    payload = store.events(identity="user-a")
    assert [event["message"] for event in payload["events"]] == ["a"]


def test_experiment_results_are_redacted_before_persistence(tmp_path) -> None:
    store = DashboardStore(tmp_path)
    experiment = Experiment("exp", "user-a", "iqm", "chemistry", "normal")
    experiment.result = {"error": "api_key=secret"}
    store.save_experiment(experiment)
    assert "secret" not in store.experiments()[0]["result"]["error"]


def test_nested_and_quoted_credentials_are_redacted(tmp_path) -> None:
    store = DashboardStore(tmp_path)
    experiment = Experiment("exp", "user-a", "iqm", "chemistry", "normal")
    experiment.result = {
        "provider": {"refresh-token": "one"},
        "log": 'authorization: "Bearer two" password=\'three\'',
    }
    store.save_experiment(experiment)
    rendered = str(store.experiments()[0])
    assert "one" not in rendered
    assert "two" not in rendered
    assert "three" not in rendered
