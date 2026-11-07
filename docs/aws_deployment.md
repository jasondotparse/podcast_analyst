# Docker and AWS deployment

This packages the React hello world UI and FastAPI API in one application image.
FastAPI serves the built UI at `/app/` and health JSON at `/api/health`; `/`
redirects to `/app/`. Login, databases, and model calls are later work. The page
is publicly accessible and cannot trigger paid model calls.

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

From the repository root:

```bash
docker compose --env-file /dev/null up -d --build app
curl -i http://127.0.0.1:8000/app/
curl -i http://127.0.0.1:8000/api/health
docker compose --env-file /dev/null logs -f app
```

Use `--env-file /dev/null` so Compose does not load the existing local `.env`;
this app requires no secrets. Stop a VS Code/terminal server on port 8000 first.
The Docker build compiles React in a Node stage and copies only the generated
`dist/` into the non-root Python runtime. The container checks `/api/health`.
Source edits require rebuilding the image; use the VS Code Vite/HMR flow in
[local_development.md](local_development.md) for rapid frontend edits.

Stop the local containers when switching back to VS Code:

```bash
docker compose --env-file /dev/null down
```

The `cloud` Compose profile adds Caddy. It is enabled by the deploy script on EC2;
it is not needed for local development. `.dockerignore` allows only app source
into the build context and excludes `.env` files and Python caches.

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

## Every application release

From the repository root:

```bash
./scripts/deploy.py beta
```

The script looks up stack outputs, builds for **linux/amd64** (the EC2 architecture),
pushes a unique immutable ECR tag, transfers Compose/Caddy configuration through
SSM, pulls the images, and waits for container health. It then verifies the public
HTTPS URL. First-time DNS propagation and certificate issuance may take a few
minutes. Failures produce a nonzero exit code and an SSM command ID for diagnosis.

Builds include the current working tree. The tag contains the current Git commit
plus a UTC timestamp and random suffix, so uncommitted iterations do not overwrite
earlier releases. Commit before releasing when you need a source-reproducible
release. Save the printed release tag for rollback.

The script does not run CDK or build anything on EC2. Docker builds React and
packages it with FastAPI. Code changes reuse Docker's dependency cache;
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

## Infrastructure changes and cleanup

For infrastructure changes, run `npm run cdk -- diff -c stage=beta` from `infra/`,
review it, then `npm run cdk -- deploy -c stage=beta`. Normal app edits use only
the release script. To create prod, deploy with `-c stage=prod`, then run
`./scripts/deploy.py prod` from the repository root.

To remove an environment, run `npm run cdk -- destroy -c stage=beta` from `infra/`.
This terminates its instance, deletes its root disk and Docker volumes, releases
the Elastic IP, and removes its A record. The existing hosted zone/domain and the
ECR repository remain; retained ECR images can continue to incur storage charges.
The shared CDK bootstrap stack also remains.

No database exists yet. Application releases never delete Docker volumes, but
instance replacement/deletion loses the root disk. Before adding durable data,
provision retained database storage and backups as described in [devops.md](devops.md).

## Validation

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
[SSM Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html).
