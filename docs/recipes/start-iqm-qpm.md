# Start the Site-owned IQM QPM

Use this debugging recipe when only the directory service and persistent IQM
QPMd are needed. For ordinary operation, prefer
[Start all site services](start-all-site-services.md).

## Prepare Credentials

Before accepting hardware reservations, use the site's secret-management
workflow to populate this protected file on `iqm-head`:

```text
/etc/openqse/qfw/device/qpu-users.json
```

The records for `user-a`, `user-b`, and `user-c` must remain enabled and have
a non-empty `api_key` for `ornl-iqm-20q`. Keep the file owned by `root:root`
with mode `0600`. Do not print the file while validating it.

## Start the Directory Service

Enter `slurmctld` as root from the Docker host:

```bash
cd /path/to/QFw-SLURM-Cluster
./do_ssh.sh
```

Inside `slurmctld`:

```bash
qfw-site-services start --target directory
qfw-site-services status --target directory
```

Run `man 8 qfw-site-services` for target selection and lifecycle details.

## Start IQM

Remain on `slurmctld` and run:

```bash
qfw-site-services start --target iqm
qfw-site-services status --target iqm
```

Credentials remain on `iqm-head`; neither startup nor reservation exports them
to application nodes.

## Stop IQM

On `slurmctld`:

```bash
qfw-site-services stop --target iqm
```

Stop the directory separately only when no other QPMd uses it.
