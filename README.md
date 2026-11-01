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
  yet; each of its steps reads `NOT_CHECKED`. `goose` (ecosystem 2, loop#315) is registered the
  same way; its steps arrive with its driver.
- **Across ecosystems**: steps where two ecosystems meet (one side admits and deals with the
  other, both sides seal their half, an outside verifier checks the pair), numbered 13 and up
  and run only under `cross`. None exist yet; `cross` reads `NOT_CHECKED` too.

Two gates hold the bar. Each pull request runs `loop.sh --stage 1` (steps 01, 02, 03, 12 and 08)
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
