# Reporter fixtures

What the reporter node's capsules plugin answers on
`POST /api/plugins/capsules/tools/mesh_evidence_request` (a 200 body:
`{answer, request_digest, verification, held}`, `mesh_evidence_bridge.rs`), one
file per outcome. The `verification` members follow
`evidence_answer::verify_response` on capsules main (424f2c7). The `answer`
is in the shape a node sends, with stand-in values, not recorded from a node:

- an artifact is the flat evidence-interop/0 envelope, its members at the top
  of `answer` (no wrapper): `profile`, `request_digest`, `subject`, `anchor`
  (`digest`, `freshness`), `artifact_digest`, `responder`, `issued_at`, and
  `derivation` / `verification` when present, then `sig` and `artifact`
  (base64url in JSON). That is `Envelope::signed_members` and
  `Envelope::encode` of the `evidence-request` crate 0.2.0, the version capsules
  main pins (`envelope.rs`). Here `artifact_digest` is the real SHA-256 of
  `artifact`; `responder` and `sig` are stand-ins and do not verify;
- a refusal is flat too: `issued_at`, `key_id`, `reason`, `request_digest`,
  `sig` (`refusal.rs`, `Refusal::to_json`).

The reporter stores the reply as bytes and reads only `verification`; the
plugin is the verifier. A live run replaces these with what the lab nodes
actually answer.

- `verified.json`: an artifact that verified under the node's announced key.
- `verified_history_card.json`: a `history_card/1` artifact that verified; it
  carries `halves`, not `checkpoints`.
- `refusal.json`: a refusal the node signed over the reporter's request.
- `no_checkpoint_yet.json`: a signed `coverage_unsatisfiable`: the node has cut no
  checkpoint yet (the first is cut 300 s after activity). Recorded as
  `no_checkpoint_yet`, not as a refusal and not as an absence.
- `no_announced_key.json`: the reporter's host announces no key for the node.
- `not_evidence.json`: a reply that is neither a verified artifact nor a signed refusal.
- `absence.txt`: the 502 body the host sends when the peer cannot be reached
  or never answers (`could not reach peer`, `peer does not answer evidence requests`).
  `tests/test_offline_retry.py` serves the other 502 bodies the plugin sends
  inline: `peer does not answer evidence requests` (recorded as `timeout`) and
  `peer returned malformed response` (`not_evidence`, an answer: not asked again).
