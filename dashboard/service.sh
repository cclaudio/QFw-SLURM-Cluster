#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
dashboard_dir="$(cd "$(dirname "${script_path}")" && pwd)"
env_file="${dashboard_dir}/run/environment"
pid_file="${dashboard_dir}/run/electroboy.pid"
log_file="${dashboard_dir}/run/electroboy.log"

usage() {
    echo "Usage: dashboard/service.sh {start|stop|restart|status|logs}"
}

load_environment() {
    if [[ ! -f "${env_file}" || ! -x "${dashboard_dir}/.venv/bin/electroboy" ]]; then
        echo "qfw-dashboard-service: run dashboard/enable.sh first" >&2
        exit 1
    fi
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
}

running_pid() {
    [[ -f "${pid_file}" ]] || return 1
    local pid
    pid="$(<"${pid_file}")"
    [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

command="${1:-}"
case "${command}" in
    start)
        load_environment
        if running_pid; then
            echo "dashboard already running with PID $(<"${pid_file}")"
            exit 0
        fi
        mkdir -p "${dashboard_dir}/run"
        setsid env -u PYTHONPATH "${dashboard_dir}/.venv/bin/electroboy" serve \
            --root "${ELECTROBOY_SERVICE_ROOT}" \
            --state-root "${ELECTROBOY_SERVICE_STATE_ROOT}" \
            --host "${ELECTROBOY_SERVICE_HOST}" \
            --port "${ELECTROBOY_SERVICE_PORT}" \
            < /dev/null \
            >>"${log_file}" 2>&1 &
        echo "$!" >"${pid_file}"
        sleep 1
        running_pid || { tail -n 40 "${log_file}" >&2; exit 1; }
        echo "dashboard listening at http://${ELECTROBOY_SERVICE_HOST}:${ELECTROBOY_SERVICE_PORT}"
        ;;
    stop)
        if running_pid; then
            pid="$(<"${pid_file}")"
            kill "${pid}"
            for _ in {1..50}; do
                kill -0 "${pid}" 2>/dev/null || break
                sleep 0.1
            done
            kill -0 "${pid}" 2>/dev/null && kill -KILL "${pid}"
        fi
        rm -f -- "${pid_file}"
        echo "dashboard stopped"
        ;;
    restart)
        "${script_path}" stop
        "${script_path}" start
        ;;
    status)
        if running_pid; then
            echo "running PID $(<"${pid_file}")"
            exit 0
        fi
        echo "stopped"
        exit 1
        ;;
    logs)
        touch "${log_file}"
        tail -n 200 -f "${log_file}"
        ;;
    -h|--help) usage ;;
    *) usage >&2; exit 2 ;;
esac
