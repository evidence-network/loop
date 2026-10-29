# bindings/

Controller binding verdicts: one file per controller, named
`<controller_key_id>.json`. This repository only reads them. A verdict is
written by a binding verifier pinned in `versions.lock`. None is pinned
today (Controller Binding v0, action-state-group/agent-action-capsule#432),
so this folder holds no verdicts yet.

```json
{"profile": "binding-verdict/0",
 "controller_key_id": "<64 lowercase hex>",
 "subjects": ["<node_id or subject id>", "..."],
 "status": "verified",
 "verifier": "<name>@<pin>",
 "checked_at": "YYYY-MM-DDTHH:MM:SSZ",
 "cites": null}
```

- `controller_key_id` is the sha256 of the controller binding, the digest a
  node's public commitment carries. It is never the controller's public key
  (for a Nostr controller, never the npub or its hex). A binding is optional:
  a node without one joins as usual and is not grouped.
- `subjects` is sorted, has no repeats, and holds at least two subjects.
- `status` is `verified`, `revoked` or `not_checked`.
- `verified` needs `verifier` to be `binding-verifier@<pin>`, at the exact pin
  `versions.lock` gives `binding-verifier`. No other pinned tool is a binding
  verifier.
- `not_checked` cites the open issue that owns the verifier
  (`<owner/repo#N>`).
- Only `verified` groups the subjects (`bound_groups` in
  `scripts/history_cards.py`), and only when each subject's commitment
  carries `controller_binding.digest` equal to `controller_key_id`.
  `revoked` and `not_checked` never do.
- A verdict carries no signature of its verifier. Until a verifier signs its
  verdicts, a verdict is trusted on the review of the pull request that adds
  it.

Check: `python3 -B scripts/check_binding.py bindings`.
