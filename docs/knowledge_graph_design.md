# Knowledge graph view

Implemented November 11, 2026 from the supplied knowledge-graph design. This is a
read-only app view using the backend's existing Ladybug handle, with no schema,
pipeline, container, or infrastructure changes. Ladybug Explorer is not embedded:
it cannot share the running writer's database file and cannot supply Postgres labels.

## Shared model

`GraphView` (`src/backend/graph_view.py`) builds `{nodes, links, truncated}` for
both REST and SSE. All Cypher stays in `GraphClient`, all SQL in `PostgresClient`.
Nodes have `id`, `type`, `label`, `detail`, and a Postgres `signal` for claims.
Claims include verbatim quote, speaker, podcast, episode, publication date, and
source URL. Entities take their name and kind from the graph. Missing Postgres
claims, theses, and episodes are dropped with their edges.

The four visual types are Thesis (ink), Claim (gray, sized by signal), Entity
(aqua), and Episode (yellow). SUPPORTS edges are blue; CHALLENGES are red and
dashed; both have arrowheads and width by strength. ABOUT and IN_EPISODE are
gray hairlines. Passage nodes and MENTIONS are omitted; claim-to-passage-to-episode
paths become IN_EPISODE. A text legend accompanies every canvas.

## API and limits

- `GET /api/graph`: all theses, their entities, up to 150 distinct stance claims
  ordered by strongest stance, then those claims' episodes.
- `GET /api/graph?thesis_id=<uuid>`: the same for one thesis.
- `GET /api/graph/neighbors?node_id=<uuid>`: the node plus at most 60 neighbors;
  non-claims first, then claims by descending signal (NULL last).
- Unknown thesis/node or Passage: 404. Invalid UUID: 422. Empty overview: empty lists.
- `subgraph(ids)` deduplicates, keeps the first 300 IDs, hides Passages, batch-loads
  labels on one Postgres connection, and returns all visual edges among retained nodes.
  Caps set `truncated`; expansion also caps the accumulated UI graph at 300 nodes.

Routes inherit `/api` password authentication. Fixed view queries run synchronously
on the app's event loop; offload only if measured stalls justify it.

## UI and chat

The cabinet owns an open/closed graph panel, leaving the chat and SSE mounted.
`GraphPanel` fetches fresh data on open, supports thesis selection, source details,
neighbor expansion, and reset. Merging preserves existing node objects/positions;
link keys normalize force-graph's object endpoints. Each canvas copies the payload
so layouts never mutate another card or the graph that a card opens.

`GraphCanvas` is presentational and lazy-loads `react-force-graph-2d` 1.29.2.
Thesis/episode labels are always drawn; other labels appear above 2x zoom. The first
settled layout fits the canvas. ResizeObserver supplies dimensions. Below 700 px,
the drawer fills the screen and the detail panel stacks below the canvas.

`ChatHarness.chat_tool_call` scans a successful `graph_query`'s Cypher and output
for UUIDs, then calls `subgraph`. A nonempty link set emits `graph` with `call_id`
and the shared payload before the tool result returns. Background threads, failures,
and linkless results emit no card. A preview exception only logs a warning.
The frontend deduplicates replayed cards by call ID. Clicking a card node opens its
details in the cabinet; the card's button opens the same subgraph.

Untrusted tooltips use a DOM element with `textContent`; the evidence panel uses
React text; source links require http(s). Node and connection buttons provide a
keyboard alternative to the canvas. Escape closes and restores trigger focus.

## Verification

- Backend: 63 tests passed against disposable Postgres databases and temporary
  Ladybug files, including ordering/caps, derived edges, canonical labels/signals,
  stale rows, hidden Passages, UUID/status/auth checks, and chat preview isolation.
- Frontend: production build passed; GraphCanvas is a separate ~69 KB gzip chunk.
- Local Chrome: overview rendered 93 nodes / 233 links (71 challenges, 18 supports,
  83 ABOUT, 61 IN_EPISODE); claim evidence matched Postgres. Expanding the selected
  claim added three entities (96 / 236); Reset restored 93 / 233. A single-thesis
  API view contained 47 nodes; its claim neighborhood contained eight.
- Live local chat: graph_query produced a four-node / three-link SSE preview before
  the final answer; its total stance count (89) matched the overview. Introductory
  assistant messages may precede a preview. In the browser, a five-node / three-link
  card opened the same subgraph, showed a claim quote, and restored focus to its
  button on Escape while the answer continued streaming.
- A temporary React render check verified escaped markup, omitted javascript:
  sources, allowed https sources, textContent tooltips, isolated canvas copies,
  and link deduplication with object endpoints. No frontend test runner was added.
- Local Chrome at 375 x 800: the drawer filled the viewport, the thesis picker
  remained readable, and details stacked below the canvas. The first check exposed
  a compressed picker; giving its label a full toolbar row fixed it.
- Beta combined release `ffe5b40080e2-20261112T013414-fe48be`: authenticated overview
  56 nodes / 119 links (26 challenges, 21 supports, 33 ABOUT, 39 IN_EPISODE),
  single-thesis view 26 nodes, claim neighborhood six nodes. Anonymous reads
  returned 401. Final JS and repaired mobile stylesheet were verified over HTTPS.
  A real chat graph_query emitted a four-node / three-link preview before its final
  answer, whose stance count (47) matched the overview. Final overview API response:
  278 ms; time to first canvas draw was not measured.
- Remaining manual checks: literal-markup tooltip hover, opening during a new
  ingestion, and beta browser rendering (the test browser remained at sign-in).
  Untrusted-text rendering was checked separately with React as described above.
