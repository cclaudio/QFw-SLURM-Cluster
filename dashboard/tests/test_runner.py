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
    assert argv[-3:] == ["/bin/bash", "-lc", "id -un"]


def test_cluster_runner_rejects_unknown_identity() -> None:
    with pytest.raises(ValueError, match="unsupported cluster identity"):
        CommandRunner(Path(".")).cluster("operator", ("id",))


def test_stream_host_delivers_each_output_line(tmp_path) -> None:
    observed = []
    result = CommandRunner(tmp_path).stream_host(
        ("printf", "one\\ntwo\\n"), observed.append
    )
    assert result.returncode == 0
    assert observed == ["one", "two"]
