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

## live/

Our three lab nodes, named without secrets and without hostnames. Node ids
are `null` until the nodes have them, and `rail` is `null` until a stage
settles. No key is kept here.
