## Configure and start the IBM QSA simulator in the QFw slurm cluster

This recipe shows how to configure and start the IBM QSA simulator for the
purpose of developement/testing in QFw - please check the
[qsa-sim](https://github.com/qrmi-community/simulated_quantum_resource.git)
repository for further detais and limitations.

If you want to experiment with real IBM Quantum System devices (on-prem), please
check the [IBM Quantum
Plans](https://quantum.cloud.ibm.com/docs/en/guides/plans-overview). 

### 1. Start the `qsa-sim`

Prerequisites:
- docker or podman
- docker-compose or podman-compose
- If using podman, please also install the podman-docker package.

The IBM QSA simulator (qsa-sim) does not need to run within the cluster. You can
install it in the docker host or somewhere else accessible by the cluster.

The instructions below will install and setup the QSA simulator and a
S3-compatible service. For more information, please refer to the
[simulated_quantum_resource](https://github.com/qrmi-community/simulated_quantum_resource)
documentation.

Set the `.env` variables as you see fit. For testing, you could keep them as-is.

```bash
git clone https://github.com/qrmi-community/simulated_quantum_resource.git
cd simulated_quantum_resource
# Update the variables below as necessary
cat << EOF > .env
QSASIM_IAM_APIKEY=myapikey
QSASIM_SERVICE_CRN=myinstance
QSASIM_BIND_PORT=8292
PYTHONUNBUFFERED=1
S3_ACCESS_KEY=myaccesskey
S3_SECRET_KEY=mysecretkey
S3_BUCKET_NAME=mybucket
S3_PORT=9000
S3_CONSOLE_PORT=9001
S3_REGION=us-east
EOF
docker compose up --build
# or: podman compose up --build
```

### 2. Populate credentials

First, create the `shared-dir/qsa-sim-device-access.yaml` and
`shared-dir/qsa-sim-creds.json` files, which contains the `ibm-fake-lagos`
device description and credentials, respectively. They are used by both QPM
local and site. Examples below.

`shared-dir/qsa-sim-creds.json`, make sure you have this configuration for all users you are planning to use and that they have `"enabled": true`:
```json
{
  "users": {
    "user-a": {
      "enabled": true,
      "devices": {
        "ibm-fake-lagos": {
          "enabled": true,
          "api_key": "myapikey",
          "aws_access_key_id": "myaccesskey",
          "aws_secret_access_key": "mysecretkey"
        }
      }
    }
  }
}
```

`shared-dir/qsa-sim-device-access.yaml`, make sure you replace `HOST_IP_ADDRESS`
by the IP address of the docker host where you started the qsa-sim:

```yaml
qpus:
  ibm-fake-lagos:
    provider: ibm
    resource-type: IBMQuantumSystem
    provider-device-id: fake_lagos
    url: http://<HOST_IP_ADDRESS>:8292
    iam-endpoint: http://<HOST_IP_ADDRESS>:8292
    service-crn: "myinstance"
    s3-endpoint: http://<HOST_IP_ADDRESS>:9000
    s3-bucket: "mybucket"
    s3-region: "us-east"
    job-timeout-seconds: "400"
    credential-db: /mnt/qsa-sim-creds.json
    libraries:
      - qrmi
    preference: qrmi
    execution-owner: qrmi
    caps:
      get_device_info: [qrmi]
      get_coupling_graph: [qrmi]
      get_backend_info: [qrmi]
      get_dynamic_backend_info: [qrmi]
      run_circuit: [qrmi]
      get_task_timing: [qrmi]
      get_task_metadata: [qrmi]
      get_calibration_snapshot: [qrmi]
```

Notice that both files are created with the 644 permission allowing them to be read by a site and local QPM. However, all cluster users can also read them. You could improve security by running `chmod 600 shared-dir/qsa-sim-creds.json`. Users trying to run local QPMs won't be able to access the credentials (Permission denied), but they could replicate and maintain both files in their user account for the local QPM.

> **SELinux note (Fedora / RHEL hosts):** The `shared-dir` bind-mount uses the
> `:z` option, which relabels files present at container start time with the
> `container_file_t` SELinux context. Files added *after* the containers are
> running keep their default host context and are **blocked** by SELinux inside
> the container (`[Errno 13] Permission denied`). If you create or replace either
> file while the cluster is already running, relabel it manually before starting
> QPM:
>
> ```bash
> chcon --reference shared-dir/qsa-sim-device-access.yaml \
>     shared-dir/qsa-sim-creds.json
> ```

Second, provision the `qsa-sim-device-access.yaml` for the QPM site. That is
done by running the `tools/provision-qfw-cluster.sh`, which will access the
`qsa-sim-head` container and create a symlink from the default
device-access.yaml to `/mnt/qsa-sim-device-access.yaml`. Notice that within the
container the `shared-dir` is mounted as `/mnt`.

```bash
tools/provision-qfw-cluster.sh
```

Lastly, start the qsa-sim QPM site service.

```bash
./do_ssh.sh
qfw-site-services start --target directory
qfw-site-services start --target gateway
qfw-site-services start --target qsa-sim
# Make sure all three services are UP
qfw-site-services status
```
