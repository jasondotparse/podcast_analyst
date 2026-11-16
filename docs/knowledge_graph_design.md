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

`GraphClient.view_nodes` looks up each node table explicitly, with parameterized
IDs. Episode, Passage, Claim, and Thesis return NULL name/kind; Entity returns its
stored properties. This avoids an untyped Ladybug 0.15.3 lookup observed on prod
returning a different node ID/table for the requested thesis. Passage lookup is
retained so the neighborhood endpoint can continue rejecting hidden passages.
ABOUT-edge selection and neighborhood traversal also use explicit endpoint types;
the remaining generic relationship lookup intermittently omitted thesis ABOUT
edges on prod even after the node lookup was corrected.

The original four visual types are Thesis (pale violet with a soft halo), Claim (gray, sized
by signal), Entity (violet), and Episode (a hollow ring). SUPPORTS edges are violet;
CHALLENGES are muted red and dashed; both have arrowheads and width by strength.
ABOUT and IN_EPISODE are dim hairlines. Colors are Nocturne tokens, listed in
`graphStyle.js`; the canvas resolves them once with `getComputedStyle`. With a
node selected, unrelated nodes fade to 25% and links that don't touch it to 12%. Passage nodes and MENTIONS are omitted; claim-to-passage-to-episode
paths become IN_EPISODE. A text legend accompanies every canvas.

Coverage adds Debate (accent-400, radius 7), Question (accent-200, radius 5), and Driver
(background with accent-500 outline, radius 4). HAS_DEBATE/HAS_QUESTION use accent-700/600;
AFFECTS uses neutral-600. The existing generated legend includes each type. Debate details show
both stored views; question details name the parent debate. All lookups remain explicitly typed.
The thesis view also includes active debates, questions and linked drivers for every entity
already selected. Rebuilding a pack deletes its historical node IDs, merges the active tree, and restores active
INFORMS evidence. INFORMS edges (accent-300, arrowheads and width by strength) link Claim → Question
with stored stance and strength; all stances including neutral appear at strength ≥25. Evidence never
recreates a retired question. Claims informing the selected questions share the existing cap with
thesis stance claims, ordered by their strongest edge. Neighbor traversal includes INFORMS.

## API and limits

- `GET /api/graph`: all active theses, their entities, up to 150 distinct stance claims
  ordered by strongest stance, then those claims' episodes. Theses archived with
  `update_thesis` are left out here and, through `graph_labels`, everywhere else
  (`thesis_id` view 404, neighborhoods, chat previews); their graph nodes and edges stay.
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

**Question evidence — November 15, 2026:** local and authenticated beta Playwright flows rendered
INFORMS evidence in the working-thesis view (294 and 373 displayed INFORMS links respectively
after the first synthetic interview; existing view caps still apply). Backend tests verify neutral
edges at strength 25+, exclusion at 24, strongest-first shared claim cap, pack rebuild preserving
kept questions' evidence, and retirement removing evidence without recreating the question.
Research chat read the stored claims/quotes and correctly flagged the initial reversed lean labels.
The final calibrated run rendered 301 local / 372 beta INFORMS links, with the cap notice visible.
See [reflection verification](etl_design.md#verification) for distributions and prompt calibration.

**Coverage packs — beta, November 15, 2026:** release `5412f586dedd-20261115T234509-5a466f`
(working tree; SSM deployment succeeded, app/db healthy). Playwright signed in through the login
page, saved the Appendix pack, and checked the two debates, three questions and seven drivers
in the thesis graph. Re-saving retained all six thesis/debate/question IDs. A first terse final
reply prompted a tool-description correction; the recheck read back each question and its drivers.
The original D2 counter-view over-negated the delivery gains and remained unchanged on re-save;
its exact wording is visible for analyst review. No browser page errors.

**Coverage packs — local, November 15, 2026:** 77 backend tests passed with local Postgres;
frontend build passed. Playwright saved the Appendix pack through chat without clarification:
D1 held the first two questions, D2 the third, with seven drivers. The graph rendered all
three new node/edge types and both debate views. Editing Q2 retired its row, created the
Trainium question, and preserved Q1/Q3 IDs; the follow-up listed each active question's drivers.
The first readback combined D1's questions/drivers. D2's generated counter-view said delivery/
automation would not expand margins, rather than the supplied example's gains being offset.
Stored questions matched the fixture; no browser page errors. Email was disabled locally.

- Production fix verified November 12, 2026 (Central), release
  `d77fd7dc5abc-20261113T012044-7f2f38`: the Amazon thesis now appears with six ABOUT
  links and two SUPPORTS links. Overview/filter: 10 nodes / 10 links; thesis
  neighborhood: 9 / 8. Three consecutive comparisons matched Postgres for every
  displayed node and edge, including sample Entity/Claim/Episode neighborhoods.
  No Priors retained all 39 claims and episode links. Chrome verified thesis and
  claim details, expansion to 13 / 13, Reset to 10 / 10, and a live research answer
  whose 11 / 11 graph card opened with the thesis intact. All 73 backend tests
  passed after adding persisted relationship/neighborhood assertions. The same
  code passed localhost and beta before this production release.
- Explicit node lookup fix, November 12, 2026 (Central): all 73 backend tests passed,
  including mixed node types after deletion/reopen, existing graph labels/edges,
  caps, stale rows, and route checks. Localhost showed all five theses in 93 nodes /
  233 links; beta showed all four theses in 60 nodes / 123 links. Every returned
  node ID/type/label and displayed edge matched Postgres for the overview and each
  thesis's filtered and neighborhood views. Signed-in Chrome checks covered thesis
  selection, evidence, expansion, and Reset. Beta's AWS view expanded from 14 / 17
  to 18 / 21 and reset to 14 / 17. Unknown/invalid routes and beta's anonymous 401
  were checked. Beta release: `d77fd7dc5abc-20261113T011017-90d64e`.
  The later production verification above also covers typed edge/neighborhood queries.
- Prod observation, November 12, 2026 (Central): No Priors episode/claim neighborhoods matched
  Postgres and its chat subgraph rendered correctly in the cabinet. With the prior implementation,
  the All theses overview omitted an existing thesis and its stance/ABOUT edges,
  although read-only queries confirmed them in both stores. Follow-up diagnosis
  found `GraphClient.view_nodes`' untyped node lookup returning the wrong node for
  the thesis UUID on prod; typed Thesis queries and Postgres labels were correct.
  `GraphView.subgraph` consequently dropped the thesis and its incident links. See the
  [end-to-end test record](prod_no_priors_e2e.md) for evidence and reproduction context.

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
