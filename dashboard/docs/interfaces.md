# Dashboard Interface Ownership

The dashboard composes existing cluster interfaces. It does not own Slurm
scheduling, QPM admission, QFw discovery, provider access, or Docker lifecycle
policy.

| Capability | Owner | Dashboard adapter | Requirements |
| --- | --- | --- | --- |
| Container state | Docker Compose | `collectors.docker_status()` | DASH-010–014 |
| Nodes and jobs | Slurm | `collectors.slurm_status()` | DASH-012–016 |
| QPM catalog | `qfw-sinfo` | `collectors.service_status()` | DASH-021–026 |
| QPM reservations | `qfw-squeue` | `collectors.allocation_status()` | DASH-031–036 |
| Cluster lifecycle | Cluster root scripts | `DashboardService.submit_action()` | DASH-041–048 |
| Service lifecycle | `qfw-site-services` | `DashboardService.submit_action()` | DASH-051–058 |
| Node administration | Slurm `scontrol` | `DashboardService.submit_action()` | DASH-061–068 |
| Experiment execution | Slurm and QFw examples | `DashboardService.submit_experiment()` | DASH-071–088 |
| Durable UI state | Dashboard package | `DashboardStore` | DASH-101–108 |
| File and Markdown | ElectroBoy modules | Public File capability | DASH-183–187 |
| Shell | ElectroBoy module | Public Shell capability | DASH-188 |
| Pane workspace | ElectroBoy core | Workflow pane catalog | DASH-177–193 |
| Widget windows | Dashboard workflow | BroadcastChannel mirror | DASH-230–241 |

## Routes

| Method and path | Purpose | Mutation |
| --- | --- | --- |
| `GET /api/qfw-dashboard/state` | Normalized cluster snapshot | No |
| `GET /api/qfw-dashboard/diagnostics` | Read-only preflight checks | No |
| `GET /api/qfw-dashboard/events` | Bounded cursor-based events | No |
| `GET /qfw-dashboard/widget` | Same-origin widget window | No |
| `POST /api/qfw-dashboard/operations` | Allow-listed lifecycle action | Yes |
| `POST /api/qfw-dashboard/preview` | Allocation command preview | No |
| `POST /api/qfw-dashboard/experiments` | Slurm experiment submission | Yes |
| `POST /api/qfw-dashboard/experiments/cancel` | Slurm cancellation | Yes |

Every mutation is handled by a typed service method. Browser code never owns
the underlying policy. The JSON contract is
[`dashboard-v1.schema.json`](../schemas/dashboard-v1.schema.json).

## Correlation identifiers

`experiment_id` is the dashboard-level identity. `slurm_job_id` links it to
Slurm. Reservation tuples link a job to `service_id` and `reservation_id`.
QPM `runtime_id` and `generation` distinguish service incarnations. Every
operation has an `operation_id`; callers may supply a stable `request_id` for
idempotency. Events retain any identifiers known at collection time.

## Structured adapter boundary

Docker Compose already emits JSON. `qfw-sinfo` and `qfw-squeue` emit JSON.
Slurm's stable formatted fields are normalized into records because the
installed Slurm version does not consistently provide its JSON plugin in all
cluster images. `qfw-site-services` is invoked only for mutation. Its output is
retained as an operation log rather than parsed as state.
