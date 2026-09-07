"""Transport-neutral HTTP routes for dashboard capabilities."""

from __future__ import annotations

from http import HTTPStatus
from importlib import resources
from pathlib import Path
from threading import Lock
from typing import Any

from electroboy.service.http import HtmlResponse, JsonResponse, ServiceResponse
from electroboy.service.registry import RouteDefinition
from electroboy.service.routes import RouteRequest

from .service import DashboardService
from .models import utc_now

_SERVICES: dict[tuple[Path, Path], DashboardService] = {}
_SERVICES_LOCK = Lock()


def route(
    method: str, path: str, handler_name: str, *, lease: bool = True
) -> RouteDefinition:
    return RouteDefinition(
        method,
        path,
        "qfw-slurm-cluster",
        handler_name,
        requires_workspace_lease=lease,
    )


def _service(request: RouteRequest) -> DashboardService:
    cluster_root = Path(request.config.root).resolve()
    state_root = Path(request.config.state_root).resolve()
    key = (cluster_root, state_root)
    with _SERVICES_LOCK:
        if key not in _SERVICES:
            _SERVICES[key] = DashboardService(cluster_root, state_root)
        return _SERVICES[key]


def _error(error: Exception) -> JsonResponse:
    if isinstance(error, PermissionError):
        status = HTTPStatus.FORBIDDEN
    elif isinstance(error, KeyError):
        status = HTTPStatus.NOT_FOUND
    elif isinstance(error, (TypeError, ValueError)):
        status = HTTPStatus.BAD_REQUEST
    else:
        status = HTTPStatus.CONFLICT
    return JsonResponse({
        "schema": "qfw-dashboard-error-v1",
        "outcome": "error",
        "timestamp": utc_now(),
        "error": {
            "type": error.__class__.__name__,
            "message": str(error),
        },
    }, status=status)


def _state(request: RouteRequest) -> JsonResponse:
    return JsonResponse(_service(request).state())


def _diagnostics(request: RouteRequest) -> JsonResponse:
    return JsonResponse(_service(request).diagnostic_state())


def _events(request: RouteRequest) -> JsonResponse:
    try:
        cursor = int((request.params.get("cursor") or ["0"])[0])
        limit = int((request.params.get("limit") or ["500"])[0])
        identity = str((request.params.get("identity") or [""])[0])
        return JsonResponse(_service(request).events(cursor, limit, identity))
    except Exception as error:
        return _error(error)


def _logs(request: RouteRequest) -> JsonResponse:
    try:
        return JsonResponse(_service(request).logs(
            str((request.params.get("source") or [""])[0]),
            int((request.params.get("cursor") or ["0"])[0]),
            int((request.params.get("limit") or ["500"])[0]),
            str((request.params.get("identity") or [""])[0]),
            str((request.params.get("instance") or [""])[0]),
        ))
    except Exception as error:
        return _error(error)


def _artifact(request: RouteRequest) -> JsonResponse:
    try:
        return JsonResponse(_service(request).artifact(
            str((request.params.get("experiment_id") or [""])[0]),
            int((request.params.get("index") or ["0"])[0]),
            str((request.params.get("identity") or [""])[0]),
        ))
    except Exception as error:
        return _error(error)


def _widget(request: RouteRequest) -> HtmlResponse:
    body = resources.files("qfw_slurm_dashboard").joinpath(
        "assets/widget.html"
    ).read_text(encoding="utf-8")
    return HtmlResponse(body)


def _operation(request: RouteRequest) -> JsonResponse:
    try:
        body = request.body()
        operation = _service(request).submit_action(
            str(body.get("action", "")),
            str(body.get("identity", "")),
            str(body.get("target", "cluster")),
            str(body.get("request_id", "")),
            str(body.get("reason", "qfw-dashboard")),
        )
        return JsonResponse(operation.payload(), status=HTTPStatus.ACCEPTED)
    except Exception as error:
        return _error(error)


def _abort_operation(request: RouteRequest) -> JsonResponse:
    try:
        body = request.body()
        operation = _service(request).abort_operation(
            str(body.get("operation_id", "")),
            str(body.get("identity", "")),
        )
        return JsonResponse(operation.payload(), status=HTTPStatus.ACCEPTED)
    except Exception as error:
        return _error(error)


def _preview(request: RouteRequest) -> JsonResponse:
    try:
        return JsonResponse({
            "schema": "qfw-dashboard-preview-v1",
            "outcome": "success",
            "command": _service(request).command_preview(request.body()),
        })
    except Exception as error:
        return _error(error)


def _experiment(request: RouteRequest) -> JsonResponse:
    try:
        experiment = _service(request).submit_experiment(request.body())
        return JsonResponse(experiment.payload(), status=HTTPStatus.ACCEPTED)
    except Exception as error:
        return _error(error)


def _cancel(request: RouteRequest) -> JsonResponse:
    try:
        body = request.body()
        _service(request).cancel_experiment(
            str(body.get("experiment_id", "")), str(body.get("identity", ""))
        )
        return JsonResponse({
            "schema": "qfw-dashboard-cancel-v1",
            "outcome": "success",
            "status": "cancel-requested",
        })
    except Exception as error:
        return _error(error)


def _retry(request: RouteRequest) -> JsonResponse:
    try:
        body = request.body()
        experiment = _service(request).retry_experiment(
            str(body.get("experiment_id", "")),
            str(body.get("identity", "")),
            body.get("submit_real_hardware") is True,
        )
        return JsonResponse(experiment.payload(), status=HTTPStatus.ACCEPTED)
    except Exception as error:
        return _error(error)


def _compare(request: RouteRequest) -> JsonResponse:
    try:
        body = request.body()
        return JsonResponse(_service(request).compare_results(
            [str(item) for item in body.get("experiment_ids", [])],
            str(body.get("identity", "")),
        ))
    except Exception as error:
        return _error(error)


def _shell(request: RouteRequest) -> JsonResponse:
    try:
        body = request.body()
        identity = str(body.get("identity", ""))
        target = str(body.get("target", "slurmctld"))
        context = _service(request).shell_context(identity, target)
        home = context["home"]
        session, _ = request.services.sessions.start_project_shell(
            request.context_id
        )
        command = (
            f"exec docker exec -it --user {identity} --workdir {home} "
            f"--env HOME={home} --env USER={identity} --env LOGNAME={identity} "
            f"{target} /bin/bash -lc 'printf \"QFw cluster shell: "
            f"user={identity} host={target} cwd={home} "
            "cluster=QFw-SLURM-Cluster\\n\"; exec /bin/bash -l'\r"
        )
        request.services.sessions.send_project_shell_input(
            request.context_id, command, session.session_id
        )
        _service(request).store.audit({
            "identity": identity,
            "action": "shell-start",
            "target": target,
            "request_id": session.session_id,
            "outcome": "started",
        })
        return JsonResponse({
            "schema": "qfw-dashboard-shell-v1",
            "outcome": "success",
            "status": "started",
            "identity": identity,
            "target": target,
            "shell_session": session.payload(selected=False),
        }, status=HTTPStatus.ACCEPTED)
    except Exception as error:
        return _error(error)


ROUTES = (
    route("GET", "/api/qfw-dashboard/state", "state", lease=False),
    route("GET", "/api/qfw-dashboard/diagnostics", "diagnostics", lease=False),
    route("GET", "/api/qfw-dashboard/events", "events", lease=False),
    route("GET", "/api/qfw-dashboard/logs", "logs", lease=False),
    route("GET", "/api/qfw-dashboard/artifact", "artifact", lease=False),
    route("GET", "/qfw-dashboard/widget", "widget", lease=False),
    route("POST", "/api/qfw-dashboard/operations", "operation"),
    route("POST", "/api/qfw-dashboard/operations/abort", "abort-operation"),
    route("POST", "/api/qfw-dashboard/preview", "preview"),
    route("POST", "/api/qfw-dashboard/experiments", "experiment"),
    route("POST", "/api/qfw-dashboard/experiments/cancel", "cancel"),
    route("POST", "/api/qfw-dashboard/experiments/retry", "retry"),
    route("POST", "/api/qfw-dashboard/results/compare", "compare"),
    route("POST", "/api/qfw-dashboard/shell", "shell"),
)

HANDLERS: dict[str, Any] = {
    "state": _state,
    "diagnostics": _diagnostics,
    "events": _events,
    "logs": _logs,
    "artifact": _artifact,
    "widget": _widget,
    "operation": _operation,
    "abort-operation": _abort_operation,
    "preview": _preview,
    "experiment": _experiment,
    "cancel": _cancel,
    "retry": _retry,
    "compare": _compare,
    "shell": _shell,
}
