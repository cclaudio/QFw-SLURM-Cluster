# Start the Site-owned NWQSim QPM

Use this debugging recipe when only the directory service and persistent
NWQSim QPMd are needed. For ordinary operation, prefer
[Start all site services](start-all-site-services.md).

## Start the Directory Service

From the Docker host, enter `slurmctld` as root:

```bash
cd /path/to/QFw-SLURM-Cluster
./do_ssh.sh
```

Inside `slurmctld`:

```bash
qfw-site-services start --target directory
qfw-site-services status --target directory
```

Run `man 8 qfw-site-services` for target selection and lifecycle details. The
manager writes the client-readable connection record beneath
`${QFW_SHARED_ROOT}`.

## Start NWQSim

Remain on `slurmctld` and run:

```bash
qfw-site-services start --target nwqsim
qfw-site-services status --target nwqsim
```

The manager executes the lower-level QFw lifecycle on `nwqsim-head` and loads
the declared libfabric, Open MPI, and NWQSim modules automatically.

## Stop NWQSim

On `slurmctld`:

```bash
qfw-site-services stop --target nwqsim
```

Stop the directory separately only when no other QPMd uses it.
