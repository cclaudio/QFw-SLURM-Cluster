#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command="${script_dir}/tools/qfw-site-services"
temporary="$(mktemp -d)"
trap 'rm -rf "${temporary}"' EXIT

grep -q '/etc/openqse/qfw-slurm/gateway.yaml' "${command}"
grep -q '/etc/openqse/qfw-slurm/plugin.conf' \
	"${script_dir}/config/qfw-slurm/plugstack.conf"
grep -q '/etc/openqse/qfw-slurm/plugin.conf' \
	"${script_dir}/config/qfw-slurm/burst-buffer.lua.conf"
if grep -R -q '/etc/qfw-slurm' \
	"${script_dir}/Dockerfile" \
	"${script_dir}/config/qfw-slurm" \
	"${script_dir}/tools/qfw-site-services"; then
	echo "retired qfw-slurm configuration root remains" >&2
	exit 1
fi

"${command}" --dry-run start >"${temporary}/start.out"
grep -q '^slurmctld: qfw-dir-svc start ' "${temporary}/start.out"
grep -q '^nwqsim-head: qfw-qpm-svc start ' "${temporary}/start.out"
grep -q '^iqm-head: qfw-qpm-svc start ' "${temporary}/start.out"
grep -q '^slurmctld: qfw gateway start$' "${temporary}/start.out"
grep -q 'nwqsim-head,nwqsim-worker-1,nwqsim-worker-2' \
	"${temporary}/start.out"
grep -q 'QFw site services are ready' "${temporary}/start.out"

"${command}" --dry-run status >"${temporary}/status.out"
grep -q 'Directory service (slurmctld)' "${temporary}/status.out"
grep -q 'NWQSim QPM (nwqsim-head)' "${temporary}/status.out"
grep -q 'IQM QPM (iqm-head)' "${temporary}/status.out"
grep -q 'QFw Slurm gateway (slurmctld:18095)' "${temporary}/status.out"

for target in directory nwqsim iqm gateway; do
	"${command}" --dry-run start --target "${target}" \
		>"${temporary}/start-${target}.out"
	"${command}" --dry-run status --target "${target}" \
		>"${temporary}/status-${target}.out"
	"${command}" --dry-run restart --target "${target}" \
		>"${temporary}/restart-${target}.out"
	"${command}" --dry-run recover --target "${target}" \
		>"${temporary}/recover-${target}.out"
done
grep -q '^slurmctld: qfw-dir-svc start ' \
	"${temporary}/start-directory.out"
grep -q '^nwqsim-head: qfw-qpm-svc start ' \
	"${temporary}/start-nwqsim.out"
grep -q '^iqm-head: qfw-qpm-svc start ' \
	"${temporary}/start-iqm.out"
grep -q '^slurmctld: qfw gateway start$' \
	"${temporary}/start-gateway.out"
if "${command}" --dry-run status --target missing >/dev/null 2>&1; then
	echo "unknown target unexpectedly succeeded" >&2
	exit 1
fi

"${command}" --dry-run stop >"${temporary}/stop.out"
gateway_line="$(grep -n 'qfw gateway stop$' "${temporary}/stop.out" | cut -d: -f1)"
iqm_line="$(grep -n '^iqm-head: qfw-qpm-svc stop ' "${temporary}/stop.out" | cut -d: -f1)"
nwqsim_line="$(grep -n '^nwqsim-head: qfw-qpm-svc stop ' "${temporary}/stop.out" | cut -d: -f1)"
directory_line="$(grep -n '^slurmctld: qfw-dir-svc stop ' "${temporary}/stop.out" | cut -d: -f1)"
[[ "${gateway_line}" -lt "${iqm_line}" ]]
[[ "${iqm_line}" -lt "${nwqsim_line}" ]]
[[ "${nwqsim_line}" -lt "${directory_line}" ]]

(
	source "${command}"
	dry_run=false
	directory_ready() { return 0; }
	nwqsim_ready() { return 0; }
	iqm_ready() { return 0; }
	gateway_managed_ready() { return 0; }
	run_qfw() { return 99; }
	run_gateway() { return 99; }
	start_services
) >"${temporary}/already-ready.out"
grep -q 'Directory service is already ready' "${temporary}/already-ready.out"
grep -q 'NWQSim QPM is already ready' "${temporary}/already-ready.out"
grep -q 'IQM QPM is already ready' "${temporary}/already-ready.out"
grep -q 'QFw Slurm gateway is already ready' "${temporary}/already-ready.out"

(
	source "${command}"
	dry_run=false
	directory_up=false
	nwqsim_up=true
	iqm_up=true
	gateway_up=true
	directory_ready() { ${directory_up}; }
	nwqsim_ready() { ${nwqsim_up}; }
	iqm_ready() { ${iqm_up}; }
	gateway_managed_ready() { ${gateway_up}; }
	stop_gateway() { echo stop-gateway; gateway_up=false; }
	stop_iqm() { echo stop-iqm; iqm_up=false; }
	stop_nwqsim() { echo stop-nwqsim; nwqsim_up=false; }
	start_directory() { echo start-directory; directory_up=true; }
	start_nwqsim() { echo start-nwqsim; nwqsim_up=true; }
	start_iqm() { echo start-iqm; iqm_up=true; }
	start_gateway() { echo start-gateway; gateway_up=true; }
	stop_directory() { echo stop-directory; directory_up=false; }
	start_services
) >"${temporary}/partial-state.out"
sed '/QFw site services are ready/d' "${temporary}/partial-state.out" \
	>"${temporary}/partial-state.events"
cat >"${temporary}/partial-state.expected" <<'EOF'
stop-gateway
stop-iqm
stop-nwqsim
start-directory
start-nwqsim
start-iqm
start-gateway
EOF
cmp "${temporary}/partial-state.expected" "${temporary}/partial-state.events"

: >"${temporary}/rollback.events"
(
	source "${command}"
	dry_run=false
	directory_up=true
	nwqsim_up=false
	directory_ready() { ${directory_up}; }
	nwqsim_ready() { ${nwqsim_up}; }
	iqm_ready() { return 1; }
	gateway_managed_ready() { return 1; }
	start_directory() { return 0; }
	start_nwqsim() {
		echo start-nwqsim >>"${temporary}/rollback.events"
		nwqsim_up=true
	}
	start_iqm() {
		echo fail-iqm >>"${temporary}/rollback.events"
		return 1
	}
	stop_nwqsim() {
		echo stop-nwqsim >>"${temporary}/rollback.events"
		nwqsim_up=false
	}
	stop_directory() {
		echo stop-directory >>"${temporary}/rollback.events"
		directory_up=false
	}
	! start_services
)
grep -q '^start-nwqsim$' "${temporary}/rollback.events"
grep -q '^fail-iqm$' "${temporary}/rollback.events"
grep -q '^stop-nwqsim$' "${temporary}/rollback.events"
if grep -q '^stop-directory$' "${temporary}/rollback.events"; then
	echo "rollback stopped a pre-existing directory" >&2
	exit 1
fi

echo "qfw-site-services dry-run lifecycle passed"
