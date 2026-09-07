#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command="${script_dir}/tools/qfw-site-services"
temporary="$(mktemp -d)"
trap 'rm -rf "${temporary}"' EXIT

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

echo "qfw-site-services dry-run lifecycle passed"
