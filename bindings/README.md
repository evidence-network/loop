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

- `subjects` is sorted, has no repeats, and holds at least two subjects.
- `status` is `verified`, `revoked` or `not_checked`.
- `verified` needs `verifier` to name a tool and the exact pin `versions.lock`
  gives it.
- `not_checked` cites the open issue that owns the verifier
  (`<owner/repo#N>`).
- Only `verified` groups the subjects (`bound_groups` in
  `scripts/history_cards.py`). `revoked` and `not_checked` never do.

Check: `python3 -B scripts/check_binding.py bindings`.
