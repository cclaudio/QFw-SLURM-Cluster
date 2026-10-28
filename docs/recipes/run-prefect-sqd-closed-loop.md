# Run the Prefect SQD Closed Loop

This recipe runs the Prefect-orchestrated SQD closed-loop workflow from
[qiskit-community/qcsc-prefect](https://github.com/qiskit-community/qcsc-prefect)
on the cluster. The workflow optimizes LUCJ circuit parameters by differential
evolution; each walker samples a circuit, recovers configurations with
qiskit-addon-sqd, and runs the SBD Davidson solver as a Slurm MPI job. Its
upstream tutorial is
[run_sbd_closed_loop_workflow_qfw-slurm.md](https://github.com/qiskit-community/qcsc-prefect/blob/main/docs/tutorials/run_sbd_closed_loop_workflow_qfw-slurm.md).

The cluster adds two host tools and a compose file so that the tutorial's
manual setup becomes repeatable:

| Piece | Purpose |
| --- | --- |
| `tools/provision-prefect.sh` | Build the workflow environment on the shared workspace. |
| `tools/prefect-sqd` | Start the Prefect server, create the blocks, submit runs, stop. |
| `docker-compose.prefect.yml` | Publish the Prefect UI on the Docker host. |

The workflow runtime runs as a non-root application user. The Prefect server,
the served deployment, and the workflow process live in `slurmctld`; quantum
stages using `quantum_source=qfw` and the SBD solver run on the `normal`
partition as Slurm jobs.

## Prerequisites

- The cluster is running.
- The Docker host can reach PyPI and GitHub from inside `slurmctld`.
- The site service plane is not needed for the `random` quantum source.
  See [The qfw Quantum Source](#the-qfw-quantum-source) for what the real
  QFw path needs.

## Publish the Prefect UI

The Prefect server listens on port 4200 inside `slurmctld`. To reach its UI
from the Docker host, append the Prefect compose file to `COMPOSE_FILE` in
`.env`:

```text
COMPOSE_FILE=docker-compose.yml:docker-compose.telemetry.yml:docker-compose.prefect.yml
```

The mapping binds `127.0.0.1:4200` on the Docker host and takes effect when
`slurmctld` is recreated, which also means running
`tools/provision-qfw-cluster.sh` and restarting the QFw site services again.

If the cluster is running on a remote host, forward the Prefect UI port from
your local machine:

```bash
ssh -L 4200:127.0.0.1:4200 <user>@<remote-host>
```

Then open `http://127.0.0.1:4200` in a local browser. If the cluster is
running locally, open the same URL directly without SSH port forwarding.

The Prefect UI can be used to follow flow and task execution and inspect the
SQD walkers while their quantum and SBD stages run through Slurm. A workflow
run can also be submitted from the deployment page with **Run → Custom run**
using the parameters described in the upstream tutorial. The command-line
workflow shown below remains available when the UI is not needed.

## Configure QFw Device Access

This section is required only when using `quantum_source=qfw`.

Before provisioning the cluster, review:

- `config/device-access.yaml`
- `config/qpu-users.json`

The cluster configuration already defines the logical QFw device
`ibm-156-nh`. In `config/device-access.yaml`, set its `provider-device-id`
to the IBM Quantum backend that should execute the circuits.

For example:

```yaml
ibm-156-nh:
  provider: ibm
  provider-device-id: <ibm-backend-name>
  resource-type: IBMQiskitRuntimeService
  url: https://quantum.cloud.ibm.com/api/v1
  credential-db: qpu-users.json
  libraries:
    - qrmi
  preference: qrmi
  execution-owner: qrmi
  caps:
    run_circuit: [qrmi]
    get_backend_info: [qrmi]
    get_task_timing: [qrmi]
    get_task_metadata: [qrmi]
```

The corresponding entry in `config/qpu-users.json` must contain enabled IBM
Quantum credentials for the Slurm application user and `ibm-156-nh`,
including the API key and service CRN.

Configure these files before running `tools/provision-qfw-cluster.sh`.

> **Important:** Cluster provisioning installs these configuration files into
> the QFw service and compute containers. Editing the copies under `config/`
> afterward does not automatically update an already-provisioned environment.
> If either file changes later, reprovision or synchronize the updated files
> to the running containers and restart the QFw site services.

## Provision the Workflow Environment

First provision the QFw-SLURM cluster from the Docker host:

```bash
cd /path/to/QFw-SLURM-Cluster
tools/provision-qfw-cluster.sh
```

To start the QFw site services, enter the cluster environment:

```bash
./do_ssh.sh
```

Then, from inside the cluster environment:

```bash
qfw-site-services start
```

Exit back to the Docker host and provision the Prefect workflow environment:

```bash
exit
tools/provision-prefect.sh
```

Provision the QFw-SLURM cluster before the Prefect environment so that
`user-a` and the Slurm environment are available before the workflow
environment is created.

For `quantum_source=qfw`, provisioning also installs the QFw device and
credential configuration required by the quantum stages.

`tools/provision-prefect.sh` is safe to rerun. Its one root step creates the
Prefect workspace, `/workspace/qfw-container-base/qfw-prefect`
(`QFW_PREFECT_ROOT` overrides), and hands it to `user-a`: the shared mount's
root belongs to whoever owns it on the Docker host, so an application user
cannot create anything there directly. Everything else runs as `user-a`
inside that workspace:

1. Clones `qcsc-prefect` to `qcsc-prefect/` at the commit this recipe was
   validated with (`QCSC_PREFECT_REF` overrides).
2. Creates `venv/` with the image venv's exact package set, so QFw's pins
   (qiskit, qrmi, the QDMI adapters) stay in force, then adds Prefect, Ray,
   ffsim, pyscf, qiskit-addon-sqd and the qcsc-prefect packages. One image
   pin is overridden: `ruamel-yaml`, which the IQM QDMI adapter pins at 0.17
   while every `pydantic_numpy` that imports under NumPy 2.5 needs 0.18.
   Nothing on the Prefect path uses it. FastAPI is held below 0.150: Prefect
   3.8.8's server still passes `min_items` to `Body()`, which FastAPI dropped
   inside the range Prefect allows, and the server then fails to start.
3. Rebuilds ffsim from source when its wheel dies with an illegal instruction,
   which it does on Apple Silicon hosts.
4. Builds the native SBD solver with the image's OpenMPI and OpenBLAS.
5. Installs the quantum-stage sampler and its submitter under `bin/`.
6. Verifies the venv under `qfw-activate`, which is how the workflow uses it.

The workspace lives on the shared mount and survives a container recreate.
Expect several minutes on first run, mostly the ffsim build.

## Start the Server and Serve the Deployment

```bash
PREFECT_RAY_NUM_CPUS=4 tools/prefect-sqd start
tools/prefect-sqd blocks
tools/prefect-sqd status
```

`start` launches the Prefect server in the background and serves the
`riken-sqd-de/riken_sqd_de` deployment from a `nohup` process
(`/workspace/home/user-a/sbd-deploy.log`). `PREFECT_RAY_NUM_CPUS` bounds the
Ray task runner that fans the walkers out; set `SBD_TASK_RUNNER=concurrent`
to use threads instead.

`blocks` writes `/workspace/home/user-a/sbd_blocks.toml` for this cluster
(account `qfw-test`, partition `normal`, one node, `SBD_MPIPROCS` ranks,
`SBD_WALLTIME`), creates the Slurm SBD blocks from it, registers the
`QFwResource` block type from the main thread, and creates the `qfw-runner`
block for `QFW_PREFECT_DEVICE` (default `ornl-iqm-20q`). The workflow still
loads this block for its QFw configuration, while quantum sampling with
`quantum_source=qfw` is submitted through `run_sampler_job()` and QFw-SLURM.

For IBM Quantum execution, create the QFw block with `ibm-156-nh`:

```bash
QFW_PREFECT_DEVICE=ibm-156-nh tools/prefect-sqd blocks
```

The command should report:

```text
Device ID: ibm-156-nh
```

## Run the Workflow

The workflow supports both a local random sampling path and quantum execution
through QFw-SLURM.

### Random sampling

Use the `random` source to validate the Prefect orchestration and classical
SQD stages without quantum hardware:

```bash
tools/prefect-sqd run random 10000 1
```

The arguments are the quantum source, the SQD subspace dimension and the
number of differential-evolution iterations. The command submits a flow run
for the N2 example and waits for it. Each iteration launches four walkers,
and each walker submits one SBD job:

```bash
docker exec slurmctld squeue -u user-a
docker exec slurmctld sacct -u user-a -X \
  --format=JobID,JobName%20,State,ExitCode,Elapsed,NodeList
```

Job directories, generated `sbd_solver.slurm` scripts and solver output
land under `/workspace/home/user-a/sbd_jobs/`. The run's energies are in the
`sqd-telemetry` artifact, which the UI shows under the flow run or
`prefect artifact inspect sqd-telemetry` prints.

On this cluster the random-source command finishes in about half a minute:
the four walkers run in Ray workers, each SBD job spends about five seconds
on its node, and the four walkers report energies near -134.77 Ha for N2 with
the fast solver preset.

With the UI published, the same run starts from the deployment page with
**Run → Custom run** and the tutorial's parameter table.

### QFw quantum sampling

To execute the quantum sampling stages through QFw-SLURM, use:

```bash
tools/prefect-sqd run qfw 10000 1
```

With `quantum_source=qfw`, each walker generates its LUCJ circuit, submits the
sampling stage as a Slurm quantum job, executes it through `ibm-156-nh`,
retrieves the samples, and then continues with configuration recovery,
postselection, subsampling, and the Slurm-based SBD solver.

Quantum and classical Slurm jobs can be monitored with:

```bash
docker exec slurmctld squeue -u user-a
docker exec slurmctld sacct -u user-a -X \
  --format=JobID,JobName%20,State,ExitCode,Elapsed,NodeList
```

## The qfw Quantum Source

With `quantum_source=qfw`, each SQD walker submits its quantum sampling stage
as a Slurm job through `qfw_slurm_sqd_sampler.py`. The Slurm job carries the
qfw-slurm reservation options, runs the SQD sampler under `qfw-srun`, and
executes the circuit through `QFwBackend`.

`tools/prefect-sqd` sets `QFW_SLURM_BIN` to the provisioned `bin/` directory
under the Prefect workspace by default. This directory contains
`qfw_slurm_sqd_sampler.py`, installed by `tools/provision-prefect.sh`. Set
`QFW_SLURM_BIN` explicitly only when using a different helper location.

The workflow process itself does not hold the QFw reservation. Instead:

1. Prefect creates the LUCJ circuit and applies the ffsim pre-initialization
   required before QPY serialization.
2. `qfw_slurm_sqd_sampler.py` writes the circuit to the job directory and
   submits a Slurm job.
3. qfw-slurm reserves the selected QPM for the allocation.
4. The SQD sampler runs under `qfw-srun`, obtains the device-specific
   `QFwBackend.target`, performs target-aware transpilation, and executes the
   circuit.
5. The sampler writes the counts record back to the shared job directory.
6. The Prefect walker converts the returned counts to a `BitArray` and
   continues with recovery and the SBD stage.

For IBM devices accessed through QRMI, the corresponding entry in
`config/device-access.yaml` must include:

```yaml
caps:
  run_circuit: [qrmi]
  get_backend_info: [qrmi]
  get_task_timing: [qrmi]
  get_task_metadata: [qrmi]
```

`get_backend_info` allows `QFwBackend.target` to obtain the device-specific
target through the QPM telemetry path.

The SQD path uses the generic `run_sampler_job()` helper with a custom sampler
and `sampler_args` to pass the SQD transpilation options into the Slurm job.

The QFw-SLURM SQD path has been validated end to end with an IBM QRMI backend.
Other QFw-SLURM QPMs have not yet been validated for this workflow; they
require `QFwBackend.target` to supply the device target needed for SQD
transpilation.

## Stop

```bash
tools/prefect-sqd stop
```

This stops the served deployment and the Prefect server. The venv, checkout,
blocks and run history stay on the shared workspace.
