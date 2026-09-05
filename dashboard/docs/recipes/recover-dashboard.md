# Recover the Cluster Dashboard

Inspect the browser service without changing cluster state:

```bash
cd /path/to/QFw-SLURM-Cluster
source dashboard/run/environment
qfw-dashboard-service status
qfw-dashboard-service logs
qfw-dashboard diagnostics
```

Restart only the dashboard after a browser-service failure:

```bash
qfw-dashboard-service restart
```

Re-run enablement after updating the recorded ElectroBoy submodule pointer or
workflow source. It preserves dashboard service state.

```bash
dashboard/enable.sh
```

Disable without deleting state:

```bash
qfw-dashboard-disable
```

Use `qfw-dashboard-disable --purge` only when the generated dashboard virtual
environment, runtime files, and saved dashboard state should be removed. This
does not stop, rebuild, or alter the Slurm cluster or QFw services.

Run `man qfw-dashboard-disable` for the exact cleanup boundary.
