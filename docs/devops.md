# Podcast Intelligence Agent: DevOps & Deployment

**Status:** React + Vite provides the local UI with HMR; FastAPI serves `/api/*` and the compiled UI at `/app/`. Docker builds the UI and backend into one image for the existing ECR/SSM release flow. Follow [AWS deployment instructions](aws_deployment.md) and [VS Code local setup](local_development.md). The React build is deployed and verified on beta. The authentication topology below describes later work; no authentication or database is implemented yet.

**Local agent work:** The Codex chat harness now streams replies locally; see
[local_codex_chat.md](local_codex_chat.md). It is not packaged for deployment yet.
The accepted [agent harness design](agent_harness_design.md) supersedes the older
Cognito proposal below with shared-password FastAPI sessions for the next phase.

**Decisions:** AWS region **us-east-1**; CDK language **TypeScript**. The domain `podcastanalystagent.com` is registered in Route 53.

**Current beta:** `https://beta.podcastanalystagent.com` runs on a `t3.medium`
(2 vCPUs, 4 GiB RAM, Unlimited CPU credits). Its DNS and HTTPS are verified; see
[the deployment guide](aws_deployment.md) for the recorded checks.

**Audience:** Engineer joining the project on day one.  
**Immediate milestone:** **P−1: deploy a minimal React + FastAPI application to the owner's personal AWS account, securely accessible through a web browser, with fast repeatable deployments.**

## 1. Operating principles

- **Minimize DevOps work.** One EC2 instance; no Kubernetes, ECS, load balancer, or elaborate CI/CD for the prototype.
- **Infrastructure ≠ application releases.** AWS CDK creates/changes cloud resources; a separate script publishes Docker images and restarts containers. Most code changes must **not** require `cdk deploy`.
- **Ship in containers.** Use Docker for reproducible builds and Docker Compose to run services on EC2 and locally.
- **Protect paid resources.** Require Cognito sign-in before serving the application **or calling any API**, including streaming/chat and ingestion endpoints.
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
  ├─ Caddy             TLS termination, HTTPS redirect
  ├─ oauth2-proxy      OIDC login/session enforcement (Cognito)
  ├─ Nginx             compiled React SPA + /api reverse proxy
  └─ FastAPI           /api/health, /api/stream; later agent/ingestion
           │
           └─ later: PostgreSQL + pgvector, LadybugDB, Codex

Amazon Cognito User Pool
  └─ invite-only accounts + hosted/managed login

AWS supporting services
  ├─ ECR               Docker image registries
  ├─ Secrets Manager   OpenAI key, OIDC client secret, session secret
  ├─ IAM + SSM         controlled EC2 management without public SSH
  └─ Route 53 / DNS    hostname → EC2 Elastic IP
```

**Why Caddy and Nginx?** Caddy handles automatic HTTPS certificates with less certificate-management work. Nginx serves the built React files and forwards `/api/*` to FastAPI. `oauth2-proxy` sits between them so authentication is enforced for the *entire site*. These are internal Compose services; only Caddy binds public ports. We can simplify the proxy setup later if it becomes unnecessary overhead.

**Domain prerequisite:** Automatic public HTTPS and the Cognito OAuth callback need a stable hostname with DNS pointed at the instance. The registered domain is `podcastanalystagent.com`; choose the application hostname when configuring deployment. Use a Cognito-provided domain for its login pages; no Cognito custom-login domain is necessary for P−1.

## 3. Container packaging

Initial Compose services:

| Service | Image / role | Publicly reachable? |
|---|---|---|
| `edge` | Caddy: HTTPS → `auth` | **Yes**, ports 80/443 |
| `auth` | oauth2-proxy: validates Cognito login, then proxies to `web` | No |
| `web` | Multi-stage Docker build: Vite compiles React; Nginx serves artifacts and proxies `/api` | No |
| `api` | Python slim image: FastAPI / Uvicorn | No |

The *React build* and *FastAPI service* are our two application images in **Amazon ECR**, tagged by Git commit SHA (not only `latest`). Base proxy images can be version-pinned. A single `compose.yaml` connects the services over a private Docker network. Browser requests use same-origin `/api/*`, avoiding unnecessary CORS configuration.

When P0 starts, add persistent PostgreSQL/pgvector and the embedded Ladybug database. **Never store durable data solely inside a disposable container filesystem.** Use an EBS-backed volume or managed storage, with backup/snapshot planning. The Codex runtime is added only after the P−1 web stack is working.

**Local development:** Vite on port 5173 provides React HMR and proxies `/api` to FastAPI on port 8000. The VS Code **run full stack app** compound starts both; **serve front end** starts only Vite. FastAPI also serves a built React snapshot at `/app/`. See [local_development.md](local_development.md); Docker Compose remains available for checking the deployment image.

## 4. Infrastructure as code: AWS CDK

Use **AWS CDK v2 with TypeScript** in `infra/`, targeting **us-east-1**. The stack will provision:

- One appropriately sized **EC2** instance with Docker + Compose and SSM Agent configured via user data/bootstrap; EBS storage.
- An **IAM instance role** with only required ECR pull, SSM management, and scoped secret-read permissions.
- A **security group** allowing public **80/443 only**; no inbound SSH (`22`) or direct access to API/database/auth ports.
- **ECR repositories** for `web` and `api` images.
- An **Amazon Cognito User Pool**, app client, and Cognito-hosted login domain.
- **Secrets Manager** secrets/placeholders and non-sensitive configuration as needed.
- A **stable address** (Elastic IP + DNS A record, if a domain is available) and useful CloudFormation outputs (instance ID, ECR URLs, user pool/client IDs, app URL).

The account is the **owner's personal AWS account**, accessed with their existing AWS CLI credentials/profile. **Never commit local AWS credentials** or place AWS access keys inside containers. EC2 uses its **instance role**, not a copy of the developer's credentials.

The implemented `PodcastAnalyst-beta` stack and exact first-time commands are in
[aws_deployment.md](aws_deployment.md). The stack currently provisions the hello
world host and HTTPS routing; authentication and secrets are later steps.

CDK bootstrap is per AWS account/region. Use `cdk diff` before subsequent infrastructure changes. This path is for **infrastructure only**, not the routine code-edit loop.

## 5. Cognito: private, invite-only access

**Threat model:** If a public visitor can hit chat/ingestion endpoints, they can consume the owner's OpenAI credits. Protecting only the React login screen is **not enough**; the backend must also be inaccessible without authentication.

Planned sign-in design:

1. Cognito User Pool with **self-registration disabled**. Only the owner creates/invites user accounts (reviewers can receive accounts when needed). Enable MFA for the owner's account; apply reasonable session expiry.
2. Cognito **authorization-code/OIDC** flow with a configured **HTTPS callback**, e.g. `https://app.example.com/oauth2/callback`.
3. `oauth2-proxy` manages the OAuth flow and session cookie, using Cognito as the OIDC identity provider. Client secret and cookie-encryption secret remain on the server.
4. Caddy forwards all external requests to `oauth2-proxy`. The proxy permits authenticated sessions through to Nginx; unauthenticated users are redirected to login.
5. Nginx serves React and proxies `/api/*` to FastAPI **only inside Docker's private network**. No alternate public route may bypass the proxy. API endpoints, SSE streams, and processing actions are therefore covered by the same authentication gate.
6. Session cookies are `Secure`, `HttpOnly`, and appropriately `SameSite` configured. No model credentials or OAuth client secrets appear in React bundles.

**Important:** Cognito makes the site *access-controlled*, not undiscoverable; anyone who finds the URL might see the login page, but cannot enter the app or spend API tokens. For interview review, share the URL **plus a deliberately invited reviewer account**, rather than enabling open sign-up. Test with a fresh incognito browser and direct unauthenticated `/api/*` requests.

## 6. Secrets, cost, and permissions

- Owner's **OpenAI API key**: store as an AWS secret; retrieve on the server at runtime/startup. **Never** pass it to the browser, Docker build arguments, source control, or logs.
- Cognito **app-client secret** and oauth2-proxy **cookie secret**: same treatment. CDK may create the app client, but explicitly wire its generated client secret into protected runtime configuration; do not assume CloudFormation automatically copies it to Secrets Manager.
- Runtime secrets are mounted/read with restrictive permissions; avoid committing `.env`, tokens, credentials, or real hostnames containing sensitive identifiers.
- Restrict EC2 IAM access to the specific secret ARNs; use AWS CLI/SSM under the developer's personal identity, not a long-lived instance access key.
- Set AWS **Budget alerts** and OpenAI usage monitoring. Monitoring is not necessarily a hard spending cap; avoid unbounded ingestion or anonymous model calls.
- **Case-study constraint to resolve:** The brief calls for **free-tier resources only**. A personally funded EC2 instance and paid OpenAI API key do **not automatically satisfy that rule**. Before final submission, either clarify acceptability with AC or demonstrate a compliant free-tier path and document the costs. Do not silently claim the prototype is free-tier.

## 7. Deployment workflow: optimize for iteration

### One-time bootstrap

1. Select AWS region/profile and a domain; verify domain DNS control.
2. Scaffold CDK, Dockerfiles, `compose.yaml`, and a minimal React/FastAPI application.
3. `cdk bootstrap` and `cdk deploy`: create AWS resources and configure the EC2 host.
4. Set runtime secrets securely and invite the initial Cognito user.
5. Run the first application release; verify HTTPS, sign-in, app page, and `/api/health`.

### Every normal application release

For the current hello world app, run **`./scripts/deploy.py beta`**; see [the deployment guide](aws_deployment.md) for setup, rollback, and logs. The later React/authenticated release flow will extend it:

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

**Logs and diagnosis:** `docker compose logs -f api`, container health checks, and SSM execution output are sufficient for P−1. Add persistent log aggregation only if it provides clear value. For streaming, configure Nginx to disable buffering on SSE routes and verify that oauth2-proxy/Caddy flush events promptly.

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

The hello world EC2 host has no database yet. Establish retained storage before
adding PostgreSQL or LadybugDB; its current root volume is disposable.

## 8. P−1: definition of done

P−1 is **complete only when all are demonstrably working**:

- [ ] A fresh `cdk deploy` provisions a usable EC2 web environment in the personal AWS account.
- [ ] Local React + FastAPI hot reload works.
- [ ] A browser can reach the deployed React app over **HTTPS** at a stable URL.
- [ ] Anonymous visitors cannot access **either** the app or `/api/*` (including chat/ingestion endpoints); authorized Cognito users can.
- [ ] Public self-registration is disabled; an invited reviewer can sign in and sign out.
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
- [AWS Cognito user creation / disabling self-registration](https://docs.aws.amazon.com/cognito/latest/developerguide/how-to-create-user-accounts.html)
- [Amazon Cognito managed sign-in](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-how-to-authenticate.html)
- [oauth2-proxy configuration](https://oauth2-proxy.github.io/oauth2-proxy/configuration/overview/)
- [AWS Systems Manager Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html)
