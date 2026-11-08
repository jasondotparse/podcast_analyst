# Docker and AWS deployment

The beta image contains React, FastAPI, and **Codex 0.167.0**. FastAPI serves
`/app/` and streams replies over `/api/*`. The Git-managed defaults select
`gpt-6-luna`, high reasoning, read-only sandboxing, and live web search.

Beta requires the shared test-user password before serving the UI or paid API
endpoints. FastAPI stores login state in memory; the browser receives only an
opaque `HttpOnly`, `Secure`, `SameSite=Lax` cookie. Sessions expire after 12 hours;
logout and backend restarts invalidate them. Each chat belongs to one login
session. `/health` remains public for container liveness; `/api/health` and `/ready`
require login. See [the auth design](devops.md#5-shared-password-login-and-server-side-sessions).

## What runs where

```text
Docker buildx on your Mac → ECR image tagged with commit + timestamp + random suffix
                                     ↓
                           SSM Run Command (no SSH)
                                     ↓
beta.podcastanalystagent.com → Elastic IP → Caddy HTTPS → FastAPI container
```

The TypeScript CDK stack in `infra/` provisions one `t3.medium` EC2 instance
(2 vCPUs, 4 GiB RAM),
an encrypted 16 GiB root volume, a public subnet, an Elastic IP, an ECR repository,
an instance role, and a Route 53 A record in the existing domain's public hosted
zone. There is no NAT gateway or load balancer. Only ports 80 and 443 accept
inbound traffic. FastAPI's port 8000 is bound to loopback on the host.

Caddy obtains and renews HTTPS certificates and redirects HTTP to HTTPS.
Its certificate files live in Docker volumes. EC2 uses its IAM role to pull from
its own ECR repository and register with SSM; it receives no local AWS keys.

`beta` and `prod` are separate stacks and hosts in **us-east-1**:

| Environment | Stack | URL |
|---|---|---|
| beta | `PodcastAnalyst-beta` | `https://beta.podcastanalystagent.com` |
| prod | `PodcastAnalyst-prod` | `https://podcastanalystagent.com` |

Deploy beta first. Prod is created only if you explicitly deploy its stack.

## Prerequisites

- Python 3.11+, Node.js 22+, npm, AWS CLI v2, Docker Desktop with Buildx/Compose.
- Start Docker Desktop before building or releasing.
- AWS CLI credentials for the intended account. Set `AWS_PROFILE` if using a named
  profile; both CDK and the deploy script honor it.
- A public Route 53 hosted zone for `podcastanalystagent.com`, with domain
  nameservers pointing to that zone. The stack creates the application A record.

These resources can incur charges: EC2, EBS, public IPv4, ECR, Route 53, and data
transfer. This setup is not a claim of free-tier eligibility. Configure an AWS
Budget alert in your account; no budget resource is created by this stack.

The host uses Unlimited CPU mode so initialization bursts are not throttled when
credits run out. Sustained CPU use above its baseline can add charges. The current
Linux On-Demand compute rate in us-east-1 is $0.0416/hour (about $30.37 for 730
hours), excluding disk, IPv4, and other services. This is a starting allocation;
measure memory again when adding the agent runtime and databases. Rates were
checked against the AWS pricing API on November 7, 2026; see
[AWS On-Demand pricing](https://aws.amazon.com/ec2/pricing/on-demand/).

## Run the container locally

VS Code/local Python development remains the fastest local workflow. For a
container check, create a **gitignored** `runtime.env` containing only:

```dotenv
OPENAI_API_KEY=<your-api-key>
PODCAST_APP_PASSWORD=<a-test-password>
```

Then run from the repository root (stop any server using port 8000 first):

```bash
docker buildx build --platform linux/amd64 --load -t podcast-analyst:local .
docker run --rm --name podcast-analyst-local -p 127.0.0.1:8000:8000 \
  --env-file runtime.env -e PODCAST_REQUIRE_AUTH=1 \
  -e PODCAST_PUBLIC_ORIGIN=http://127.0.0.1:8000 \
  -e PODCAST_CODEX_HOME=/home/app/.codex podcast-analyst:local
```

Open <http://127.0.0.1:8000/app/> and sign in. This local container check uses an
unpersisted home; cloud Compose uses the persistent
host mount below. Local HTTP cookies omit `Secure`; HTTPS beta cookies include it.
The image runs as UID 10001, includes the native Codex binary and a writable
`/app/.local/agent-workspace`, and has no runtime Node/Vite dependency.
Source/configuration edits require rebuilding the image.

The `cloud` Compose profile adds Caddy. Only Caddy exposes public ports; the app
port is host-loopback only. `.dockerignore` excludes `.env`, `runtime.env`, local
Codex homes, and caches; no runtime credentials are passed to the image build.

## Codex home and configuration

The image packages `codex/config.toml`, `codex/AGENTS.md`, and `codex/skills/` at
`/app/codex/`. FastAPI installs them at startup, before launching Codex; see
[the local configuration workflow](local_codex_chat.md#git-managed-codex-configuration).
These files contain no credentials. The build excludes runtime homes and `.env`.

Compose sets `PODCAST_CODEX_HOME=/var/lib/podcast-analyst/codex` and bind-mounts
the same EC2 host path into the container. The existing SSM deploy command creates
that directory with mode `0700` and owner/group `10001:10001` before Compose
starts, so the existing non-root app user can write configuration and state.
No directory is created implicitly by Compose. When running Compose manually on
Linux, first run:

```bash
sudo install -d -m 700 -o 10001 -g 10001 /var/lib/podcast-analyst/codex
findmnt -T /var/lib/podcast-analyst/codex
```

Confirm the path is backed by EBS. The current EC2 root filesystem is EBS-backed;
no new volume is provisioned. State survives process restarts and container
replacements/application releases. The root volume is deleted on instance
termination, so this does **not** preserve state across EC2 replacement. Do not
delete the host directory to update configuration; restart with the new image.

## One-time AWS setup

From the repository root, verify the destination account and install the locked
infrastructure dependencies:

```bash
aws sts get-caller-identity
export AWS_REGION=us-east-1
cd infra
npm ci
npm test
npm run cdk -- synth -c stage=beta
npm run cdk -- bootstrap
npm run cdk -- diff -c stage=beta
npm run cdk -- deploy -c stage=beta
cd ..
```

`bootstrap` creates the standard `CDKToolkit` stack once per account/region.
`deploy` creates the web host and prints `InstanceId`, `RepositoryUri`, `PublicIp`,
and `AppUrl`. An empty host does not serve the page until the first app release.
Host bootstrap installs Docker and a checksum-verified Compose plugin. The deploy
script waits for SSM and host bootstrap to become ready.

Keep `infra/cdk.context.json` with the project: it caches the AMI and hosted-zone
lookups, keeping infrastructure synthesis repeatable. To deliberately refresh the
AMI, reset its SSM context key with `npm run cdk -- context --reset <key>`, then
review `cdk diff`. Refreshing the AMI or changing bootstrap can replace EC2.

## Runtime secrets (one-time setup or rotation)

The local root `.env` must contain `OPENAI_API_KEY`. With explicit authorization
to transfer that key to the AWS account, run:

```bash
.venv/bin/python scripts/configure_runtime.py beta
```

This creates a random test-user password if `PODCAST_BETA_PASSWORD` is absent,
saves it in the gitignored local `.env` (mode `0600`), and uploads **only** the API
key and test-user password to `/podcast-analyst/beta/runtime` in **us-east-1**
Parameter Store as a standard `SecureString` using the AWS-managed SSM KMS key.
It never prints the secrets or passes them as command-line arguments. Share the
password with testers out of band. To rotate it, change `PODCAST_BETA_PASSWORD`
in `.env`, rerun this command, and deploy; existing login sessions then end.

The existing EC2 `AmazonSSMManagedInstanceCore` policy already permits parameter
retrieval; no CDK update, EC2 replacement, or new service is needed. This existing
AWS-managed policy grants parameter reads account-wide; this demo adds no further
IAM grants. The deploy command downloads the parameter using the host's role to
`/opt/podcast-analyst/runtime.env` with mode `0600`. Compose injects those variables
only into the app container. Secrets stay out of ECR images, SSM command payloads,
CloudFormation outputs, frontend bundles, and release logs. Administrators with
SSM access or Docker access on the host remain trusted.

## Every application release

From the repository root:

```bash
./scripts/deploy.py beta
```

The script looks up stack outputs, builds for **linux/amd64** (the EC2 architecture),
pushes a unique immutable ECR tag, transfers Compose/Caddy configuration through
SSM, retrieves runtime secrets, pulls the images, and waits for container health. It then verifies the public
HTTPS URL. First-time DNS propagation and certificate issuance may take a few
minutes. Failures produce a nonzero exit code and an SSM command ID for diagnosis.

Builds include the current working tree. The tag contains the current Git commit
plus a UTC timestamp and random suffix, so uncommitted iterations do not overwrite
earlier releases. Commit before releasing when you need a source-reproducible
release. Save the printed release tag for rollback.

The script does not run CDK or build anything on EC2. Docker builds React and
packages it with FastAPI and the pinned native Codex binary. Code changes reuse Docker's dependency cache;
Python/npm dependencies are reinstalled when their manifests or lockfiles change.
Releases briefly restart the single app container; this is not zero-downtime hosting.

## Roll back or redeploy an existing image

```bash
./scripts/deploy.py beta <previous-release-tag>
```

This skips the build and deploys the existing ECR image. It uses the Compose and
Caddy files in your checkout; if those changed, check out the matching configuration
first. Prior images are retained in ECR, without automatic expiration.
The host stores the last successful configuration in `.env` and the previous one
in `.env.previous` under `/opt/podcast-analyst`. A failed release does not
automatically roll back; use the command above to restore a known-good image.

## Logs and troubleshooting

Get the instance ID from stack outputs:

```bash
aws cloudformation describe-stacks --region us-east-1 --stack-name PodcastAnalyst-beta --query 'Stacks[0].Outputs'
```

Inspect a deployment using its printed command ID:

```bash
aws ssm get-command-invocation --region us-east-1 --command-id <command-id> --instance-id <instance-id>
```

For host logs, run commands through SSM (no Session Manager plugin is needed):

```bash
aws ssm send-command --region us-east-1 --instance-ids <instance-id> --document-name AWS-RunShellScript --parameters '{"commands":["cd /opt/podcast-analyst && docker compose --profile cloud ps && docker compose --profile cloud logs --tail 100"]}'
```

Fetch that command's output with `get-command-invocation` as above. If bootstrap
failed, use the same mechanism with `tail -n 100 /var/log/cloud-init-output.log`.
If SSM is offline, check EC2 instance status, the instance role, and outbound
internet access. For HTTPS failures, verify the A record points to `PublicIp`,
then check Caddy logs. Port 8000 and SSH intentionally have no public ingress.

### Codex tool failures

Container logs cover startup, HTTP requests, and turn-level failures. A tool can
fail while Codex still completes a normal assistant reply, so a successful HTTP
response or `turn/completed` is not proof that the tool worked.

For tool-level diagnostics, inspect Codex's persisted JSONL traces under
`/var/lib/podcast-analyst/codex/sessions/` on the host (the same path inside the app
container). Use SSM as above and `docker compose exec -T app python ...` to read a
specific recent trace. Match `custom_tool_call` to `custom_tool_call_output` by
`call_id`; older traces may use `function_call` / `function_call_output` instead.
Web operations may appear inside the generic `exec` tool's input rather than as
a top-level weather/search event. Inspect the matching **output**, not just the
call or the assistant's summary. Traces contain conversation content; select only
the affected turn and redact secrets rather than dumping entire files into logs.

One beta failure returned `failed to spawn code-mode host
/usr/local/bin/codex-code-mode-host: No such file or directory`. The runtime image
must package both `codex` and its sibling `codex-code-mode-host` executable from
the same pinned release. Configuration/readiness checks alone do not catch a
missing tool-execution helper; verify a successful real tool result after a
runtime packaging change.

## Infrastructure changes and cleanup

For infrastructure changes, run `npm run cdk -- diff -c stage=beta` from `infra/`,
review it, then `npm run cdk -- deploy -c stage=beta`. Normal app edits use only
the release script. To create prod, deploy with `-c stage=prod`, configure its runtime secrets, then run
`./scripts/deploy.py prod` from the repository root.

To remove an environment, run `npm run cdk -- destroy -c stage=beta` from `infra/`.
This terminates its instance, deletes its root disk (**including both databases**)
and Docker volumes, releases the Elastic IP, and removes its A record. The existing
hosted zone/domain and the ECR repository remain; retained ECR images can continue
to incur storage charges. The shared CDK bootstrap stack also remains.

## Databases

Compose runs PostgreSQL + pgvector as the private `db` service; the app container
owns the embedded LadybugDB graph. Every release ensures these host directories exist:
`/var/lib/podcast-analyst/postgres` (uid 999, the image's `postgres` user) and
`/var/lib/podcast-analyst/ladybug` (uid 10001), both mode `0700`. The first
release also writes `/opt/podcast-analyst/db.env` (mode `0600`) with a random
`POSTGRES_PASSWORD` and the app's `DATABASE_URL`; the password never leaves the
host. Postgres reads it only when initializing an empty data directory, so **never
delete `db.env` while data exists**. App startup applies pending migrations; see
[the database design](database_design.md#implementation).

Releases, restarts, and Compose down/up preserve both databases. EC2 replacement
or termination does not; see [devops.md](devops.md#preserving-the-data-layer).

Inspect Postgres through SSM with
`cd /opt/podcast-analyst && docker compose exec -T db psql -U podcast -c '\dt'`.
The running app holds Ladybug's exclusive lock. To inspect the graph, run
`docker compose stop app`, then `docker compose run --rm --no-deps -T app python -c '...'`,
then `docker compose up -d --wait app`.

## Validation

The **data layer release** was deployed to beta on **November 8, 2026**, without
an infrastructure deployment:

- Release tag: `ebd3e0e41108-20261108T160238-31f39b`.
- All 17 local tests pass, including a migration test against a real throwaway
  Postgres database and a Ladybug reopen/persistence test.
- Before beta, the deploy flow was rehearsed on a Docker-in-Docker Linux host
  (Compose v2.40.3) using the exact storage and `db.env` lines from `deploy.py`.
- On beta: `001_initial` applied once; pgvector 0.8.7; all nine design tables plus
  `schema_migrations`; `passages.embedding` is `vector(1536)`; 11 graph tables.
  Data directories are owned by 999 and 10001 with mode `0700` on the root EBS (xfs)
  volume; `db.env` is `0600`, owned by root.
- A Postgres row and a graph node survived an app container recreate and were then
  deleted. A second `deploy.py` run left `db.env` and migration history unchanged.
- After startup, the host used 425 of 3839 MiB (app 95 MiB, db 41 MiB).

The tool-runtime fix was deployed on **November 7, 2026** as
`8abc73fd9016-20261107T220255-tools`. The image includes both pinned executables.
An isolated native x86 container on EC2 returned actual Chicago weather data;
the deployed public HTTPS chat then streamed a weather reply in 30 text chunks.
The local Docker Desktop amd64-emulation test killed the helper with SIGKILL,
so the native EC2 test is the runtime validation for this fix.


The **Codex chat release** was deployed to beta on **November 7, 2026**, without
an infrastructure deployment:

- Release tag: `0b8dbea2655e-20261107T214942-9d7bcd`.
- All 15 offline tests pass, covering configuration preservation, login/logout,
  expiry, cross-site rejection, chat ownership, and the existing RPC/SSE behavior.
- The Linux/amd64 image initializes native Codex 0.167.0 as UID 10001.
- Public HTTPS redirects anonymous UI requests to login and rejects API/SSE calls
  with 401. Login issues an opaque `Secure`, `HttpOnly`, `SameSite=Lax` cookie.
- Authenticated HTTPS checks verified progressive reply text (20 delta events),
  a same-thread follow-up retaining context, independent login-session isolation,
  and logout revocation. These checks used HTTP cookie jars, not browser automation.
- The initial web-tool check verified invocation and the assistant reply but
  missed failed tool outputs. The missing code-mode helper was diagnosed from
  the matching `custom_tool_call_output`; see the tool-failure procedure above.
- Host inspection verified `gpt-6-luna`, high reasoning, live web search, and
  `/var/lib/podcast-analyst/codex` on the EBS-backed root filesystem, owned by
  `10001:10001` with mode `0700`. Runtime secrets have mode `0600`, owned by root.
- A controlled app-container restart preserved the persisted thread file's
  SHA-256 hash. Codex history remains on disk; the application still starts fresh
  browser conversations after reload/restart.

Automated Chrome verification was unavailable in this session; the HTTPS/API
checks above are the live verification record.

### Previous releases

The **React release** was deployed to beta on **November 7, 2026**, using
`./scripts/deploy.py beta` without an infrastructure deployment.

- Release tag: `4ca7e5ffddbc-20261107T181832-a3db5c`.
- Chrome renders **Hello world!** and **Backend: ok** at
  `https://beta.podcastanalystagent.com/app/`.
- Public HTTPS serves the exact locally built HTML, JavaScript, and CSS.
- `/api/health` returns `{"status":"ok"}`. HTTP and `/` redirect to HTTPS `/app/`;
  unknown API paths, missing assets, and `/docs` return 404.

The following checks describe the original hello world release in AWS on
**November 7, 2026**, before the React conversion:

- CDK bootstrap and `PodcastAnalyst-beta` deployment succeeded in us-east-1.
- The host was resized through CDK to `t3.medium`; AWS reports Unlimited CPU
  credits, and Linux reports 3839 MiB of usable memory.
- Public HTTPS returns the exact hello world HTML; HTTP redirects to HTTPS and
  `/docs` returns 404.
- A second image with a temporary HTML marker reached the live site using only
  the app deploy command. Redeploying the first tag restored the original page.
- All three infrastructure tests pass, and a subsequent `cdk diff` reports no
  differences. Native ARM and EC2-targeted amd64 containers were checked locally.
- Prod has not been provisioned. Login and the agent runtime are not implemented.

Known-good hello world image tag: `a60078f98ed4-20261107T173247-007869`.
Verification used HTTP requests; an automated browser session was unavailable.

Run `npm test` in `infra/` for assertions covering the host count, public ports,
IMDSv2, disk encryption, image retention, and environment DNS names. The deploy
script performs the live health/HTTPS checks. `npm audit` currently reports a
moderate minimatch advisory in the CDK tooling; this dependency is not packaged
in the application image.

References: [CDK bootstrap](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping-env.html),
[Compose installation](https://docs.docker.com/compose/install/linux/),
[Caddy HTTPS](https://caddyserver.com/docs/automatic-https),
[SSM Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html),
[encrypted runtime parameters](https://docs.aws.amazon.com/systems-manager/latest/userguide/secure-string-parameter-kms-encryption.html).
