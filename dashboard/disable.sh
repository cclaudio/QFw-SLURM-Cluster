#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
dashboard_dir="$(cd "$(dirname "${script_path}")" && pwd)"
purge=0

if [[ "${1:-}" == "--purge" ]]; then
    purge=1
    shift
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    echo "Usage: dashboard/disable.sh [--purge]"
    exit 0
fi
if (($#)); then
    echo "qfw-dashboard-disable: unexpected argument: $1" >&2
    exit 2
fi

"${dashboard_dir}/service.sh" stop || true
if ((purge)); then
    for target in "${dashboard_dir}/.venv" "${dashboard_dir}/run" \
                  "${dashboard_dir}/state"; do
        [[ "${target}" == "${dashboard_dir}/"* ]] || exit 1
        rm -rf -- "${target}"
    done
    echo "dashboard installation and state removed; cluster state unchanged"
else
    echo "dashboard disabled; installation and state preserved"
fi
