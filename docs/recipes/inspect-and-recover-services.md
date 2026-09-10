# Inspect and Recover QFw Services

Use this recipe when an allocation is pending, a QPM appears unavailable, or
a previous service lifecycle did not finish cleanly.

## Inspect Slurm

Enter `slurmctld` as root:

```bash
cd /path/to/QFw-SLURM-Cluster
./do_ssh.sh
sinfo -N -o '%N %P %t %f %c'
squeue -o '%i %u %T %R %N'
```

Application nodes should be in `normal`. The four dedicated service nodes
should be in `qfw-services`, not allocated to application jobs.

## Inspect the Service Plane

```bash
qfw-site-services status
```

Run `man 8 qfw-site-services` for state and exit-status details. Inspect one
managed target from `slurmctld` with:

```bash
qfw-site-services status --target directory
qfw-site-services status --target nwqsim
qfw-site-services status --target iqm
qfw-site-services status --target gateway
```

The site manager performs remote-node placement. Administrators do not invoke
the lower-level QFw lifecycle commands directly.

## Inspect as an Application User

```bash
./do_ssh.sh --user user-a
source "${QFW_INSTALL_PREFIX}/bin/qfw-activate" \
  --venv "${QFW_VENV}"

qfw-sinfo
qfw-sinfo nwqsim-head
qfw-sinfo iqm-head
qfw-squeue

qfw-deactivate
```

Run `man 1 qfw-sinfo` and `man 1 qfw-squeue` for JSON output and filtering.
These commands expose sanitized service and allocation state, not provider
credentials or other users' reservation IDs.

## Recover the Complete Service Plane

Do not delete lifecycle files by hand. The managers distinguish ready,
starting, stopped, and stale state and preserve useful diagnostics.

From `slurmctld` as root:

```bash
qfw-site-services recover --target all
qfw-site-services status
```

If startup still fails, inspect the manager logs beneath the corresponding
run directory:

```text
/var/lib/qfw-site-services/directory/services/qfw-site-dirsvc/logs
/var/lib/qfw-site-services/qpm/nwqsim/services/nwqsim/logs
/var/lib/qfw-site-services/qpm/iqm-ornl-20q/services/iqm-ornl-20q/logs
/var/log/qfw-slurm-gateway/gateway.log
```

For a pending job, inspect `scontrol show job <job-id>` and the gateway log at
`/var/log/qfw-slurm-gateway/gateway.log`. `BurstBufferResources` is expected
while Slurm polls a delayed preliminary evaluation. Cancel the job with
`scancel <job-id>` when the user no longer wants the allocation; the teardown
path attempts reservation release.
