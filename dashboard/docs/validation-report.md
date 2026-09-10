# Dashboard Validation Report

## Validated baseline

Validation used the `release/v0.1` cluster and the image below.

| Component | Revision or identifier |
| --- | --- |
| QFw-SLURM-Cluster baseline | `c6085ce42a269164a962e8d2ee0266faa650e0bd` |
| ElectroBoy | `ac1c16f6bdc92fc2a5a9997d3dc4c8ccebb28d2e` |
| QFw in the image | `8851b0b748c3ac611aa5a503ed96870b59fbad52` |
| qfw-slurm in the image | `3e17d5baef459c084a89baf7675e086b18847393` |
| Cluster image | `sha256:22d8adcc3930e1311874d7b8a8e368ef7ee04a9688d63eff034fd53779c75af7` |

The final dashboard commits appear after the baseline named above. A fresh
cluster image build completed from the pinned upstream QFw and qfw-slurm
revisions. The dashboard remained a host-side opt-in component and did not
enter the cluster image.

## Automated validation

The dashboard unit suite covered records, command construction, identity
execution, collectors, partial failures, service dependency guards, bounded
logs, redaction, persistence, result classification, artifact access, and
workflow registration. All tests passed.

ElectroBoy's core service, interface, and distribution-boundary suites passed
against the workflow-neutral pane-catalog extension. The headless browser test
rendered the cluster workflow, its exact Dashboard, Progress, File, and Shell
pane catalog, and the eight fixed dashboard widgets. Software and creative
writing workflows were absent.

A Chrome DevTools interaction test opened all eight widget windows. Changes to
each external filter appeared in the dashboard through the shared channel.
Closing each external window preserved its original widget and presentation
state.

Enablement was repeated against the pinned submodule. The service started on a
selected loopback port, returned a ready aggregate API response, and stopped
without changing the cluster. A port already used by another ElectroBoy
instance was rejected without adopting or stopping that process.

## Live cluster validation

The ready-state aggregate contained 16 Docker containers, 14 Slurm controller,
database, and node records, four directory-discovered services, five
service-plane components, and the complete sanitized version inventory. Every
diagnostic passed, including MUNGE, clock skew, four-CPU topology, shared
mounts, modules, directory publication, service registration, the DVM URI,
gateway connectivity, and IQM credential readiness.

| Case | Result | Evidence |
| --- | --- | --- |
| Normal NWQSim | Passed | Job 6 produced a successful terminal QFw record. |
| Heterogeneous NWQSim | Passed | Job 9 used one reservation set and completed. |
| Cancellation | Passed | Job 11 reached `CANCELLED`; qfw-slurm released it. |
| QPM restart and reuse | Passed | NWQSim restarted, then job 14 used reservation 6. |
| Complete service cycle | Passed | Dashboard stop and start restored every service. |
| Concurrent users | Passed | Jobs 15, 16, and 17 passed for users a, b, and c. |
| Classical-resource wait | Passed | Job 26 entered pending state, then completed with reservation 10. |
| Real IQM chemistry | Passed | Job 13 ran 5 qubits and 16 shots, with terminal status `ok`. |
| Node drain and resume | Passed | Node `c8` returned to `idle` after both actions. |

The real-IQM test reused the protected credential configuration already
installed on `iqm-head`. No credential was copied into the dashboard, command
line, retained experiment, log response, or report.

The first chemistry dashboard attempt stopped before provider execution because
the wrapper lacked `QFW_CHEM_APP_DIR`. The command generator now supplies both
the absolute application and its directory. The bounded rerun passed. This
regression has a focused unit test.

## Isolation and retention

Commands executed as `user-a`, `user-b`, `user-c`, and `root` reported the
expected account, home directory, and working directory. Regular identities
could not read root-owned service logs or open service-node shells. Application
logs and artifacts were read through the experiment's captured owner and
restricted to that owner's shared home directory.

Operation, experiment, event, and audit records use bounded output and
centralized recursive redaction. Completed experiment records survived a new
dashboard service instance. Retries created new experiment identifiers, while
comparison left both source records unchanged.

## Source-specific behavior

PRTE does not emit a separate DVM text log through the QFw service manager. The
Progress DVM source therefore presents the authoritative bounded
`service-plane.json` lifecycle record. NWQSim process output remains available
as its own simulator source. IQM provider activity is selected independently
from the QPM log stream.

The validation did not recreate the live cluster after the IQM credential was
provisioned. The recreate command is covered by argument, authorization,
confirmation, and operation-retention tests. Running it would remove named
volumes and was unnecessary after the fresh image build had passed.

## Verification commands

Run the focused validation from the cluster repository.

```bash
PYTHONPATH=dashboard/src:dashboard/external/electroboy/src \
  pytest -q dashboard/tests

PYTHONPATH=dashboard/external/electroboy/src pytest -q \
  dashboard/external/electroboy/tests/test_service.py \
  dashboard/external/electroboy/tests/test_service_interfaces.py \
  dashboard/external/electroboy/tests/test_distribution_boundaries.py

dashboard/enable.sh --port 18765
source dashboard/run/environment
qfw-dashboard-service start
qfw-dashboard status
qfw-dashboard diagnostics
qfw-dashboard-service stop
```

Use any free loopback port in place of `18765`.
