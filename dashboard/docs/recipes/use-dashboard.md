# Use the Cluster Dashboard

Start the service and open its reported URL:

```bash
cd /path/to/QFw-SLURM-Cluster
source dashboard/run/environment
qfw-dashboard-service start
qfw-dashboard status
```

The Dashboard pane shows fixed health, node, service, allocation, experiment,
topology, result, and alert widgets. Collapse a widget to reduce its footprint
or select **Pop out** to mirror it in another browser window. The original
widget remains in place.

Choose `user-a`, `user-b`, or `user-c` before submitting ordinary experiments.
The selected identity owns the Slurm job and its files. Choose `root` only for
cluster lifecycle, service lifecycle, node recovery, or a root shell. An
identity change applies only to newly submitted work.

Split the workspace to place Progress, File, or Shell beside Dashboard. Use
Progress filters in component-first order. File browses this checkout through
ElectroBoy's Markdown capability. **Open selected cluster shell** starts the
Shell pane in `slurmctld` as the identity selected when the shell was created.

Real IQM submissions require a separate confirmation and bounded shot count.
Credentials never appear in dashboard forms, retained events, or results.

Run `man qfw-dashboard` for CLI status and diagnostics commands.
