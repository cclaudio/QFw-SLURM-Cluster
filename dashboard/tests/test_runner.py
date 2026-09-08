from pathlib import Path
from unittest.mock import patch

import pytest

from qfw_slurm_dashboard.runner import CommandRunner


def test_cluster_runner_captures_selected_identity() -> None:
    runner = CommandRunner(Path("."))
    with patch("qfw_slurm_dashboard.runner.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        runner.cluster("user-b", ("id", "-un"), container="c1")
    argv = run.call_args.args[0]
    assert argv[:5] == ["docker", "exec", "--user", "user-b", "--workdir"]
    assert "/workspace/home/user-b" in argv
    assert "HOME=/workspace/home/user-b" in argv
    assert argv[-3:-1] == ["/bin/bash", "-lc"]
    assert "timeout --signal=TERM --kill-after=2 30" in argv[-1]
    assert "id -un" in argv[-1]


def test_cluster_timeout_is_enforced_inside_container() -> None:
    runner = CommandRunner(Path("."))
    with patch("qfw_slurm_dashboard.runner.subprocess.run") as run:
        run.return_value.returncode = 124
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        runner.cluster("root", ("slow-command",), timeout=7)
    argv = run.call_args.args[0]
    assert "timeout --signal=TERM --kill-after=2 7" in argv[-1]
    assert run.call_args.kwargs["timeout"] == 10


def test_cluster_runner_rejects_unknown_identity() -> None:
    with pytest.raises(ValueError, match="unsupported cluster identity"):
        CommandRunner(Path(".")).cluster("operator", ("id",))


def test_stream_host_delivers_each_output_line(tmp_path) -> None:
    observed = []
    processes = []
    result = CommandRunner(tmp_path).stream_host(
        ("printf", "one\\ntwo\\n"), observed.append, on_start=processes.append
    )
    assert result.returncode == 0
    assert observed == ["one", "two"]
    assert len(processes) == 1
