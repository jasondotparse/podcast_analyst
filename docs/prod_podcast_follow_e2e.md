# Production podcast follow verification

November 12, 2026 (Central). Requested: Big Technology, TBPN, and Invest Like the Best.
Production: `https://podcastanalystagent.com`, release
`d77fd7dc5abc-20261113T012044-7f2f38`.

All three ingestions and UI graph checks passed. Registered feeds ingest the newest episode and future
releases; they do not backfill the archive. No runtime changes or restarts.

## Big Technology

- Registered through signed-in Chrome: `https://feeds.megaphone.fm/LI3617121267`.
- Episode: **Storm Clouds Ahead For Apple?, Debating Amazon’s Bee Frontier, AI as UI** (November 11).
- Episode ID: `e712e3b8-8f85-4a40-8472-27c27191355a`.
- Job: `ef7faa5c-ecf6-434c-86d2-0478ccb8044a`, queued at 7:53:23 PM Central.
- UI displayed transcription then claim extraction. App and Postgres healthy;
  authenticated health/readiness passed.
- Completed 8:00:37 PM (7m 13s): 12,964 transcript words, 26/26 embedded passages,
  54/54 ranked claims; every quote matched its passage and was within 250 words.
- Episode graph API: 55 nodes / 54 links, all 54 claim IDs exactly match Postgres,
  no truncation. Claim `91e48bce-e3e7-458b-a8ca-d4cf9a835a58` neighborhood: 5 nodes / 4 links.
- Chrome: recent ingestion showed completed; research returned the MG Siegler
  Apple gross-margin claim with signal 63 and a graph card. Opened it, selected
  the claim, checked quote/speaker/show/date/source, and expanded to Apple,
  Apple Services, Gross margin, and the source episode (5 nodes / 4 links).
- Audit SSM: `bd85ab57-a512-44e2-99c4-bddb69558e97`; healthy containers and targeted
  logs: `05df0525-c299-48de-ba9b-bf723d73204a`. No new ingestion errors observed.

## TBPN

- Registered through Chrome: `https://feeds.transistor.fm/technology-brother`.
- Episode: **Jensen Huang Resigns as NVIDIA CEO | Diet TBPN** (November 12).
- Episode ID: `c8a0fe2d-87e0-48b9-85c9-6ce856d43f39`.
- Job: `76543df4-f94e-43f4-90f3-d37b078df34a`, 8:02:29–8:06:03 PM (3m 34s).
- Published-transcript path: 6,150 words, 12/12 embedded passages, 42/42 ranked
  claims; every quote matched its passage and was within 250 words.
- Episode graph API: 43 nodes / 42 links, all 42 claims exactly match Postgres,
  no truncation. Health and readiness passed.
- Chrome: recent ingestions showed completed; research returned claim
  `776cf5b4-d445-4517-bf38-a3f17ad7c31a`, attributed to Colette Kress (signal 74).
  Opened the 5-node / 4-link card and checked quote, speaker, podcast, episode,
  date, source, three ABOUT edges, and IN_EPISODE. Claims are attributed source
  assertions; this test does not independently verify the episode's allegations.
- Final audit SSM: `d76ffba5-a12a-45d7-aa71-8156348014b7`.

## Invest Like the Best

- Registered through Chrome: `https://feeds.megaphone.fm/CLS2859450455`.
- Episode: **Zach Reitano - Building Ro and Why Healthcare Should Be 50% of GDP -
  [Invest Like the Best, EP.500]** (November 11).
- Episode ID: `b97b5a86-5572-4275-9550-d6ae20e8bdb4`.
- Job: `325b9542-988a-4553-84ab-3409ee064723`, queued at 8:13:32 PM.
- Completed at 8:19:56 PM (6m 24s): 15,912 transcript words, 32/32 embedded
  passages, 68/68 ranked claims. Every quote matched its passage and was within
  250 words. UI showed transcription, extraction, then completed in the drawer.
- Episode neighborhood correctly capped at 60 claims (61 nodes / 60 links,
  `truncated=true`); these matched the top 60 canonical claims. Independent
  per-claim neighborhood checks confirmed all 68 claims link to the episode.
- Chrome research returned the Ro growth claim, signal 89, and counted all 68
  graph claims. Opened its 3-node / 2-link card and selected the claim: quote,
  Zach Reitano attribution, date, source, Ro ABOUT edge, and IN_EPISODE matched.
- Sample claim: `c53bf144-8015-4172-a2b9-1ca78ddf8c1d`.

## Final health and outcome

- All three watcher rows remain active for hourly checks.
- All 164 new claims are ranked, have valid quotes, and link to their source
  episodes in the graph. Full audit: `c8745ab7-617d-4457-8d66-286d81114097`.
- App/Postgres healthy; authenticated health/readiness passed. Targeted logs
  showed no new warnings/errors. Final health SSM: `e8fc5a1f-f4b7-43fa-ab01-927805424301`.
- No new ingestion or graph bug observed. No application code, deployment,
  schema, or infrastructure change was needed.
