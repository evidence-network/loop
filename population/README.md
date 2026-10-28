# population/: who the loop runs against

`loop.sh --population rehearsal|live` hands each step `population/<name>/`,
and the step reads `population.json` there. The two populations never share
a findings directory or a count.

## rehearsal/

Demo nodes for CI and pull requests: Alice and Bob, on the mock rail
(`"rail": "mock"`; nothing settles).

**The keys under `rehearsal/keys/` are TEST keys. They are public on purpose,
so anyone can rerun the loop and check its findings. No live node ever uses
them, and nothing they sign means anything outside the rehearsal.**

Each `keys/<name>/` holds `node-key.pem` (Ed25519, PKCS#8) and
`node-key.pub.pem` (SPKI). `make_rehearsal.py` made them with openssl;
`make_rehearsal.py --force` makes a new pair.

`witness` in `population.json` is the rehearsal witness, a TEST witness:
its key is `keys/witness/witness-key.pem` (public on purpose, like the node
keys), its `url` names it in a receipt, and `witnesses.json` is the witness
directory a verifier trusts it under (its raw Ed25519 key in hex). Step 02
gives it the book's checkpoint and verifies the receipt it returns with
evidencebook's verifier. Its receipts mean nothing outside the rehearsal.

## live/

Our three lab nodes, named without secrets and without hostnames. Node ids
are `null` until the nodes have them, and `rail` is `null` until a stage
settles. No key is kept here.

Each node is:

```
{"name": "stevenmih-1", "node_id": "<64 hex>", "host": "lab",
 "data_dir": "$LAB_DIR/nodes/lab-1/.local/share/capsules",
 "book_dir": "$LAB_DIR/nodes/lab-1/join-book"}
```

- `name`: the node's label, chosen by its operator (the lab run's are `stevenmih-1`, `-2`, `-3`;
  their directories on the lab host keep `lab-1`, `lab-2`, `lab-3`).
- `node_id`: the node id its capsules plugin learned from its host.
- `host`: always `"lab"`, the one lab host. Never a hostname.
- `data_dir`: the node's capsules plugin data dir on the lab host. Step 01 seals
  with `keys/node-key.pem` there and checks against `keys/node-key.pub.pem`.
- `book_dir`: the `--home` join.sh made the node's book in. Step 02 appends to that
  book (`book/`, with its `checkpoint.seed`), signing with the key under `data_dir`.

`lab/lab.sh entries` (through `lab/pack.py`) fills `node_id`, `data_dir` and
`book_dir` at bring-up. The paths start with `$LAB_DIR`, which the steps expand
from the environment, so no host path goes into the repository. The committed
values stay `null` until the lab run's tarball comes back. A step that needs a
path which is `null`, or not readable by the user running the loop, writes
NOT_CHECKED for that node (`live node <name>`, issue `evidence-network/loop:
the live lab run, which fills population/live`); it never crashes and never
reads that as a FAIL.
