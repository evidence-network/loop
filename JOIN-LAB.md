# Joining the evidence network (lab)

This is the lab edition of the join guide. It changes while the lab runs;
`JOIN.md` replaces it when the network opens to everyone.

## What a node does

**The request.** A node keeps a book: an append-only log of signed records
about the work it did, with signed checkpoints over it. Anyone may send the
node an evidence request, a small signed question about that book. The node
answers with a signed artifact, or with a signed refusal that says why. If
nothing comes back in time, the asker records the absence. Those are the
only three outcomes, and the asker can check each of them later, offline.
Two questions are open to everyone, strangers included: `checkpoints` (what
the book has committed to) and `history_card/1` (a summary derived from it).
Nothing in either answer reveals a single record.

**The halves.** Every exchange has two sides, and each side keeps its own
record of it in its own book: its half. Neither half is the truth on its own.
When the two halves agree, the exchange is settled. When they disagree, that
disagreement is itself the recorded state, and nobody guesses which side is
right.

**The tiers.** How much evidence a decision needs depends on what is at
stake. A small exchange can go ahead on what a node publishes about itself;
a larger one asks for more, such as a fresh, signed answer to the asker's own
request, with proof that the records it relies on are in the book. A node
that publishes nothing can still take part, at the smallest tier.

**Judgment.** The asker decides, under its own written policy, and reads each
source for what it is: the node's own statements as the node's, a third
party's as that party's. Nothing is pooled into one number. There is no
score, no ranking and no leaderboard. A refusal to answer limits what a node
is trusted with; it does not ban it.

What a node publishes about itself is its **commitment**: its keys, how to
ask it, which questions it answers, and the digest of the sharing policy it
answers under. A node's key is bound to whoever runs it, its **controller**,
by a record both keys sign. The binding says that one controller holds both
keys; it never says who that is.

## Joining, with join.sh

`join.sh` is the one command. On the pinned release it:

1. binds your node key to your controller key (a `controller-binding/0`
   record, signed by both, and the controller class);
2. creates your book and cuts its first checkpoint;
3. writes your commitment, with your sharing policy's digest in it;
4. asks your node `checkpoints`, then `history_card/1`, the way a stranger
   would, and keeps the requests and answers for anyone to check.

It writes `join.json`, which lists what was done and what is still only your
own word: what is self-attested, unwitnessed, unchecked, unbound or unshared.

### What you need

- Linux on amd64, `openssl` 3, `python3`, `curl`.
- Two Ed25519 private keys in PKCS#8 PEM: your node key and your controller
  key. They must differ. To make them:

  ```sh
  openssl genpkey -algorithm ed25519 -out node-key.pem
  openssl genpkey -algorithm ed25519 -out controller-key.pem
  chmod 600 node-key.pem controller-key.pem
  ```

  Keep the controller key off the node once the binding is signed.

### Steps

```sh
git clone https://github.com/evidence-network/loop && cd loop

# 1. Install capsulectl and capsulectl-book at the versions.lock pins,
#    each checked against its release's SHA256SUMS.
steps/12-join.setup.sh

# 2. Join.
./join.sh --name my-node --node-key node-key.pem \
  --controller-key controller-key.pem --out join-out

# 3. Read what it did, and what is still unchecked.
cat join-out/join.json

# 4. Publish: open a pull request adding your commitment to nodes/.
```

Your book and its profile live under `~/.local/share/evidence-network/my-node`
(`--home` moves them). join.sh never touches your own capsulectl
configuration, and it never pushes anything.

Options: `--controller-class` (default `operator_key`: a plain Ed25519 key you
hold), `--policy FILE` (a sharing policy; default capsulectl-book's documented
defaults: history to counterparties, adjudications to their subjects),
`--answers-within` (how soon you answer; default `PT1H`).

### Checking a join

The loop's step 12 checks what join.sh wrote:

```sh
steps/12-join --population population/rehearsal --out findings/12-join
```

It verifies the stranger's answers under the keys your commitment advertises,
with the pinned capsulectl-book, and checks that they match the advertised
policy. Its `finding.json` says `PASS`, `FAIL` or `NOT_CHECKED`, and for each
part not checked, what, why and the issue that owns it.

## What the lab build does not do yet

These read `NOT_CHECKED` until they ship. They are never shown as checked.

- `capsulectl book doctor --probe` is merged but not in a released
  capsulectl-book (action-state-group/evidencebook#133, #134). join.sh uses
  it when the installed build has it; until then it asks the same two
  questions on the public path without it.
- No released tool verifies a `controller-binding/0` record yet. join.sh
  signs it; nothing checks it, so your key reads as unbound.
- The commitment's final format and checker come with step 03; join.sh
  writes a provisional one.
- Mesh nodes keep their book in the capsules plugin. For them, join, doctor
  and the probe come with Mesh-LLM/capsules-plugin#53, and the signed
  commitment with Mesh-LLM/capsules-plugin#51. Until then the loop probes a
  Mesh node through the daily reporter's ask.
- No witness is registered, so checkpoints are unwitnessed.
