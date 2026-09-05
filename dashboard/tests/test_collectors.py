from pathlib import Path

from qfw_slurm_dashboard.collectors import collect_all, docker_status
from qfw_slurm_dashboard.runner import CommandResult


class FakeRunner:
    def host(self, argv, timeout=None):
        return CommandResult(tuple(argv), 0, '{"State":"running","Name":"c1"}\n', "")

    def cluster(self, identity, argv, **kwargs):
        command = argv[0]
        if command == "sinfo":
            return CommandResult(tuple(argv), 0, "c1|normal|idle||4|1024|compute\n", "")
        if command == "squeue":
            return CommandResult(tuple(argv), 0, "", "")
        if command == "qfw-sinfo":
            return CommandResult(tuple(argv), 1, "", "not running")
        if command == "qfw-squeue":
            return CommandResult(tuple(argv), 0, "[]\n", "")
        raise AssertionError(command)


def test_collectors_keep_partial_state_when_service_is_stopped() -> None:
    sources = {item.name: item for item in collect_all(FakeRunner())}
    assert sources["docker"].status == "ready"
    assert sources["slurm"].records[0]["node"] == "c1"
    assert sources["services"].status == "stopped"
    assert sources["allocations"].status == "ready"


def test_malformed_docker_output_is_typed_unavailable() -> None:
    runner = FakeRunner()
    runner.host = lambda argv, timeout=None: CommandResult(tuple(argv), 0, "{", "")
    assert docker_status(runner).status == "unavailable"
