# Production email alert verification

November 12, 2026 (Central). Production: `https://podcastanalystagent.com`, release
`d77fd7dc5abc-20261113T012044-7f2f38`. Each run saved a thesis and subscribed
`holtkam2@gmail.com` through signed-in Chrome chat, then uploaded a `.txt` transcript
through the UI's **Upload podcast** dialog. No code, deployment, or threshold changes.

## Run 1: FT, "How AI is really changing jobs" (no alert, as designed)

- Thesis `b2a776e5-bbac-41ae-8774-21d4451cb2f0`: AI machine translation will not cut
  translator pay or speed up turnaround through 2028.
- Job `fb5eb283-1c0a-44da-b5a6-afd69e9cf470` completed in 2m 32s: 37 claims, all ranked.
- Six claims challenged the thesis; the strongest ("twice the pace and half the price")
  scored strength 95 but signal 50. No claim in the episode reached signal 60
  (max 50: a journalist relaying interviews scores low on source standing), so no alert
  row was created and no email was sent.

## Run 2: EE Times, "Enabling AI at the Speed of Light" (alert sent)

- Thesis `81dc3db3-8cbe-4e54-9fd6-9ff5a5c931d3`: Sivers Semiconductors will stay fab-light,
  making only ~10% of its lasers in Glasgow, through 2028.
- Job `1dd56d7a-ad20-4bb7-9684-c0a33e07501f` completed in 3m 14s: 39 claims, 22 with
  signal ≥60 (max 81).
- One qualifying match: the CEO's claim that Sivers moved from ~10% Glasgow production to
  a 1-to-2 (or 2.5) in-house-to-partner model. Challenge strength 99, signal 81
  (specificity 71, materiality 72, source standing 100).
- Alert `cd879680-8e13-4ef6-b25d-b58f6def9a86`: `sent` to `holtkam2@gmail.com` at
  03:16:49 UTC. CloudWatch `AWS/SES` showed Send = 1 and Delivery = 1 in the 03:15 UTC
  window.

## Notes

- The notifier logs nothing on a successful send or when no claim qualifies. Evidence
  comes from the `alerts` row and SES metrics, not app logs.
- `aws ses get-send-statistics` lagged behind CloudWatch for this send.
- Both test theses, subscriptions, and episodes remain on prod.
- Evidence SSM commands: `2dfeb8c6-1073-4609-8197-7ea1ba27fce6` (run 1),
  `a8e1f15f-3e04-4d79-b975-fcf88242eb7c` (run 2).
