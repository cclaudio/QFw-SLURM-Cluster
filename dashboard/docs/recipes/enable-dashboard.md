# Enable the Cluster Dashboard

The dashboard is opt-in and runs on the Docker host. From a recursive clone:

```bash
cd /path/to/QFw-SLURM-Cluster
dashboard/enable.sh --dry-run
dashboard/enable.sh
source dashboard/run/environment
qfw-dashboard-service start
```

For an ordinary clone, the enable command initializes only the pinned
`dashboard/external/electroboy` submodule. It installs ElectroBoy core,
reusable modules, and the cluster workflow in `dashboard/.venv`. Software and
creative-writing workflows are not installed or enabled.

The service listens on `127.0.0.1:8765` by default. Select an unused loopback
port when necessary:

```bash
dashboard/enable.sh --port 18765
source dashboard/run/environment
qfw-dashboard-service start
```

Run `man qfw-dashboard-enable` and `man qfw-dashboard-service` for details.
