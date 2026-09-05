from qfw_slurm_dashboard.models import SourceState, aggregate_state, utc_now
from qfw_slurm_dashboard.redaction import redact


def source(name: str, status: str) -> SourceState:
    return SourceState(name, status, utc_now())


def test_aggregate_reports_ready_partial_and_stopped() -> None:
    assert aggregate_state([source("a", "ready")])["health"] == "ready"
    assert aggregate_state([
        source("a", "ready"), source("b", "unavailable")
    ])["health"] == "partial"
    assert aggregate_state([
        source("a", "stopped"), source("b", "unavailable")
    ])["health"] == "stopped"


def test_redaction_removes_provider_secrets() -> None:
    value = redact("api_key=secret refresh-token: token Authorization=Bearer abc")
    assert "secret" not in value
    assert " token" not in value
    assert "abc" not in value
    assert value.count("[REDACTED]") == 3
