# nodes/

The commitment list: one file per node, named `<node_id>.json`. A node joins by
pull request and leaves by pull request. The `nodes` check must pass.

## Join

Open a pull request adding `nodes/<node_id>.json`:

```json
{
  "node_id": "<64 lowercase hex: the mesh node id>",
  "key_id": "<64 lowercase hex: the capsules plugin's Ed25519 public key>",
  "operator": "<GitHub handle of the person who runs the node>",
  "consent": "<the operator statement below, word for word>",
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

- `operator` is the contact for the node: a GitHub handle, written without `@`.
  It is the only contact the list holds. The check refuses anything shaped like
  an email address or a phone number; the list holds no personal data.
- `consent` is the operator statement. By adding it, the operator agrees to it:

  > I run this node. The evidence network's daily reporter, signing with the key in reporter/reporter.json, may ask it checkpoints and history_card/1 once a day. What it answers, or that it did not answer, is published in the evidence-network/loop repository, including after that repository is public. I leave by removing this entry by pull request: the asks stop from the next run, and reports already published stay.

  The value goes in the entry exactly as this JSON string:

  ```
  "I run this node. The evidence network's daily reporter, signing with the key in reporter/reporter.json, may ask it checkpoints and history_card/1 once a day. What it answers, or that it did not answer, is published in the evidence-network/loop repository, including after that repository is public. I leave by removing this entry by pull request: the asks stop from the next run, and reports already published stay."
  ```

- `ask_route: mesh` means: ask over the lab mesh, addressed by `node_id`.
- `disclosure.policy: counterparties` means records are answered only to the counterparty of that exchange; `checkpoints` and `history_card/1` are answered to anyone.

The pull request is opened by the operator named in `operator`, or that
operator approves it, so the statement is theirs.

## Leave

Open a pull request that removes `nodes/<node_id>.json` and adds
`nodes/left/<node_id>.json`:

```json
{"node_id": "<the node id>", "left": "<YYYY-MM-DD: the day the pull request merges>"}
```

The reporter reads `nodes/` at the start of every run, so once the pull request
merges, the next run does not ask the node. A run already under way finishes
with the list it started with.

To rejoin, add the entry back (with the statement) and remove the leave mark in
the same pull request.

## Past reports

Reports already published stay. Each one is a signed record of what was asked
and what came back on that day, under the keys named in it; removing a node
does not edit or delete them. The leave mark says from when the node is no
longer asked, so a reader of `reports/daily/` can tell a node that left from a
node that stopped answering.

## The check

`scripts/check_nodes.py` refuses an entry:

- whose `node_id` or `key_id` is not 64 lowercase hex, or whose file name is not `<node_id>.json`;
- that repeats another entry's `node_id` or `key_id`;
- that has a missing or unknown field;
- whose `consent` is not the operator statement, word for word;
- whose `operator` is not a GitHub handle, or looks like an email address or a phone number.

It also refuses a leave mark that is not `{"node_id", "left"}` with a real date,
or whose node is still listed.

Run it locally: `python3 scripts/check_nodes.py nodes`
