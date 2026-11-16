# Podcast Intelligence Agent: DevOps & Deployment

**Status:** React + Vite provides the local UI with HMR; FastAPI serves `/api/*` and the compiled UI at `/app/`. Docker builds the UI and backend into one image for the existing ECR/SSM release flow. Follow [AWS deployment instructions](aws_deployment.md) and [VS Code local setup](local_development.md). The React build is deployed and verified on beta. Beta deployment now includes password login and server-side sessions, plus PostgreSQL + pgvector and LadybugDB with empty schemas ([database design](database_design.md#implementation)).

**Local agent work:** The Codex chat harness streams replies locally and is packaged for beta; see
[local_codex_chat.md](local_codex_chat.md).
The accepted [agent harness design](agent_harness_design.md) uses a password
distributed to test users ahead of time, server-side login sessions, and cookies.
The beta runtime implements this password/session gate.

**Decisions:** AWS region **us-east-1**; CDK language **TypeScript**. The domain `podcastanalystagent.com` is registered in Route 53.

**Current beta:** `https://beta.podcastanalystagent.com` runs on a `t3.medium`
(2 vCPUs, 4 GiB RAM, Unlimited CPU credits). Its DNS and HTTPS are verified; see
[the deployment guide](aws_deployment.md) for the recorded checks.

**Prod:** `https://podcastanalystagent.com` uses the same image, Compose/Caddy files,
and release scripts on its own `t3.medium`, with a 32 GiB root disk (beta stays at
16 GiB). Its password, SES identity, and initially empty databases are separate.
Follow the [prod runbook](aws_deployment.md#prod-deployment); never copy beta data
or deploy a beta instance replacement to create prod.

**Alpha:** `https://alpha.podcastanalystagent.com` is an experimental testbed with
beta's exact settings (instance, 16 GiB disk, password) on its own host and
databases. Deploy with `./scripts/deploy.py alpha`.

**Audience:** Engineer joining the project on day one.  
**Immediate milestone:** **P−1: deploy a minimal React + FastAPI application to the owner's personal AWS account, securely accessible through a web browser, with fast repeatable deployments.**

## 1. Operating principles

- **Minimize DevOps work.** One EC2 instance; no Kubernetes, ECS, load balancer, or elaborate CI/CD for the prototype.
- **Infrastructure ≠ application releases.** AWS CDK creates/changes cloud resources; a separate script publishes Docker images and restarts containers. Most code changes must **not** require `cdk deploy`.
- **Ship in containers.** Use Docker for reproducible builds and Docker Compose to run services on EC2 and locally.
- **Protect paid resources.** FastAPI validates a pre-shared test-user password, creates a server-side login session, and checks its cookie before serving the protected UI or API, including streaming/chat and ingestion endpoints.
- **Keep credentials out of code.** The owner's AWS CLI profile is used locally; the personal OpenAI API key stays server-side in AWS Secrets Manager (or an equivalent protected runtime secret).
- **Optimize the feedback loop.** Local hot reload; one command to deploy application changes; simple logs, health checks, and rollback.

## 2. Target topology (P−1)

```text
Developer's machine
  ├─ aws CLI (personal AWS profile)
  ├─ AWS CDK v2 → CloudFormation (initial infra + infra changes)
  └─ ./scripts/deploy.py beta
       ├─ build Linux Docker images → push to ECR
       └─ SSM Run Command → EC2 pulls images and restarts Compose

Internet / invited reviewer
  │ HTTPS (app.example.com)
  ▼
EC2 instance — public ports 80/443 only
  ├─ Caddy             TLS termination, HTTPS redirect → FastAPI
  └─ FastAPI           public login page + password check
       ├─ server-side login sessions ← opaque session-ID cookie
       ├─ protected React /app/* + /api/*, including SSE
       ├─ Codex App Server subprocess
       ├─ LadybugDB          embedded graph file, owned by this process
       └─ PostgreSQL + pgvector (`db` container, private network)

AWS supporting services
  ├─ ECR               Docker image registries
  ├─ SSM SecureString  OpenAI key + app password (runtime-only)
  ├─ IAM + SSM         controlled EC2 management without public SSH
  └─ Route 53 / DNS    hostname → EC2 Elastic IP
```

**Request handling:** Caddy handles automatic HTTPS and proxies to FastAPI over the private Compose network. FastAPI serves the built React files and owns password verification, server-side login sessions, and authorization for both the UI and API. Only Caddy binds public ports.

**Domain prerequisite:** Automatic public HTTPS needs a stable hostname with DNS pointed at the instance. The registered domain is `podcastanalystagent.com`; beta uses `beta.podcastanalystagent.com`. Login, the protected UI, and the API share that origin so the browser sends its session cookie on requests.

## 3. Container packaging

Initial Compose services:

| Service | Image / role | Publicly reachable? |
|---|---|---|
| `edge` | Caddy: HTTPS → `app` | **Yes**, ports 80/443 |
| `app` | Vite builds React; FastAPI serves it at `/app/`, handles `/api/*`, and enforces server-side session authentication | No |
| `db` | PostgreSQL 18 + pgvector, version- and digest-pinned | No |

The *React build* and *FastAPI service* ship together in one application image in **Amazon ECR**, tagged by Git commit SHA plus a unique release suffix. Caddy's image is version-pinned. A single `compose.yaml` connects the services over a private Docker network. Browser requests use same-origin `/api/*`, avoiding unnecessary CORS configuration. The image includes Codex 0.167.0 and the password/session implementation.

PostgreSQL/pgvector and the embedded Ladybug database store data in host bind mounts, never only inside a disposable container filesystem; see [Preserving the data layer](#preserving-the-data-layer).

**Local development:** Vite on port 5173 provides React HMR and proxies `/api` to FastAPI on port 8000. The VS Code **run full stack app** compound starts both; **serve front end** starts only Vite. FastAPI also serves a built React snapshot at `/app/`. See [local_development.md](local_development.md); Docker Compose remains available for checking the deployment image.

## 4. Infrastructure as code: AWS CDK

Use **AWS CDK v2 with TypeScript** in `infra/`, targeting **us-east-1**. The stack will provision:

- One appropriately sized **EC2** instance with Docker + Compose and SSM Agent configured via user data/bootstrap; EBS storage.
- An **IAM instance role** with only required ECR pull, SSM management, and scoped secret-read permissions.
- A **security group** allowing public **80/443 only**; no inbound SSH (`22`) or direct access to API/database/auth ports.
- An **ECR repository** for the combined application image.
- **SSM Parameter Store SecureString** runtime secrets for the OpenAI key and shared app password, configured separately from CloudFormation.
- A **stable address** (Elastic IP + DNS A record) and useful CloudFormation outputs (instance ID, ECR URL, app URL).

The account is the **owner's personal AWS account**, accessed with their existing AWS CLI credentials/profile. **Never commit local AWS credentials** or place AWS access keys inside containers. EC2 uses its **instance role**, not a copy of the developer's credentials.

The implemented `PodcastAnalyst-beta` stack and exact first-time commands are in
[aws_deployment.md](aws_deployment.md). The stack provisions the host and HTTPS routing. Runtime secrets are configured
separately in encrypted Parameter Store, then retrieved by the existing host role.

CDK bootstrap is per AWS account/region. Use `cdk diff` before subsequent infrastructure changes. This path is for **infrastructure only**, not the routine code-edit loop.

## 5. Shared-password login and server-side sessions

**Threat model:** If a public visitor can hit chat/ingestion endpoints, they can consume the owner's OpenAI credits. Protecting only the React login screen is **not enough**; the backend must also be inaccessible without authentication.

Implemented beta sign-in design:

1. Give invited test users the app URL and shared password ahead of time. A public login page submits the password to `POST /api/login`; FastAPI verifies it against a backend-only runtime secret. There is no public registration or separate user-account provisioning.
2. On successful login, FastAPI generates an unpredictable login-session ID and stores the login state and expiry **server-side**. The single-worker demo uses a small in-memory store; restarting the backend signs users out.
3. Send only the opaque login-session ID in the browser cookie. Use `HttpOnly`, `Secure` in HTTPS deployments, `SameSite=Lax`, and a defined expiry. Any cookie-signing secret is independent of the shared password. Passwords, authentication claims, and model credentials stay out of the cookie and frontend bundle.
4. For `/app/*` and protected `/api/*` requests, verify the cookie and look up its unexpired server-side session. Redirect unauthenticated UI requests to login; reject unauthenticated API/SSE requests with 401. Only login entry points and intentionally minimal liveness checks are public.
5. Bind each Codex app-session ID to its owning login session. Check ownership on turn, events, and interrupt endpoints, so knowing the shared password does not grant access to another browser's conversation.
6. `POST /api/logout` removes the server-side login record and clears the cookie; expiry also ends access. Login and other state-changing requests use same-origin/CSRF protection. Verify login, logout, expiry, direct API requests, and SSE authorization before inviting users.

**Starlette detail:** `SessionMiddleware` provides signed, cookie-based sessions by default; it does not supply a server-side session store. If used as a signing helper, its cookie should carry only our opaque session ID. FastAPI must still store and validate login state on the server.

Anyone who finds the URL may see the login page, but the protected UI and paid API actions require a valid server-side session. Test in a fresh incognito browser and with direct unauthenticated API/SSE requests.

## 6. Secrets, cost, and permissions

- Owner's **OpenAI API key**: store as an AWS secret; retrieve on the server at runtime/startup. **Never** pass it to the browser, Docker build arguments, source control, or logs.
- **Shared app password**: store as a protected runtime secret alongside the OpenAI key. The opaque random session ID needs no separate signing secret; authorization comes from the server-side lookup. Do not put them in React build variables, images, CloudFormation outputs, or logs.
- Runtime secrets are mounted/read with restrictive permissions; avoid committing `.env`, tokens, credentials, or real hostnames containing sensitive identifiers.
- Restrict EC2 IAM access to the specific secret ARNs; use AWS CLI/SSM under the developer's personal identity, not a long-lived instance access key.
- Set AWS **Budget alerts** and OpenAI usage monitoring. Monitoring is not necessarily a hard spending cap; avoid unbounded ingestion or anonymous model calls.
- **Case-study constraint to resolve:** The brief calls for **free-tier resources only**. A personally funded EC2 instance and paid OpenAI API key do **not automatically satisfy that rule**. Before final submission, either clarify acceptability with AC or demonstrate a compliant free-tier path and document the costs. Do not silently claim the prototype is free-tier.

## 7. Deployment workflow: optimize for iteration

### One-time bootstrap

1. Select AWS region/profile and a domain; verify domain DNS control.
2. Scaffold CDK, Dockerfiles, `compose.yaml`, and a minimal React/FastAPI application.
3. `cdk bootstrap` and `cdk deploy`: create AWS resources and configure the EC2 host.
4. Set the OpenAI key and shared app password securely; give test users the password out of band.
5. Run the first application release; verify HTTPS, sign-in, app page, and `/api/health`.

### Every normal application release

For application releases, run **`./scripts/deploy.py beta`**; see [the deployment guide](aws_deployment.md) for setup, rollback, and logs. The authenticated release flow is:

```text
1. Build React + FastAPI images for the EC2 CPU architecture
2. Tag images with Git commit SHA
3. Authenticate to ECR and push images
4. Send an SSM command to EC2 (no SSH required)
5. EC2 pulls those exact image tags
6. docker compose up -d / recreate changed containers
7. Check /api/health through the authenticated site; report errors
```

The current deploy script uses Docker Buildx, ECR, and **SSM Run Command**. It builds explicitly for `linux/amd64`, matching the x86 EC2 host, rather than publishing the Mac's ARM architecture.

**Rollback:** Keep prior SHA-tagged images and a small release record. Revert the Compose image tags to the previous known-good commit, then recreate containers. Avoid deploying new infrastructure during a simple application rollback.

**Logs and diagnosis:** `docker compose logs -f app`, container health checks, and SSM execution output are sufficient for P−1. Add persistent log aggregation only if it provides clear value. Verify that Caddy forwards FastAPI's SSE responses progressively through the session-authenticated connection.

## Preserving the data layer
We don't want our databases to get wiped each time we do a deploy to beta/prod! Persistent Docker volumes don't automatically protect us against EC2 instance replacement.

For example, if CDK destroys and recreates our EC2 instance, data on its root EBS volume could disappear.

I'd therefore recommend:
1. Put database files on a dedicated EBS volume.
2. Configure that volume to be retained when the EC2 instance is replaced.
3. Use Docker bind mounts pointing to directories on that volume.
4. Never run docker compose down -v in our deployment scripts.
The daily workflow should be:
Application containers
Replaced

React · FastAPI · Agent runtime

Persistent database storage
Preserved

PostgreSQL · pgvector · LadybugDB

One other thing: schema migrations should be explicit deployment steps, not something that silently wipes and recreates tables.

Bottom line: We should be able to deploy application changes fifty times a day without losing a single podcast, transcript, embedding, or graph relationship.

**Current state (demo trade-off):** items 3 and 4 are implemented and migrations
are forward-only, applied once each at app startup. Items 1 and 2 are deferred:
both databases live under `/var/lib/podcast-analyst/{postgres,ladybug}` on the
root EBS volume, alongside the host-generated `db.env` password file. Releases,
restarts, and Compose down/up preserve them; **EC2 replacement or termination
deletes them.** CDK replaces the instance when `infra/bootstrap.sh` or the AMI
context changes, so check `cdk diff` for instance replacement before deploying
infrastructure. The ~15–20 demo episodes can be re-ingested. Add the retained volume
before the data is expensive to recreate.

## 8. P−1: definition of done

P−1 is **complete only when all are demonstrably working**:

- [ ] A fresh `cdk deploy` provisions a usable EC2 web environment in the personal AWS account.
- [ ] Local React + FastAPI hot reload works.
- [ ] A browser can reach the deployed React app over **HTTPS** at a stable URL.
- [ ] Anonymous visitors cannot access the protected UI or API/SSE endpoints; test users with valid server-side login sessions can.
- [ ] A reviewer can sign in with the pre-shared password and sign out; logout and expiry invalidate access server-side.
- [ ] Two logged-in browsers cannot access each other's Codex sessions, even though they use the same app password.
- [ ] React successfully calls authenticated FastAPI `GET /api/health`.
- [ ] A dummy FastAPI SSE endpoint streams progressively all the way to the browser.
- [ ] A frontend or backend edit goes live using **one application-deploy command**, without `cdk deploy`.
- [ ] Errors can be diagnosed with straightforward container/SSM logs; a prior release can be redeployed.
- [ ] No secrets are in Git, image layers, browser assets, or public API responses; cost alerts are configured.

**Keep this phase narrow.** There is no requirement yet to connect PostgreSQL, build the knowledge graph, configure Codex, or process podcasts. A secure, boring deployment loop is the deliverable.

## 9. After P−1

- **P0-A:** One episode → transcript → attributed claims → Postgres/pgvector → Ladybug graph.
- **P0-B:** Codex + `SKILL.md` with bounded graph/vector tools and source-grounded responses.
- **P0-C:** React research UX, citations, transcript inspection, graph visualization.
- **P0-D:** Required multi-episode sample, ranking, failure cases, evaluation, docs.
- **P1:** Scheduled discovery, novelty/thesis-impact assessment, proactive analyst alerts.

**Rule:** If deploying a one-line API change feels cumbersome, fix that before expanding application scope.

## Reference documentation

- [AWS CDK deployment and bootstrapping](https://docs.aws.amazon.com/cdk/v2/guide/deploy.html)
- [Starlette session middleware semantics](https://starlette.dev/middleware/#sessionmiddleware)
- [Starlette response cookies](https://starlette.dev/responses/#set-cookie)
- [AWS Systems Manager Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html)
