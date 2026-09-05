from importlib import resources

from qfw_slurm_dashboard.plugin import workflow


def test_workflow_registers_only_public_required_modules() -> None:
    definition = workflow()
    assert definition.id == "qfw-slurm-cluster"
    assert definition.workspace_policy == "shared-singleton"
    assert definition.modules == (
        "core",
        "markdown_documents",
        "file_browser",
        "project_shell",
        "progress",
    )


def test_frontend_declares_exact_pane_catalog_and_fixed_widgets() -> None:
    frontend = resources.files("qfw_slurm_dashboard").joinpath(
        "assets/frontend.js"
    ).read_text()
    for label in ("Dashboard", "Progress", "File", "Shell"):
        assert f'label: "{label}"' in frontend
    for widget in (
        "health", "nodes", "services", "allocations",
        "experiments", "topology", "results", "alerts",
    ):
        assert f'["{widget}",' in frontend
    assert "BroadcastChannel" in frontend
    assert "setInterval(refreshState, 2500)" in frontend
