#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
dashboard_dir="$(cd "$(dirname "${script_path}")" && pwd)"
cluster_root="$(cd "${dashboard_dir}/.." && pwd)"
electroboy_dir="${dashboard_dir}/external/electroboy"
venv_dir="${dashboard_dir}/.venv"
state_root="${dashboard_dir}/state"
host="127.0.0.1"
port="8765"
dry_run=0

usage() {
    cat <<'EOF'
Usage: dashboard/enable.sh [OPTIONS]

Create or refresh the optional QFw Slurm cluster dashboard installation.

Options:
  --dry-run       Print resolved revisions, paths, and actions without changes.
  --host ADDRESS  Listener address written to the dashboard environment.
                  The default is 127.0.0.1.
  --port PORT     Listener port. The default is 8765.
  -h, --help      Show this help.
EOF
}

while (($#)); do
    case "$1" in
        --dry-run) dry_run=1 ;;
        --host) host="${2:?missing value for --host}"; shift ;;
        --port) port="${2:?missing value for --port}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "qfw-dashboard-enable: unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [[ ! "${port}" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
    echo "qfw-dashboard-enable: invalid port: ${port}" >&2
    exit 2
fi
if [[ "${host}" != "127.0.0.1" && "${host}" != "localhost" ]]; then
    echo "qfw-dashboard-enable: this release permits only loopback binding;" \
         "use an authenticated reverse proxy for remote access" >&2
    exit 2
fi

electroboy_revision="$(git -C "${cluster_root}" ls-tree HEAD \
    dashboard/external/electroboy 2>/dev/null | awk '{print $3}')"
cluster_revision="$(git -C "${cluster_root}" rev-parse HEAD)"

cat <<EOF
cluster root: ${cluster_root}
cluster revision: ${cluster_revision}
ElectroBoy source: ${electroboy_dir}
ElectroBoy revision: ${electroboy_revision:-unrecorded}
virtual environment: ${venv_dir}
service state: ${state_root}
listener: ${host}:${port}
workflow: qfw-slurm-cluster
EOF

if ((dry_run)); then
    cat <<EOF
would initialize the pinned ElectroBoy submodule if absent
would install electroboy-core, electroboy-modules, and this workflow
would register only qfw-slurm-cluster
EOF
    exit 0
fi

if [[ -z "${electroboy_revision}" ]]; then
    echo "qfw-dashboard-enable: dashboard submodule is not recorded" >&2
    exit 1
fi
if [[ ! -f "${electroboy_dir}/packages/electroboy-core/pyproject.toml" ]]; then
    git -C "${cluster_root}" submodule update --init -- \
        dashboard/external/electroboy
fi
actual_revision="$(git -C "${electroboy_dir}" rev-parse HEAD)"
if [[ "${actual_revision}" != "${electroboy_revision}" ]]; then
    echo "qfw-dashboard-enable: ElectroBoy checkout does not match gitlink" >&2
    exit 1
fi

python3 -m venv "${venv_dir}"
env -u PYTHONPATH "${venv_dir}/bin/python" -m pip install \
    --disable-pip-version-check --force-reinstall \
    "${electroboy_dir}/packages/electroboy-core" \
    "${electroboy_dir}/packages/electroboy-modules" \
    "${dashboard_dir}"

mkdir -p "${state_root}/.electroboy/service" "${dashboard_dir}/run"
chmod 700 "${state_root}" "${dashboard_dir}/run"
ln -sfn "${dashboard_dir}/enable.sh" \
    "${venv_dir}/bin/qfw-dashboard-enable"
ln -sfn "${dashboard_dir}/disable.sh" \
    "${venv_dir}/bin/qfw-dashboard-disable"
ln -sfn "${dashboard_dir}/service.sh" \
    "${venv_dir}/bin/qfw-dashboard-service"
python3 - "${state_root}/.electroboy/service/workflows.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "enabled_builtins": [],
    "extra_workflows": [
        {
            "id": "qfw-slurm-cluster",
            "factory": "entry-point:qfw-slurm-cluster",
        }
    ],
}
path.write_text(json.dumps(payload, indent=2) + "\n")
PY

cat >"${dashboard_dir}/run/environment" <<EOF
export ELECTROBOY_SERVICE_ROOT=${cluster_root}
export ELECTROBOY_SERVICE_STATE_ROOT=${state_root}
export ELECTROBOY_SERVICE_HOST=${host}
export ELECTROBOY_SERVICE_PORT=${port}
export QFW_CLUSTER_ROOT=${cluster_root}
export QFW_DASHBOARD_STATE_ROOT=${state_root}
export PATH=${venv_dir}/bin:${PATH}
export MANPATH=${dashboard_dir}/man:${MANPATH:-}
EOF

env -u PYTHONPATH "${venv_dir}/bin/python" - "${cluster_revision}" \
    "${actual_revision}" \
    "${venv_dir}" "${state_root}" >"${dashboard_dir}/run/installation.json" <<'PY'
import importlib.metadata
import json
import pathlib
import platform
import sys

workflows = sorted(
    item.name
    for item in importlib.metadata.entry_points(group="electroboy.workflows")
)
if workflows != ["qfw-slurm-cluster"]:
    raise SystemExit(f"unexpected ElectroBoy workflows: {workflows}")
print(json.dumps({
    "schema": "qfw-dashboard-install-v1",
    "cluster_revision": sys.argv[1],
    "electroboy_revision": sys.argv[2],
    "python": platform.python_version(),
    "interpreter": sys.executable,
    "virtual_environment": str(pathlib.Path(sys.argv[3]).resolve()),
    "service_state": str(pathlib.Path(sys.argv[4]).resolve()),
    "workflows": workflows,
}, indent=2, sort_keys=True))
PY

echo "dashboard enabled; run ${dashboard_dir}/service.sh start"
