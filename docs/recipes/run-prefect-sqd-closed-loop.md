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

Everything runs as a non-root application user. The Prefect server, the
served deployment and the workflow process live in `slurmctld`; the SBD
solver runs on the `normal` partition through `sbatch` and `srun`.

## Prerequisites

- The cluster is running and provisioned (`tools/provision-qfw-cluster.sh`),
  so `user-a` exists with the `qfw-test` Slurm account.
- The Docker host can reach PyPI and GitHub from inside `slurmctld`.
- The site service plane is not needed for the `random` quantum source.
  See [The qfw quantum source](#the-qfw-quantum-source) for what the real
  QFw path needs.

## Publish the Prefect UI

The Prefect server listens on port 4200 inside `slurmctld`. To reach its UI
from the Docker host, append the Prefect compose file to `COMPOSE_FILE` in
`.env`:

```text
COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml:docker-compose.telemetry.yml:docker-compose.prefect.yml
```

The mapping binds `127.0.0.1:4200` on the Docker host and takes effect when
`slurmctld` is recreated, which also means running
`tools/provision-qfw-cluster.sh` and `qfw-site-services start` again.

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

## Provision the Workflow Environment

From the Docker host:

```bash
cd /path/to/QFw-SLURM-Cluster
tools/provision-prefect.sh
```

The script is safe to rerun. Its one root step creates the Prefect
workspace, `/workspace/qfw-container-base/qfw-prefect` (`QFW_PREFECT_ROOT`
overrides), and hands it to `user-a`: the shared mount's root belongs to
whoever owns it on the Docker host, so an application user cannot create
anything there directly. Everything else runs as `user-a` inside that
workspace:

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
`QFwResource` block type from the main thread and creates the `qfw-runner`
block for `QFW_PREFECT_DEVICE` (default `ornl-iqm-20q`).

## Run the Workflow

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

On this cluster the command above finishes in about half a minute: the four
walkers run in Ray workers, each SBD job spends about five seconds on its
node, and the four walkers report energies near -134.77 Ha for N2 with the
fast solver preset.

With the UI published, the same run starts from the deployment page with
**Run → Custom run** and the tutorial's parameter table.

## The qfw Quantum Source

With `quantum_source=qfw`, each SQD walker submits its quantum sampling stage
as a Slurm job through `qfw_slurm_sampler.py`. The Slurm job carries the
qfw-slurm reservation options, runs the SQD sampler under `qfw-srun`, and
executes the circuit through `QFwBackend`.

The workflow process itself does not hold the QFw reservation. Instead:

1. Prefect creates the LUCJ circuit and applies the ffsim pre-initialization
   required before QPY serialization.
2. `qfw_slurm_sampler.py` writes the circuit to the job directory and submits
   a Slurm job.
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

The QFw-SLURM SQD path has been validated end to end with the IBM QRMI backend.
Other QFw-SLURM QPMs have not yet been validated for this workflow; they require
`QFwBackend.target` to supply the device target needed for SQD transpilation.

## Stop

```bash
tools/prefect-sqd stop
```

This stops the served deployment and the Prefect server. The venv, checkout,
blocks and run history stay on the shared workspace.
