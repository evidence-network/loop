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
settles. No private key is kept here. The one public key is the live judge's,
`step10/keys/judge.pub.pem`, which `lab/lab.sh judge-key` writes; its private
half stays on the lab host (`steps/10-judge.md`, Sealing).

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

## demo/

The two demo nodes Brattain and Shockley (the Devcon demo mesh), for a step that reads what live nodes
recorded. `"population": "demo"` with `"kind": "live"`: step 06 takes its live path for it (read-only, never
pays). It is not run by `loop.sh`; the owner of the nodes copies each node's capsules data dir (read-only) into
a run directory, exports it as `$DEMO_DIR` (`$DEMO_DIR/brattain/capsules`, `$DEMO_DIR/shockley/capsules`), and
runs `steps/06-settle --population population/demo --out findings/<date>-demo/06-settle`.

- `pinned`: the nodes run a dev build, so the finding pins `demo-mesh-llm` and `demo-capsules` from
  `versions.lock` (which says the build includes unmerged mesh-llm PRs), not the lab's pins.
- `window.from`: only records whose signed capsule timestamp is at or after it count. For the 2026-10-30
  run it is the M4 roll (17:58Z); older records come from older plugin builds and must not be attributed
  to the pinned one.
- `crosscheck: "payment_hash"`: the step pairs the payment hashes the two nodes recorded by role and amount
  (`crosscheck.json`): one node's payer observation and the other's provider observation, the payer's
  `amount_msat` equal to the provider's `credited_msat + fee_msat`. Unpaired hashes are listed with a reason.
  Agreement is not finality; the result stays NOT_CHECKED on Mesh-LLM/capsules-plugin#83.
- `node_id`: the peer id from the node's `CAPSULES_PEER_KEYS` (Sherlock2, Buzz thread on loop#183).
- The run's `build.json` records the binary sha256 and the plugin archive sha256.
