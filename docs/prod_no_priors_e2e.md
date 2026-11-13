# Production No Priors end-to-end test

November 12, 2026, Central time. **Ingestion and research passed; the graph defect
found here was subsequently fixed and verified on prod (see follow-up below).**
Tested `https://podcastanalystagent.com` in signed-in Chrome, release
`b86d2d782f87-20261113T003204-eb80c2`. No application changes or restarts were made.

## Follow and ingestion

- Submitted exactly: `Follow the No Priors podcast for new episodes`.
- The agent verified and registered `https://feeds.megaphone.fm/nopriors` at 6:42:12 PM.
- The watcher logged the newest episode queued at 6:42:29 PM:
  **Powering AI-Driven Drug Discovery with President of Isomorphic Labs Max Jaderberg**, published November 12.
- Job `ef239387-010e-4d08-9587-60e6858786e9`; episode `21a0dffb-77ef-4461-ad70-5d9a49dec00f`.
- MP3 transcription, screening, chunking, embedding, extraction, ranking, reflection,
  projection, and notification finished at 6:46:03 PM, about 3 minutes 36 seconds after job creation.
  The UI showed transcription and extraction progress, then cleared the status bar.
- Final job status: `completed`, step `notify`, no error. Targeted container logs showed
  registration, queueing, and extraction completion, with no matching warnings or errors.
- Postgres: 7,515 words / 42,179 characters; 15 passages, ordinals 0–14, all embedded;
  39 claims, all ranked, signal 26–71; 49 entities and 110 claim/entity links.
- Every claim quote matched its passage after whitespace normalization; all were at most
  250 words. Thirty claims had a speaker; nine retained unknown attribution.
- All 39 assessments were unrelated to the existing Amazon thesis. No alert row was created.

## Graph and research

- The episode-neighborhood API returned exactly the same 39 claim IDs as Postgres,
  with 39 `IN_EPISODE` links and no truncation. Two high-signal claim neighborhoods
  contained the expected episode, entities, quotes, speaker, score, date, and source.
- Asked what AlphaFold 3 predicts beyond protein structure. The agent used vector search
  and SQL, correctly noted that the transcript does not specifically name version 3,
  quoted the discussion of protein interactions, and explained the time saved over
  X-ray crystallography. Its source URL and quote matched the ingested evidence.
- Asked whether Isomorphic Labs sells/licenses the model and how it uses the engine.
  The agent used SQL and Cypher, quoted that the engine stays in-house, and explained
  its owned pipeline and pharma/biotech partnerships without inventing financial terms.
- Opened that answer's graph card in the cabinet: five nodes / four links, comprising
  the claim, episode, Isomorphic Labs, Drug discovery, and Pharmaceutical partnerships.
  Selected the claim and visually checked the evidence, Max Jaderberg attribution,
  signal 69, publication date, source link, and three `ABOUT` / one `IN_EPISODE` connections.

## Graph overview defect observed on prod

Before this episode was projected, **All theses** showed nine nodes / two links but
omitted the saved Amazon thesis and its edges. Authenticated `/api/graph` returned
the same omission: six entities, two older BG2 claims, one episode, and only two
`IN_EPISODE` links. Independent agent queries confirmed the same thesis ID in both
stores (`b6692792-c98e-4f28-8ad3-6ffb6558b7bd`), two `SUPPORTS`, and six Thesis `ABOUT`
edges in Ladybug. The production follow-up below resolves this discrepancy.
No Priors being absent from the thesis-focused overview is expected because its
assessments were all unrelated. Its chat subgraph and episode neighborhood were correct.

Follow-up diagnosis isolated `GraphClient.view_nodes`' generic lookup:
`MATCH (n) WHERE n.id IN $ids RETURN n.id, label(n), n.name, n.kind`.
On prod, filtering to the thesis UUID returned a different Claim UUID
(`8fc5dd22-457f-4ae5-b075-7e58aeaf8c52`); another run returned a Passage.
Sequential typed `MATCH (t:Thesis) WHERE t.id IN [...]` returned the correct thesis,
and a typed traversal returned its six ABOUT edges. The Postgres label lookup also
returned the correct description. `GraphView.subgraph` therefore skips the requested
thesis when it is absent from those generic lookup rows, then requests edges only
among retained nodes, removing all links to the omitted thesis from the API response.
The browser receives neither the thesis nor those links. The proposed correction is
type-specific node lookups; the underlying Ladybug 0.15.3 behavior was not reproduced
in a small fresh local graph. No runtime code, data, or deployment was changed
during that diagnosis.
Diagnostic SSM commands: `9623d880-9679-415e-9871-5179b7cae098` (labels/API),
`136b9ece-5a07-4e1e-a77e-c8a11943e95b` (generic/typed queries),
`e9d95225-372a-4410-b52f-eb6d31db09f1` (sequential confirmation).

Evidence: SSM baseline `8297382b-f2a8-4007-8ba2-190580109c7f`, completed-data/graph audit
`80f54bd6-c58b-4c5e-b97d-818c11ddc2bd`, final logs/status
`9557e640-09a5-4aa8-b49c-adc91df89afd`. The watcher remains active in production.

## Explicit lookup fix: localhost and beta

`GraphClient.view_nodes` now uses explicit Episode, Passage, Claim, Thesis, and
Entity lookups. All 73 backend tests passed. Localhost's five theses (93 nodes /
233 links) and beta's four theses (60 nodes / 123 links) matched Postgres, including
every displayed relationship in all thesis-filtered and neighborhood views.
Chrome checks covered the overview, thesis/claim details, expansion, and Reset.
Beta release `d77fd7dc5abc-20261113T011017-90d64e` was built from the fix branch's
working tree, based on `d77fd7d`; no migrations or production deployment were made.
SSM deployment `fc63c0a6-cbe5-41af-9c74-608e2e14ba7b`; canonical graph audit
`138ef62a-61a3-4ddf-a644-65330a69237d`.

## Production follow-up: resolved

The initial node-only production deployment exposed another generic query:
ABOUT-edge selection intermittently omitted all six thesis/entity links. Explicit
endpoint types now cover ABOUT selection and neighborhood traversal too. All 73
backend tests passed again, with persisted relationship/neighborhood assertions;
localhost's graph audit and beta's full audit passed before the final prod release.

- Final beta: `d77fd7dc5abc-20261113T011925-080cf5`; audit
  `2b63f1d1-f0ef-4f4d-a11c-b5dce12e030b`.
- Final prod: `d77fd7dc5abc-20261113T012044-7f2f38`; deployment
  `3a1deae2-67a8-4723-b7af-9b0bf5a74aa6`.
- Three consecutive prod audits matched Postgres: overview/filter 10 nodes / 10
  links (one thesis, six entities, two supporting claims, one episode), and thesis
  neighborhood 9 / 8. Sample entity/claim/episode neighborhoods also matched.
- No Priors retained its 39 claims and 39 episode links. Invalid/unknown route
  behavior and anonymous 401 protection passed. Audit command:
  `8eefcdf7-7373-4ae8-8137-fa4c20fe6f53`.
- Chrome showed all six ABOUT and two SUPPORTS connections, correct claim evidence,
  expansion to 13 / 13, and Reset to 10 / 10. A read-only research turn returned
  both supporting claims with exact quotes, scores, and source links, and an 11 / 11
  graph preview that opened in the cabinet with the Amazon thesis present.
- The deployed graph-client checksum matched the tested source
  (`2ce99aa95751b34442ae1054b1721ea8599563210f039eac1e2c43684a06086a`);
  app and Postgres remained healthy. No schema or infrastructure changes were made.
