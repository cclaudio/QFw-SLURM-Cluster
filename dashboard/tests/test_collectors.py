from pathlib import Path

from qfw_slurm_dashboard.collectors import collect_all, docker_status
from qfw_slurm_dashboard.runner import CommandResult


class FakeRunner:
    def host(self, argv, timeout=None):
        return CommandResult(tuple(argv), 0, '{"State":"running","Name":"c1"}\n', "")

    def cluster(self, identity, argv, **kwargs):
        command = argv[0]
        if command == "bash":
            command = "qfw-sinfo" if "qfw-sinfo" in argv[-1] else "qfw-squeue"
        if command == "sinfo":
            return CommandResult(tuple(argv), 0, "c1|normal|idle||4|1024|compute\n", "")
        if command == "squeue":
            return CommandResult(tuple(argv), 0, "", "")
        if command == "scontrol":
            return CommandResult(tuple(argv), 0, "Slurmctld(primary) at UP\n", "")
        if command == "sacctmgr":
            return CommandResult(tuple(argv), 0, "linux|\n", "")
        if command == "qfw-sinfo":
            return CommandResult(tuple(argv), 1, "", "not running")
        if command == "qfw-squeue":
            return CommandResult(
                tuple(argv), 0,
                '{"schema":"qfw-squeue-v1","errors":[],"jobs":[]}\n', ""
            )
        if command == "qfw-site-services":
            return CommandResult(tuple(argv), 1, "Directory service\nstate not found\n", "")
        raise AssertionError(command)


def test_collectors_keep_partial_state_when_service_is_stopped() -> None:
    sources = {item.name: item for item in collect_all(FakeRunner())}
    assert sources["docker"].status == "ready"
    assert next(
        item for item in sources["slurm"].records if item["kind"] == "node"
    )["node"] == "c1"
    assert sources["services"].status == "stopped"
    assert sources["allocations"].status == "ready"
    assert sources["service-plane"].status == "stopped"


def test_malformed_docker_output_is_typed_unavailable() -> None:
    runner = FakeRunner()
    runner.host = lambda argv, timeout=None: CommandResult(tuple(argv), 0, "{", "")
    assert docker_status(runner).status == "unavailable"


def test_collector_exception_does_not_hide_healthy_sources() -> None:
    runner = FakeRunner()
    original = runner.host
    runner.host = lambda argv, timeout=None: (_ for _ in ()).throw(
        RuntimeError("docker unavailable")
    ) if argv[0] == "docker" else original(argv, timeout)
    sources = {item.name: item for item in collect_all(runner)}
    assert sources["docker"].status == "unavailable"
    assert sources["slurm"].status == "ready"
