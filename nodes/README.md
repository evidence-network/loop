# nodes/

One file per node, named `<node_id>.json`. Add yours by pull request; the `nodes` check must pass.

```json
{
  "node_id": "<64 lowercase hex: the mesh node id>",
  "key_id": "<64 lowercase hex: the capsules plugin's Ed25519 public key>",
  "operator": "<GitHub handle of the person who runs the node>",
  "mesh": "lab",
  "ask_route": "mesh",
  "disclosure": {
    "policy": "counterparties",
    "stranger_safe": ["checkpoints", "history_card/1"]
  },
  "host_build": "<mesh-llm build the node runs>",
  "plugin_version": "<capsules plugin version>"
}
```

- `ask_route: mesh` means: ask over the lab mesh, addressed by `node_id`.
- `disclosure.policy: counterparties` means records are answered only to the counterparty of that exchange; `checkpoints` and `history_card/1` are answered to anyone.
- The check refuses an entry whose `node_id` or `key_id` is not 64 lowercase hex, whose file name is not `<node_id>.json`, that repeats another entry's `node_id` or `key_id`, or that has a missing or unknown field.

Run it locally: `python3 scripts/check_nodes.py nodes`
