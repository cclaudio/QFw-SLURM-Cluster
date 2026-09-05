"""Command-line access to dashboard capabilities for CI and diagnostics."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .runner import IDENTITIES
from .service import DashboardService


def _service(args: argparse.Namespace) -> DashboardService:
    cluster_root = Path(args.cluster_root).resolve()
    state_root = Path(args.state_root).resolve()
    return DashboardService(cluster_root, state_root)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="qfw-dashboard")
    value.add_argument(
        "--cluster-root",
        default=os.environ.get("QFW_CLUSTER_ROOT", str(Path.cwd())),
    )
    value.add_argument(
        "--state-root",
        default=os.environ.get(
            "QFW_DASHBOARD_STATE_ROOT", str(Path.cwd() / "dashboard" / "state")
        ),
    )
    commands = value.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("diagnostics")
    events = commands.add_parser("events")
    events.add_argument("--cursor", type=int, default=0)
    events.add_argument("--limit", type=int, default=500)
    action = commands.add_parser("action")
    action.add_argument("action")
    action.add_argument("--identity", choices=IDENTITIES, default="user-a")
    action.add_argument("--target", default="cluster")
    action.add_argument("--request-id", default="")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    service = _service(args)
    if args.command == "status":
        payload = service.state()
    elif args.command == "diagnostics":
        payload = service.diagnostic_state()
    elif args.command == "events":
        payload = service.events(args.cursor, args.limit)
    else:
        payload = service.submit_action(
            args.action, args.identity, args.target, args.request_id
        ).payload()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
