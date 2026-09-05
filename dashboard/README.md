# QFw Slurm Cluster Dashboard

This optional ElectroBoy workflow operates and observes the virtual QFw Slurm
cluster. It is not part of the normal cluster configure, build, or startup
path. The cluster remains completely usable when this directory's generated
environment and state do not exist.

Enable and start it from the repository root:

```bash
dashboard/enable.sh
source dashboard/run/environment
qfw-dashboard-service start
```

Open the loopback URL printed by the start command. The workflow exposes only
Dashboard, Progress, File, and Shell panes. `user-a` is selected initially.
Choose `root` explicitly before invoking host or site-service mutations.

Use `man qfw-dashboard`, `man qfw-dashboard-enable`,
`man qfw-dashboard-service`, and `man qfw-dashboard-disable` after sourcing
the generated environment. Start with [the recipes](docs/recipes/README.md)
for end-to-end procedures.

Generated files are confined to `.venv/`, `run/`, and `state/` beneath this
directory. `dashboard/disable.sh` stops the browser service and preserves
them. `dashboard/disable.sh --purge` removes only those generated dashboard
files; it never changes cluster state.
