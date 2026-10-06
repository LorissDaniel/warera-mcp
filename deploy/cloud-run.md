# Cloud Run maintainer notes

The public hosted service uses stateless Streamable HTTP and no client login.
WarEra credentials remain request-scoped tool arguments. All tools are read-only.
User connection instructions belong in the main README.

With an authenticated gcloud CLI and a project linked to active billing, run:

```bash
bash deploy/cloud-run.sh YOUR_PROJECT_ID europe-west1
```

Cloud Build builds the Dockerfile with locked dependencies; local Docker is not
required. The runtime runs as a non-root user on `0.0.0.0:$PORT` with a dedicated
Google service account. No WarEra credential or client token is deployed.

The script grants the default Compute Engine build identity `roles/run.builder`,
creates the runtime identity, deploys behind IAM, configures the exact service
hostname for MCP Host validation, then allows public invocation. Redeploying
briefly makes the service private again. IAM grants may take minutes to propagate.

Defaults: 1 CPU, 512 MiB, concurrency 8, minimum 0 instances, maximum 1 instance,
60-second timeout, JSON MCP responses and `/health` startup probe. In-memory
cache/rate limits apply per process; adding instances increases the upstream
request budget. Cloud Run may briefly exceed instance limits during deployment.
Proxy-header trust remains disabled; platform proxy peers may share rate limits.

Verify `/health`, then MCP `initialize`, `tools/list` (44 tools), and a public
tool call. Clients use the HTTPS service URL with `/mcp` and no auth headers.

Scale-to-zero and a single instance are not a spending cap. Traffic, builds and
Artifact Registry storage can incur charges. Use Billing budget alerts to
monitor spending.

References: [source deployment](https://docs.cloud.google.com/run/docs/deploying-source-code)
and [container contract](https://docs.cloud.google.com/run/docs/container-contract).
