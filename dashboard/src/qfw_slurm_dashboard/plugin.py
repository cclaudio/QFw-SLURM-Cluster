"""ElectroBoy workflow registration."""

from __future__ import annotations

from electroboy.service.registry import WorkflowDefinition

from .routes import HANDLERS, ROUTES


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="qfw-slurm-cluster",
        label="QFw Slurm Cluster",
        modules=(
            "core",
            "agent_sessions",
            "markdown_documents",
            "file_browser",
            "project_shell",
            "progress",
        ),
        stages=(),
        project_kinds=("project", "meta-project"),
        backend_package="qfw_slurm_dashboard",
        frontend_bundle="qfw-slurm-cluster.js",
        frontend_stylesheets=("css/qfw-slurm-cluster.css",),
        asset_package="qfw_slurm_dashboard",
        asset_root="assets",
        asset_resource="frontend.js",
        routes=ROUTES,
        handlers=HANDLERS,
        workspace_policy="shared-singleton",
    )
