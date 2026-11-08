# loop

The evidence network's commitment list and its daily reports.

The network is a set of nodes on a mesh. Each node keeps a book of sealed records of the
exchanges it takes part in, and answers evidence requests about them under its disclosure
policy: a record is answered to the counterparty of that exchange, and to nobody else. Two
questions are safe to answer to anyone, and every listed node answers them:

- `checkpoints`: the checkpoints the node has cut over its book; they show no record;
- `history_card/1`: the node's history card.

An answer is either an artifact or a refusal the node signed over that request, so whoever
asked can check it under the key the node announces on the mesh.

## What the loop is for

Several products are built from the same parts (sealed records, books, evidence requests,
admission, settlement), and each part moves on its own release. The loop is how they keep
working together: it runs the same steps (seal, book, advertise, admit, deal and push,
settle, ask, verify, dispute, judge, report, join) against pinned versions of those parts, so
a change that breaks something shows up as a step that stops passing. On the live runs the bar
is that no step that passed stops passing, not that every step passes; on the rehearsal
population every step must pass.

One loop, two layers, picked with `loop.sh --ecosystem mesh|muse|cross|goose` (default `mesh`):

- **Per ecosystem**: the same steps 01 to 12, run inside one ecosystem with its own fixtures
  and nodes. Today that is `mesh`: mesh nodes running the capsules plugin. `muse` has no steps
  yet; each of its steps reads `NOT_CHECKED`. `goose` (ecosystem 2, loop#315) covers all twelve rows:
  its rehearsal runs two evidencebook Goose nodes (`steps/goose.setup.sh` installs them) and the steps with a
  Goose input run on it (today steps 01, 02, 03, 04, 05, 06, 07 and 12; 02, 04, 05, 07 and 12 also need capsulectl (steps/12-join.setup.sh), 03 and 12 the capsule-cli that verifies the stapled history_card/1 (steps/capsule-cli.setup.sh), and 02 the step 02 driver (steps/02-book.setup.sh)); every other row reads `NOT_CHECKED` naming the issue it waits on.
- **Across ecosystems**: steps where two ecosystems meet (one side admits and deals with the
  other, both sides seal their half, an outside verifier checks the pair), numbered 13 and up
  and run only under `cross`. None exist yet; `cross` reads `NOT_CHECKED` too.

The twelve steps, in the order `loop.sh` names them. Each asserts one fact and writes one finding. The Mesh and Goose columns say what the step checks in each ecosystem; whether a step passes today is on the status page ([status/README.md](status/README.md)), not here.

| # | Step | What it proves | Mesh | Goose |
|---|---|---|---|---|
| 01 | `seal` | A capsule sealed by one implementation verifies in a second language. | capsule-emit seals a record with the node's key; the AAC Go verifier passes Class 1 and recomputes the capsule id. | The same check on a capsule the Goose node sealed. |
| 02 | `book` | The record is in a book at a position, under a signed checkpoint that verifies. | evidencebook's verifier checks the record, its checkpoint and the witness receipt. | The same check on the Goose node's book. |
| 03 | `advertise` | Each node's commitment is published, fetched back and verified. | The commitment is published to a git carrier, read back, and checked: fields, no counts, signature, policy digest. | The same check on the Goose node's commitment, including its history card. |
| 04 | `admit` | The buyer's admission decision (GO or NO) is sealed before the deal, and the deal obeys it. | The capsules plugin's decision is read from the node's ledger; the loop checks the order, not the policy behind it. | The same check on the decision the Goose buyer seals. |
| 05 | `deal` | Both sides seal halves of one deal and push them; each holds and cites the other's. | Two nodes seal their halves, push them at completion, and each receiver seals a record citing the half it holds. | The same check on the two Goose nodes' halves. |
| 06 | `settle` | The exchange's settlement is on the rail, final and banded, and the public count equals the disclosed count. | A paid exchange's settlement is matched to the payment hash its payer sealed; the nodes' disclosed counts are checked against the counterparty's bundle. | The same check on the buyer's pay record: on rehearsal, the mock rail; live, one real Lightning payment between two lab wallets standing in for the Goose nodes, which hold no wallet. |
| 07 | `ask` | A later request is sealed before it is sent, then answered or recorded absent by its deadline. | A node asks another for an exchange and for `history_card/1`; the answer is a signed artifact or a signed refusal under the advertised policy. | The same check, with the two Goose nodes asking each other through their mailboxes. |
| 08 | `verify` | An outsider reproduces every finding of the run, offline, from its bundle alone. | Re-verifies every finding offline from the run's bundle; any finding it cannot reproduce reads `NOT_CHECKED` ([loop#193](https://github.com/evidence-network/loop/issues/193)). | Not wired for Goose yet: `NOT_CHECKED`, [loop#193](https://github.com/evidence-network/loop/issues/193). |
| 09 | `dispute` | The referee's signed Result on a pair of twin answers is held by each node it concerns, with the same bytes. On Mesh a dispute is an adjudication only, so that is all this step checks. | The referee seals the Result and delivers it; each node seals an `adjudication_received` record citing it. | Not wired for Goose yet: `NOT_CHECKED`, [loop#315](https://github.com/evidence-network/loop/issues/315). |
| 10 | `judge` | A deterministic pack runs over a window of the run's records, and its sealed Results cite every record in it. | The pack runs over the records step 08 reproduced; its Results verify offline. | Not wired for Goose yet: `NOT_CHECKED`, [loop#315](https://github.com/evidence-network/loop/issues/315). |
| 11 | `report` | The report page renders only from verified inputs, every number has a denominator, and the page's digest is sealed in a book. | The page is built from steps 08 and 10, with no score; the node seals its digest. | Not wired for Goose yet: `NOT_CHECKED`, [loop#315](https://github.com/evidence-network/loop/issues/315). |
| 12 | `join` | One command on the pinned release joins a node, and a stranger's probe of it verifies. | `join.sh` binds the key, creates the book and commitment, and probes the node with `capsulectl book doctor --probe`. | One command on the pinned Goose release (`loop_join`) opens the book and returns the signed commitment, then the stranger's `history_card/1` probe, as in the Mesh column. The node's key is not bound to a controller. |

The Goose column describes the rehearsal population (two Goose nodes on a mock rail). Steps 01, 03 and 07 also have a live Goose input; step 06 uses a real Lightning payment between lab wallets when the live run pays.

Two gates hold the bar. Each pull request runs `loop.sh --stage 1` (steps 01, 02, 03, 12, 07 and 08)
against the rehearsal population (simulated nodes on a mock rail, no real payments) and fails
if any step fails. The ecosystem gate keeps each step's last live result per ecosystem, with
the pins it ran under, in [`baseline/`](baseline/README.md): a pull request that changes a pin
must bring a new live run, and fails if a step that passed now fails, is missing or reads
`NOT_CHECKED`.

How the network grows is a separate report, not part of either gate:
[`/growth/`](https://evidence-network.github.io/loop/growth/), beside
[`/stats/`](https://evidence-network.github.io/loop/stats/). See [site/README.md](site/README.md).

## What is here

- [`nodes/`](nodes/README.md): the commitment list. One `<node_id>.json` per node, added by the
  operator's pull request. An entry says how to ask a node and which key it lists. The list
  is discovery, not evidence: the answers are the evidence.
- [`reports/`](reports/README.md): once a day the reporter asks every listed node the two
  questions above and commits, per node, what it asked, the bytes that came back and how they
  verified. A day with no run is marked, never silently missing. Nothing is scored, ranked or
  totalled across nodes.
- [`reporter/`](reporter/README.md): the reporter's public key and how it runs. It is a node
  like any other and asks nothing beyond the two questions.
- [`steps/`](steps/README.md) and `loop.sh`: the loop's steps and the script that runs them,
  one finding per step. [`baseline/`](baseline/README.md): the ecosystem gate's last accepted
  live results.
- `scripts/`: the checks CI runs, and the reporter's scripts. Each runs with plain `python3`.

## Running a node on the network

The network is in its lab stage: the nodes are run by known operators on a lab mesh. To take
part you run a node (a mesh host build with the capsules plugin) as a full node on that mesh,
then add your entry to `nodes/` by pull request. [`JOIN-LAB.md`](JOIN-LAB.md) covers creating
the book and publishing its commitment; for installing the mesh host and plugin, ask in an issue.

What joining publishes, in this repository and from then on in its history:

- your entry: the node's id, its plugin key, the host build and plugin version, and the GitHub
  handle you give as its operator (a handle only: never an email address or a phone number);
- each day, your node's answers to `checkpoints` and `history_card/1`, or the fact that it did
  not answer.

Nothing else is asked of your node, and no record is published: the reporter is not your
counterparty, so your node answers it only the two stranger-safe questions. To leave, remove
your entry by pull request: the asks stop from the next run, and reports already published
stay. See [nodes/README.md](nodes/README.md).

## Checking it yourself

```sh
python3 scripts/check_nodes.py nodes           # the list is well formed
python3 scripts/check_reports.py reports/daily # the reports are well formed, no day missing
python3 -m unittest discover -s tests          # the tests
```

Each report directory keeps the answer bytes as they came back, so a report can be checked
again under the node's key without trusting the reporter.

## Contributing

By pull request, with every commit signed off (DCO). See [CONTRIBUTING.md](CONTRIBUTING.md) for
the checks each pull request must pass.

## License

Apache-2.0. See [LICENSE](LICENSE).
