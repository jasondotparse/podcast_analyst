import { useEffect, useId, useRef, useState } from 'react'
import { ArrowCounterClockwiseIcon, PauseIcon, PlayIcon, XIcon } from '@phosphor-icons/react'
import WalkthroughNav from './WalkthroughNav.jsx'
import './Onboarding.css'
import './HowItWorks.css'

// These are explanatory traces, not live queries or API calls. Keep them aligned with /docs.
const CHAPTERS = [
  {
    label: 'Agent harness', title: 'A research agent powered by Codex App Server',
    intro: 'Codex supplies the agent loop, we supply the research procedures, data access, and interface. Follow a question from the browser to a tool call and back by pressing play below.',
    points: [
      ['One runtime for everything', 'One FastAPI worker owns a long-lived Codex App Server subprocess. Each app session gets its own thread; one turn runs at a time per thread. Codex manages context and decides which tools to call.'],
      ['Python does the application work', 'Dynamic tools are Python handlers inside FastAPI. No need for MCP! SQL and Cypher exploration are read-only agent tool calls. Seperate tools are used for saving theses, subscriptions, and RSS watchers.. Live web search can add outside context - we get this for free curtesy of openai with nothing more than a config.toml update.'],
    ],
    tools: ['vector_search', 'graph_query', 'postgres_database_query', 'add_thesis', 'add_subscription', 'register_rss_to_watcher'],
    note: 'Git-managed AGENTS.md and SKILL.md files teach research, feed discovery, and extraction behavior. The browser never receives the OpenAI key.',
    source: 'src/backend/chat.py',
    topology: 'harness',
    reference: ['Codex App Server', 'https://learn.chatgpt.com/docs/app-server'],
    nodes: [
      ['React', 'Ask a question', 'The browser posts a question to its app session. FastAPI checks session ownership before starting a turn.', 'POST /api/sessions/{id}/turns'],
      ['FastAPI', 'Own the boundary', 'The backend sends newline-delimited JSON-RPC through the Codex child’s stdin. A continuous reader routes replies and events by thread.', 'turn/start → stdio JSON-RPC'],
      ['Codex', 'Choose the next action', 'The model can ask for evidence, inspect tool results, and continue researching. Tool calls return to FastAPI for execution.', 'item/tool/call → Python handler'],
      ['Research tools', 'Read or save', 'A vector search, SQL query, or graph traversal returns structured evidence. Narrow write tools handle user-requested changes.', 'tool arguments → result → agent'],
      ['React', 'Stream the answer', 'FastAPI forwards text deltas, tool activity, and graph previews over Server-Sent Events. A terminal turn event ends the busy state.', 'SSE: delta · tool · graph · completed'],
    ],
  },
  {
    label: 'Data layer', title: 'Hybrid vector + graph RAG. Embedding-based retrieval grounds research in real quotes, then graph traversal adds context',
    intro: 'passage = the atomic retrieval unit. Claim = an attributed assertion grounded in that passage. PostgreSQL holds the records, LadybugDB makes their relationships traversable.',
    points: [
      ['A source of truth with ordinary joins', 'PostgreSQL + pgvector stores podcasts, episodes, ordered passages, embeddings, claims, and entities. It also holds theses, assessments, ingestion jobs, watchers, subscriptions, and alert records.'],
      ['An interpretation you can trace back', 'Claims preserve the assertion, speaker when known, and exact quote. write_claim accepts evidence only when the quote appears in a real passage after whitespace normalization. LadybugDB uses the same UUIDs and can be rebuilt from Postgres.'],
    ],
    note: 'Supports/challenges edges are assessed relationships, not verified facts. Unrelated assessments remain in Postgres. Canonical transcript and quote text stay there too.',
    source: 'src/backend/db/graph_schema.cypher',
    topology: 'data',
    nodes: [
      ['Episode', 'The source', 'An episode belongs to a podcast and carries its transcript, metadata, and optional source URL. Ordered passages reference this record.', '(Episode)-[:CONTAINS]->(Passage)'],
      ['Passage', 'Retrieval unit', 'Passages contain source text and a 1,536-dimensional embedding. Their IDs connect semantic search results back to canonical evidence.', 'passages: episode_id · ordinal · text · embedding'],
      ['Claim', 'Attributed assertion', 'A claim references a passage and stores a verbatim evidence quote, optional speaker, and investment-signal scores.', '(Claim)-[:EVIDENCED_BY]->(Passage)'],
      ['Thesis', 'What the analyst believes', 'A thesis describes a specific investment expectation. Assessments record whether a claim supports it, challenges it, or is unrelated, with a strength score.', '(Claim)-[:SUPPORTS|CHALLENGES]->(Thesis)'],
      ['Entity', 'The shared connection', 'Companies, people, technologies, and concepts connect claims and theses. Sharing an entity means shared subject matter; stance needs its own assessment.', '(Claim)-[:ABOUT]->(Entity)<-[:ABOUT]-(Thesis)'],
    ],
  },
  {
    label: 'Ingestion', title: 'Construct a world model one podcast at a time',
    intro: "TXT uploads, MP3 uploads, and watched RSS/Atom feeds enter the same durable job pipeline. It's just a list of python function that run in sequence.",
    points: [
      ['Fixed stages, visible progress', 'One in-process worker handles one episode at a time. Postgres records the current stage and outcome. Content hashes and source identifiers deduplicate episodes; retries reuse completed work. Partial claim extraction is cleared and rerun.'],
      ['Continuous monitoring, kept simple', 'Feeds are checked hourly while the pipeline is idle. Registration takes the newest episode, then later releases. New theses are also assessed against historical claims during idle time; that backfill does not send retrospective emails.'],
    ],
    note: 'Current scope: TXT, MP3, RSS/Atom. YouTube ingestion is deferred. The current pipeline does not produce passage timestamps; citations use available metadata and exact quotes.',
    source: 'src/backend/ingestion/ingestion_service.py',
    nodes: [
      ['Acquire', 'Upload or RSS', 'An upload creates an episode and durable job, returning HTTP 202. MP3 audio is stored so processing can resume after a restart.', 'deduplicate → episode + ingestion_job'],
      ['Transcribe + screen', 'Accept useful input', 'MP3s are converted with ffmpeg into 20-minute segments and transcribed with gpt-transcribe. Decisions API then screens the transcript for relevance and prompt injection; rejection or refusal skips the job.', 'transcribe → screen'],
      ['Chunk + embed', 'Make passages searchable', 'Split into roughly 750-word passages with 250-word overlap. Embed batches of up to 64 using text-embedding-3-large shortened to 1,536 dimensions.', 'chunk → embed'],
      ['Extract claims', 'Ground every assertion', 'A dedicated Codex thread reads the transcript through SQL and saves claims and entities through write_claim. Quotes must match a real passage.', 'extract_claims → validated Postgres records'],
      ['Rank + reflect', 'Judge signal and stance', 'Decisions scores claim specificity, materiality, and source standing, then assesses the claims against active theses. Results persist before graph publication.', 'rank → reflect'],
      ['Publish + notify', 'Make evidence useful', 'Project the episode and assessed relationships into LadybugDB, then check subscribed theses for qualifying new challenges and send email alerts.', 'project_graph → notify → completed'],
    ],
  },
  {
    label: 'Models + Decisions', title: 'Decisions API & LLM use in ingestion pipeline',
    intro: 'GPT-6-luna serves two different jobs in the app: both for driving the chat experience and ingesting new claims. The ingestion pipeline also calls the OpenAI Decisions API directly for typed choices and rubric scores.',
    points: [
      ['Codex handles the investigation', 'The configured default is gpt-6-luna with high reasoning effort. Its tool loop can explore evidence and synthesize a cited answer. Extraction uses a dedicated thread with a narrower set of tools.'],
      ['Decisions makes judgments explicit', 'Screening returns accept/reject choices. Ranking returns three rubric scores. Reflection returns supports/challenges/unrelated plus strength. Python turns these typed results into stored scores and pipeline decisions.'],
    ],
    note: 'Signal and strength are model judgments, not probabilities that a claim is true. Audio uses gpt-transcribe; semantic search uses text-embedding-3-large.',
    source: 'src/backend/ingestion/claim_ranker.py',
    reference: ['OpenAI Decisions API', 'https://developers.openai.com/api/docs/guides/decisions'],
    nodes: [
      ['Episode screen', 'Choice questions', 'Two questions ask whether the transcript is relevant to public equities and free of instructions aimed at the AI. Both must accept before processing continues.', 'on_topic: accept/reject · prompt_injection: accept/reject'],
      ['Claim ranking', 'Three scored axes', 'Each claim is scored against five anchored levels for specificity, materiality, and source standing. The weighted 0–4 results are scaled to 0–100.', 'specificity · materiality · source_standing'],
      ['Signal', 'A transparent calculation', 'The application computes the rounded mean of those three components. Components are retained so the formula remains inspectable.', 'signal = round((specificity + materiality + source_standing) / 3)'],
      ['Thesis reflection', 'Choice + strength', 'For each missing active thesis, Decisions evaluates the claim’s direction and how strongly it bears on that thesis. These assessments drive graph edges and alert eligibility.', 'stance: supports | challenges | unrelated · strength: 0–100'],
    ],
  },
  {
    label: 'Retrieval / RAG', title: 'Vector retrieval to kick off research, graph traversal to ground it in context',
    intro: 'Retrieval-augmented generation gives the agent source material to reason from. Vectors find similar language, the graph follows relationships, and SQL verifies the underlying records.',
    points: [
      ['Complementary questions', 'Vector search asks “what sounds relevant?” Cypher asks “what connects?” SQL asks “what exactly was said, by whom, and in which episode?” Together they support investigation across episodes and investment theses.'],
      ['The agent chooses the route', 'This trace is an example strategy, not a hardcoded chain. Codex can query any of the three, inspect results, and follow up. Graph queries can also produce the interactive graph cards seen in chat.'],
    ],
    note: 'Answers must distinguish a guest’s claim from verified fact and include exact quotes with speaker, episode, and source URL when available. Outside web corroboration is separate from podcast evidence.',
    source: 'src/backend/agent_tools.py',
    nodes: [
      ['Question', 'Illustrative research', 'Suppose you ask which podcast evidence challenges a thesis about AWS expansion. The agent decides which evidence it needs.', 'Example: “What could constrain AWS expansion?”'],
      ['Vector search', 'Find candidate passages', 'Embed the question with the same model and dimensions as the passages. pgvector orders them by cosine distance; the default returns five passages with metadata.', 'vector_search(query) → text + passage IDs + similarity'],
      ['Graph traversal', 'Follow the connections', 'Follow a passage to its claims, shared entities, and assessed thesis links. This can surface related evidence from other episodes.', 'graph_query(cypher) → entities + evidence + stance links'],
      ['SQL lookup', 'Check the receipts', 'Fetch canonical quotes, speakers, episode dates, scores, and joins. Model-written SQL runs as a SELECT-only role in a read-only transaction.', 'postgres_database_query(sql) → authoritative records'],
      ['Cited answer', 'Synthesize with limits', 'Explain which claims bear on the thesis, preserve attribution, and state gaps. Similar wording or a shared company alone does not establish a challenge.', 'claim + exact quote + speaker + episode + available URL'],
    ],
  },
  {
    label: 'Infrastructure', title: 'One AWS host - EC2 and docker do all the heavy lifting.',
    intro: 'Beta & prod run in us-east-1 on one EC2 virtual machine. Containers keep delivery repeatable while the deployment stays small enough to understand end to end.',
    points: [
      ['A compact application stack', 'Caddy terminates HTTPS. FastAPI serves the built React UI, runs Codex and the ingestion worker, and owns embedded LadybugDB. PostgreSQL + pgvector runs in a private Compose container on the same host.'],
      ['Evidence-driven email', 'A subscribed thesis qualifies for an alert when a new challenge has strength ≥75 and claim signal ≥60. Postgres snapshots evidence and recipients; boto3 sends through SES. Alert records are deduplicated by episode and thesis, and sent alerts are skipped.'],
    ],
    note: 'Host-mounted state survives app releases. It is on the instance’s root EBS disk, which is deleted on instance termination. This is a single-host demo, with no high-availability tier.',
    source: 'infra/lib/podcast-analyst-stack.ts',
    topology: 'infra',
    reference: ['AWS → Azure compute map', 'https://learn.microsoft.com/en-us/azure/architecture/aws-professional/compute'],
    nodes: [
      ['Route 53', '≈ Azure DNS', 'DNS points beta.podcastanalystagent.com at an Elastic IP, the AWS counterpart of a static Azure Public IP. Public ingress is limited to HTTP and HTTPS.', 'DNS → static IP → Caddy HTTPS'],
      ['EC2 + EBS', '≈ Azure VM + Managed Disk', 'One t3.medium runs Caddy, the app, and Postgres through Docker Compose. LadybugDB is embedded in the app process; persistent files live on the host disk.', 'EC2 host: edge · app / Codex / Ladybug · db / pgvector'],
      ['IAM role', '≈ Managed identity + RBAC', 'The instance role authorizes image pulls, parameter reads, and email sending. The app does not need copied AWS access keys.', 'instance identity → scoped AWS permissions'],
      ['Parameter Store', '≈ Key Vault secrets', 'SSM SecureString parameters hold runtime secrets such as the OpenAI key and shared login password. The host fetches them at deployment; they are not baked into the image.', 'secure parameters → runtime environment'],
      ['Amazon SES', '≈ Azure Communication Services Email', 'The backend uses SESv2 to send plain-text alerts with attributed claims, quotes, scores, and links back to the app. CDK configures the sender domain and DKIM records.', 'qualifying challenge → alert record → SES → inbox'],
    ],
  },
  {
    label: 'Delivery / CI/CD', title: 'Infrastructure as code. Releases are python script invocations.',
    intro: 'CDK provisions the environment; a separate deployment script ships application changes. Today, releases are developer-triggered: there is no GitHub Actions CI or automatic push-to-main deployment.',
    points: [
      ['Provisioning is separate from shipping', 'TypeScript CDK synthesizes CloudFormation, conceptually like Bicep producing an Azure Resource Manager deployment. cdk diff and cdk deploy handle infrastructure changes; ordinary app releases do not rerun them.'],
      ['Repeatable images, explicit verification', 'The multi-stage Docker build compiles React and packages FastAPI, ffmpeg, and pinned Codex with its helper. Tests run separately from deployment. The release script waits for containers and checks health and public HTTPS; failures return a nonzero exit code.'],
    ],
    note: 'Python tests, the frontend build, and CDK tests are available locally. Rollback reuses a previous image tag; it is manual. This single-host Compose rollout does not promise zero downtime.',
    source: 'scripts/deploy.py',
    nodes: [
      ['Build', 'From the local checkout', 'Run the deployment script after validation. Docker Buildx builds the current working tree for the host’s linux/amd64 architecture.', './scripts/deploy.py beta'],
      ['Push to ECR', '≈ Azure Container Registry', 'A release tag combines the Git commit, timestamp, and a random suffix. ECR stores immutable images, so the tag identifies the image used for a release.', 'commit + timestamp + suffix → immutable image tag'],
      ['SSM command', '≈ Azure VM Run Command', 'AWS Systems Manager instructs the EC2 host to pull the image and recreate Compose services. No inbound SSH or remote image build is needed.', 'SSM → docker compose up --wait'],
      ['Verify / roll back', 'Keep the release tag', 'The script checks the release and reports its tag and command ID. To roll back, explicitly deploy a previous release tag using the same script.', './scripts/deploy.py beta <previous-release-tag>'],
    ],
  },
]

// Positions form a left-to-right trace on the first row, then return along the second.
const POSITIONS = [[110, 65], [330, 65], [550, 65], [550, 205], [330, 205], [110, 205]]
const DATA_POSITIONS = [[110, 65], [330, 65], [550, 65], [330, 205], [550, 205]]
const INFRA_POSITIONS = [[110, 65], [330, 65], [110, 205], [330, 205], [550, 65]]
const RELATIONSHIPS = {
  data: [[0, 1, 'CONTAINS'], [2, 1, 'EVIDENCED_BY'], [2, 4, 'ABOUT'], [3, 4, 'ABOUT'], [2, 3, 'SUPPORTS / CHALLENGES']],
  infra: [[0, 1, 'DNS'], [2, 1, 'permissions'], [3, 1, 'runtime secrets'], [1, 4, 'send email']],
  harness: [[0, 1, 'HTTP'], [1, 2, 'JSON-RPC ↔'], [2, 3, 'tool call ↔'], [1, 4, 'SSE']],
}

function connection([x1, y1], [x2, y2]) {
  const dx = x2 - x1, dy = y2 - y1
  const inset = Math.min(dx ? 86 / Math.abs(dx) : Infinity, dy ? 37 / Math.abs(dy) : Infinity)
  return `M ${x1 + dx * inset} ${y1 + dy * inset} L ${x2 - dx * inset} ${y2 - dy * inset}`
}

function Mechanism({ chapter }) {
  const [active, setActive] = useState(0)
  const [playing, setPlaying] = useState(false)
  const marker = useId().replace(/:/g, '')
  const positions = chapter.topology === 'data' ? DATA_POSITIONS : chapter.topology === 'infra' ? INFRA_POSITIONS : POSITIONS
  const edges = RELATIONSHIPS[chapter.topology] ?? chapter.nodes.slice(1).map((_, i) => [i, i + 1, 'then'])
  const node = chapter.nodes[active]

  useEffect(() => {
    if (!playing) return
    const timer = setTimeout(() => {
      if (active === chapter.nodes.length - 1) setPlaying(false)
      else setActive(active + 1)
    }, 4000)
    return () => clearTimeout(timer)
  }, [playing, active, chapter.nodes.length])

  const select = (index) => { setPlaying(false); setActive(index) }
  return (
    <section className={`how-mechanism${playing ? ' is-playing' : ''}`} aria-label={`${chapter.label} diagram`}>
      <div className="how-map-heading">
        <span>{chapter.topology === 'data' ? 'Inspect the model' : chapter.topology === 'infra' ? 'Inspect the deployment' : 'Follow the flow'}</span>
        <span>Illustrated · no live calls</span>
      </div>
      <div className="how-map-scroll">
        <div className="how-map">
          <svg className="how-wires" viewBox="0 0 660 270" preserveAspectRatio="none" aria-hidden="true">
            <defs><marker id={marker} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 8 5 L 0 9" /></marker></defs>
            {edges.map(([from, to]) => <path key={`${from}-${to}`} d={connection(positions[from], positions[to])}
              className={to === active || (chapter.topology && from === active) ? 'is-active' : undefined}
              markerEnd={`url(#${marker})`} />)}
          </svg>
          {chapter.nodes.map(([name, tag], i) => (
            <button key={`${name}-${i}`} type="button" className="how-node" aria-pressed={active === i}
              onClick={() => select(i)} style={{ left: `${positions[i][0] / 6.6}%`, top: `${positions[i][1] / 2.7}%` }}>
              <span className="how-node-number">0{i + 1}</span><strong>{name}</strong><span>{tag}</span>
            </button>
          ))}
        </div>
      </div>
      <div className={`how-connections${chapter.topology ? ' has-topology' : ''}`} aria-label="Diagram connections">
        {edges.filter(([from, to]) => !chapter.topology || from === active || to === active).map(([from, to, label]) => (
          <span key={`${from}-${to}`}>{chapter.nodes[from][0]} <b>→ {label} →</b> {chapter.nodes[to][0]}</span>
        ))}
      </div>
      <div className="how-trace-controls" role="group" aria-label="Diagram playback">
        <button type="button" className="btn btn-secondary" onClick={() => {
          if (!playing && active === chapter.nodes.length - 1) setActive(0)
          setPlaying(!playing)
        }}>{playing ? <PauseIcon size={13} /> : <PlayIcon size={13} />}{playing ? 'Pause' : 'Play'}</button>
        <button type="button" className="btn btn-secondary" disabled={active === 0} onClick={() => select(active - 1)} aria-label="Previous diagram step">←</button>
        <span>{active + 1} / {chapter.nodes.length}</span>
        <button type="button" className="btn btn-secondary" disabled={active === chapter.nodes.length - 1} onClick={() => select(active + 1)} aria-label="Next diagram step">→</button>
        <button type="button" className="btn btn-secondary how-reset" onClick={() => select(0)}><ArrowCounterClockwiseIcon size={13} />Reset</button>
      </div>
      <div className="how-readout" aria-live="polite" aria-atomic="true">
        <h3>{node[0]} <span>/ {node[1]}</span></h3>
        <p>{node[2]}</p>
        <code>{node[3]}</code>
      </div>
    </section>
  )
}

export default function HowItWorks({ onClose }) {
  const [index, setIndex] = useState(0)
  const dialog = useRef(null)
  const title = useRef(null)
  const scroll = useRef(null)
  const chapter = CHAPTERS[index]
  const close = () => {
    // Remove modal inertness before the cabinet restores focus to its trigger.
    dialog.current.close()
    onClose()
  }

  useEffect(() => {
    const element = dialog.current
    element.showModal()
    return () => element.close()
  }, [])

  useEffect(() => {
    title.current?.focus({ preventScroll: true })
    scroll.current?.scrollTo(0, 0)
  }, [index])

  return (
    <dialog ref={dialog} className="how-dialog" aria-labelledby="how-title" onCancel={(event) => { event.preventDefault(); close() }}>
      <div className="onboarding how-it-works" ref={scroll}>
        <div className="onboarding-page how-page">
          <div className="how-masthead"><span>Podcast Analyst <span>/ How it works</span></span>
            <button type="button" className="btn btn-secondary" onClick={close}><XIcon size={14} />Close</button>
          </div>
          <nav className="how-chapters" aria-label="How it works chapters">
            {CHAPTERS.map(({ label }, i) => <button key={label} type="button" aria-current={i === index ? 'step' : undefined}
              onClick={() => setIndex(i)}><span>0{i + 1}</span>{label}</button>)}
          </nav>
          <div className="how-heading"><p className="how-eyebrow">Under the hood / {chapter.label}</p>
            <h1 id="how-title" ref={title} tabIndex={-1}>{chapter.title}</h1><p>{chapter.intro}</p>
          </div>
          <div className="how-content" key={index}>
            <Mechanism chapter={chapter} />
            <div className="how-explanation">
              {chapter.points.map(([heading, text]) => <section key={heading}><h2>{heading}</h2><p>{text}</p></section>)}
              {chapter.tools && <div className="how-tools" aria-label="Chat dynamic tools">{chapter.tools.map((tool) => <code key={tool}>{tool}</code>)}</div>}
              <p className="how-note">{chapter.note}</p>
              <div className="how-sources"><a href={`https://github.com/jasondotparse/podcast_analyst/blob/main/${chapter.source}`} target="_blank" rel="noreferrer">In the code ↗</a>
                {chapter.reference && <a href={chapter.reference[1]} target="_blank" rel="noreferrer">{chapter.reference[0]} ↗</a>}
              </div>
            </div>
          </div>
          <footer className="how-footer"><WalkthroughNav index={index} count={CHAPTERS.length}
            onPrevious={() => setIndex(index - 1)} onNext={() => setIndex(index + 1)} onFinish={close} finishLabel="Back to research →" /></footer>
        </div>
      </div>
    </dialog>
  )
}
