# Step 13 Ask across: an evidence request answered across ecosystems

Step 13 is the first step where two ecosystems meet at the evidence request. It is not a deal: a party in one ecosystem asks
a listed node of another for evidence about one interaction, through the carrier that node names, and the answer is
checked offline under the key the node is listed with. `goose` to `mesh` and `mesh` to `goose` come first; outside
responders follow. Each ecosystem runs only the steps that fit it, and conforming means shareable evidence
(Steven's ruling of 2026-11-12, [loop#440](https://github.com/evidence-network/loop/issues/440)).

Run it as its own stage: `./loop.sh --stage cross --ecosystem cross [--population rehearsal|live] [--strict]`. The stage
is steps `13` and `08`, because step 08 re-derives 13 from the same output.

## What the step asserts

Each (asker ecosystem, responder ecosystem) cell is a row of `pairs.json`. A row is `PASS` when, for each ask in it:

- the outcome bundle verifies, and both records (the request and the outcome) are signed by the asker's key;
- the request was sealed in the asker's book **before** it was sent, and the bytes sent are the bytes sealed;
- the answer is a signed artifact or a signed refusal, recorded under the responder's **listed** key and its checkpoint
  key, in time, and the reply bytes verify again in a fresh verifier book (the pinned `capsulectl-book`);
- an answer that arrives after the deadline counts as an absence, never as an answer, and an absence is recorded only when
  the window has closed;
- a refusal is not for a subject the responder's advertised policy answers to anyone.

The judge is step 07's (`judge_pair`, run with `mailbox=True`, the variant it uses for a Goose node). Step 13 does not copy
it. A row is `FAIL` if any of that does not hold, `NOT_CHECKED` if it could not be checked, and the finding is the worst row:
one `NOT_CHECKED` or `FAIL` cell never sits under a `PASS`.

**The request's deadline is required by step 13. The draft does not require it.** draft-mih-agent-evidence-request-02 §3.4 makes
the request's `deadline` optional. Step 13 requires it: a request carried over a mailbox has no other window, and the Nostr
mailbox binding measures an absence against it, so a request without its own `deadline` is a `FAIL` here. The deadline must
be the responder's `answers_within`, counted from the moment the request was sealed. This is the loop's requirement; it is
not a change to the draft or to the request format.

**A signed refusal is a correct answer.** A node that advertises `disclosure.policy: counterparties` refuses an exchange
request from an asker that is not its counterparty, with a refusal signed under its listed key. That scores as an answer,
not a failure; an unsigned refusal, a refusal under another key, or a refusal of a subject the policy answers to anyone is a
`FAIL`.

## What an answer carries

The consent statement says the network never asks a node for "the content of any request, answer or payment". An answer in
step 13 carries digests and signatures, never bodies:

| Part | What it carries | What it does not |
|---|---|---|
| The request (the asker's own evidence request) | the subject (a record id the asker took part in, or the bare `checkpoints` subject), the coverage, the `deadline`; its sha256 is recorded in the responder's book | no request or answer body; no payment |
| An artifact answer | the evidence envelope: the request digest, the subject, the anchor (the responder's checkpoint, signed under its listed checkpoint key) and the artifact **by its digest**, signed by the responder | no body, and **no payment amount**: a payment is a separate record, not an answer to an evidence request |
| A refusal | the request digest, a reason from the profile's list, `issued_at`, signed by the responder | no reason text beyond the registered reason |
| The asker's outcome record | the header of the request and of the outcome, disclosed as an Evidence Bundle (`outcome.bundle.json`), and the digest of the reply bytes it keeps | not the content the reply's artifact digest names |

`pairs.json` itself holds the result of each cell, its `not_checked` entries and the digest of each proof file. It holds no
time, so step 08 can write it again and get the same bytes.

## Who is asked

A listed node is asked only if its `nodes/` entry carries the consent statement that covers step 13
(`check_nodes.CONSENT_ASK_ACROSS`, revision 2 in [nodes/README.md](../nodes/README.md)). An entry on the first statement is
skipped and reported in its cell as "not opted in": a `NOT_CHECKED` cell naming why, never a `FAIL`. Each entry moves to the
new statement by a pull request its own operator opens or approves.

The carrier comes from the entry. The entry's `ask_route` (`mesh` or `nostr`) must agree with the signed commitment's `ask`
member (`ask.nostr` exists exactly for `nostr`), and a pair whose two disagree is a `FAIL`. The address is read from the
commitment: a mesh node's address is its `node_id`; a Goose node's is its `ask.nostr` mailbox key and relays. No carrier is
hard-coded, and an outside carrier would be a new `ask_route` value, which is another item.

A node's ecosystem is the `community` its history card carries. The names the issue uses and the card's community meet in one
map, `ECOSYSTEM_OF_COMMUNITY` in `steps/13-ask-across`: `mesh-llm` is `mesh`, `muse` is `muse`, `goose` is `goose`, and a
`/0` card reads as `mesh-llm`. A community the map does not list is refused, never guessed.

## Rehearsal and live

**Rehearsal.** `scripts/cross_rehearsal.py` runs `scripts/cross_ask_session.py` with rehearsal keys made for the run (no
live key, wallet or lab VM): a mesh node (a `capsulectl book` node joined by `join.sh`) and a Goose node (evidencebook's G1),
asking each other three questions each way through an in-memory relay: an exchange request (refused with a signed refusal
under the counterparties policy), `history_card/1` (answered with a signed artifact) and the same exchange request whose
answer is posted only after the deadline (an absence). The run waits out the one-minute deadline, as step 07 does.

The rehearsal mesh node's mailbox is the loop's adapter, not the mesh plugin, and its listing is a rehearsal-only entry
with `ask_route: nostr` made together with a commitment re-signed to carry `ask.nostr`. So a `PASS` says: request and answer
bytes verify across ecosystems under the listed keys. It does not say a live mesh node answers on a Nostr mailbox.

**Live.** No cross request is exchanged, so each live cell stays `NOT_CHECKED`, naming the gap that owns it:

- goose asking mesh: [loop#442](https://github.com/evidence-network/loop/issues/442). The listed mesh commitments carry no
  `ask.nostr`, and the mesh plugin does not read a Nostr mailbox.
- mesh asking goose: [loop#443](https://github.com/evidence-network/loop/issues/443). The mesh plugin sends asks over the mesh
  only, and the lab Goose relay is reachable only from the lab host.

Each listed entry on the first consent statement is also named in its cell, as not opted in.

## Needs

`capsulectl` and `capsulectl-book` (`steps/07-ask.setup.sh`) and the Goose tools (`steps/goose.setup.sh`); CI installs both with the
other steps' pins. Without them the
step reads `NOT_CHECKED` naming the tool; when they are installed and the run fails it is a `FAIL` with the run's log.
