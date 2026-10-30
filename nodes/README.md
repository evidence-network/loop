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

  > I add this node to the evidence network to help improve the evidence layer. The network may ask it for its history summary, usually daily, and never for the content of any request, answer or payment. A summary of its answers, or that it did not answer, is published at https://evidence-network.github.io/loop/stats/. To leave, remove this entry: the asks stop on the next run, and past reports stay.

  The value goes in the entry exactly as this JSON string:

  ```
  "I add this node to the evidence network to help improve the evidence layer. The network may ask it for its history summary, usually daily, and never for the content of any request, answer or payment. A summary of its answers, or that it did not answer, is published at https://evidence-network.github.io/loop/stats/. To leave, remove this entry: the asks stop on the next run, and past reports stay."
  ```

- `ask_route: mesh` means: ask over the lab mesh, addressed by `node_id`.
- `disclosure.policy: counterparties` means records are answered only to the counterparty of that exchange; `checkpoints` and `history_card/1` are answered to anyone.

The pull request is opened by the operator named in `operator`, or that
operator approves it, so the statement is theirs.

## Commitment

Beside its entry, a node may publish a signed commitment in
`nodes/commitments/<node_id>.json`: how it can be asked, what it answers, and
a stapled history card for each book it keeps. `join.sh` writes one
(`evidence-commitment/1`: one card per book, in `history_cards`, each naming
its book's log id as `stream`); commitments in the earlier
`evidence-commitment/0` form, with one `history_card`, still verify and read
as one card. The format is in `steps/03-advertise.md`. A card that carries no
witness receipt is unwitnessed; today's lab books are.

Each card names its **community**: `mesh-llm` or `muse`. `join.sh` writes
`mesh-llm`, and a `/0` card reads as `mesh-llm`.

The live lab nodes' commitments are the ones their own plugin signs and serves (`commitment` tool,
capsules-plugin#75). `lab/lab.sh commitments` writes the commitment as returned, byte for byte, with its policy
at `nodes/commitments/policies/<node_id>.json`; the harness never signs for a live node. A commitment carries
no controller key or npub, only `controller_binding.digest` when a binding exists.

A card's witnesses are read from the witness receipts inside it, each naming
its witness in a `witness` member (`RECEIPT_WITNESS` in
`scripts/history_cards.py`). That name is **provisional** until a receipt
producer exists; no card carries a receipt yet.

**A cleared history is a new stream.** A node that clears a book starts a new
stream, with a new log id, and staples the new stream's card. It reads like a
stream that has just started: no history yet, and the date of its first
checkpoint. Nothing marks it as cleared. Rewriting or truncating the old log
is not a clear, and still fails the consistency check. Retention commitments
the node made on the old stream still bind until their `until`.

`scripts/check_commitment.py nodes/commitments` checks them (the
`commitments` check), and `scripts/history_cards.py` lists the cards of one.

## Controller bindings

A controller binding says that several subjects (nodes, or a node and a
personal agent) are under one controller. Only a verifier can say a binding
holds. Steps 05 and 12 check a binding record with aac-go's
`cross_language binding` (Controller Binding v0, go/v0.16.0), but that
writes no verdict and no `binding-verifier` is pinned, so this repository
groups no subjects by it. A verifier's verdict would be published as
`bindings/<controller_key_id>.json` (`binding-verdict/0`; see
`bindings/README.md`). Nothing produces one today.

Subjects are shown as one only when their verdict is `verified` by the
binding verifier that `versions.lock` pins, and each subject's commitment
carries the verdict's binding digest as `controller_binding.digest`. A `revoked` or `not_checked` verdict never joins
them. `scripts/check_binding.py bindings` checks the verdicts.

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
