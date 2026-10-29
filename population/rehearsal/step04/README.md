# Step 04's rehearsal inputs

Step 04 (Admit) runs before steps whose outputs it reads, so each input from
another step is a stand-in here, signed with TEST keys (Ed25519 over the
body's canonical JSON; `steps/04-admit.d/standin.py`). `make_standins.py`
writes them; a rerun reproduces the same bytes from the same keys.

- `scenarios/<name>/deal.json`: the terms of one deal `bob` (the buyer) is
  offered by `alice` (the seller): its id, both keys, its value band
  `(currency, decade)` and when it is offered. A stand-in of step 05's deal.
- `scenarios/<name>/history_card.json`: the seller's `history_card/1`, signed
  by the seller's key, with its checkpoint time and its past deals, each
  carrying the counterparty's signed half. A stand-in of the card step 07
  asks for; the plugin's card carries no halves yet. Absent when the seller
  shows none.
- `scenarios/<name>/adjudications.json`: referee Results naming the seller
  (`adjudication_issued`, outcome `for_seller` or `against_seller`, citing the
  deal's exchange by digest). A stand-in of step 09's shape.
- `controllers.json`: controller declarations, each signed by its
  controller's key and binding node keys. `alice` and `carol` are under one
  controller, so deals between them are not claims held. A stand-in until the
  capsules plugin's declaration format is read here.
- `referees.json`: the referee keys the buyer trusts. A Result signed by any
  other key is reported and not counted.
- `keys/`: TEST keys for carol, dave, erin, the referee and two controllers,
  published on purpose and never used by a live node.

The scenarios: `t0-no-card` (GO: a card is optional at T0), `go`, `ask` (a
stale card: one derived request), `no-half` (LIMIT), `self-dealt` (LIMIT: the
only halves are from a key under the seller's controller),
`adverse-with-buyer` (NO) and `adverse-other` (LIMIT).
