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
        "health", "inventory", "nodes", "services", "allocations",
        "experiments", "topology", "results", "alerts",
    ):
        assert f'["{widget}",' in frontend
    assert "BroadcastChannel" in frontend
    assert "setTimeout(pollState, 2500)" in frontend
    assert "installCanvasInteraction" in frontend
    assert 'event.button !== 1' in frontend
    assert "stage.style.zoom = String(camera.scale)" in frontend
    assert "stage.style.transform" not in frontend
    assert 'details.style.setProperty("--qfw-pulse-duration"' in frontend
    assert 'details.style.setProperty("--qfw-pulse-delay"' in frontend
    assert "function captureWidgetScrollPositions()" in frontend
    assert "restoreWidgetScrollPositions(scrollPositions)" in frontend
    assert "function renderAlerts(payload)" in frontend
    assert 'element("pre", "qfw-alert-detail")' in frontend
    assert 'operationGroup("Cluster control", "cluster")' in frontend
    assert 'operationGroup("Service control", "services")' in frontend
    assert 'operationGroup("Node control", "nodes")' in frontend
    assert 'action: `service-${serviceAction.value}`' in frontend
    assert "/api/qfw-dashboard/operations/abort" in frontend
    assert 'element("pre", "qfw-operation-output")' in frontend
    assert "markup: rendered.outerHTML" in frontend

    popout = resources.files("qfw_slurm_dashboard").joinpath(
        "assets/widget.html"
    ).read_text(encoding="utf-8")
    assert "/assets/service/css/qfw-slurm-cluster.css" in popout
    assert "template.innerHTML = lastMarkup" in popout
    assert "JSON.stringify(lastPayload" not in popout
    assert "function hydrateWidget()" in popout
    assert 'widget !== "topology"' in popout
    assert "graph.style.width" in popout
    assert 'zoom?.addEventListener("change"' in popout
    assert 'class="qfw-widget-viewport"' in popout
    assert "function zoomAt(" in popout
    assert "popout_camera" in popout
    assert "event.button !== 1" in popout
    assert "output.style.zoom = String(camera.scale)" in popout
    assert "output.style.transform" not in popout
    assert "function captureMirrorScrollPositions()" in popout
    assert "restoreMirrorScrollPositions(scrollPositions)" in popout


def test_dashboard_uses_electroboy_pane_colors() -> None:
    stylesheet = resources.files("qfw_slurm_dashboard").joinpath(
        "assets/qfw-slurm-cluster.css"
    ).read_text(encoding="utf-8")

    assert "background: var(--terminal, #10141f);" in stylesheet
    assert "--qfw-cyan: #5ee8ff;" in stylesheet
    assert ".qfw-widget-window" in stylesheet
    assert ".qfw-control-group" in stylesheet
    assert ".qfw-topology-zoom" in stylesheet
    assert ".qfw-alert-detail" in stylesheet
    assert "white-space: pre;" in stylesheet
    assert "@keyframes qfw-telemetry-sweep" in stylesheet
    assert "@keyframes qfw-widget-ambient-pulse" in stylesheet
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet
    assert "background: var(--panel" not in stylesheet
    assert "overflow: hidden;" in stylesheet
    assert ".qfw-widget-chevron" in stylesheet
    assert "font: inherit;" in stylesheet
    assert ".qfw-dashboard select option" in stylesheet
    assert ".qfw-progress-tools select option" in stylesheet
    assert ".qfw-widget-window select option" in stylesheet
    assert "background: #0a2235;" in stylesheet
    assert ".qfw-operation-groups" in stylesheet
    assert ".qfw-operation-output" in stylesheet
