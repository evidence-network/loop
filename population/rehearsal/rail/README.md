# The mock rail

The rehearsal population's rail. It is a set of files: nothing moves, and
nothing it signs means anything outside the rehearsal. **Its key under
`keys/` is a TEST key, published on purpose, never used by a live node or a
real rail.**

Step 06 (Settle) reads it. Other steps may read it; only step 06 writes it.

| File | What it is |
|---|---|
| `rail.json` | the rail's declaration |
| `ledger.jsonl` | the rail's public ledger: every settlement object it issued, one per line, in order |
| `keys/rail-key.pem`, `keys/rail-key.pub.pem` | the rail's TEST key (Ed25519, PKCS#8 and SPKI PEM) |
| `mock_rail.py` | `issue()` makes a settlement object; `verify()` is the adapter a step calls on one |
| `make_rail.py` | writes `rail.json` and `ledger.jsonl`, and step 06's inputs in `../step06/` |

`make_rail.py` is deterministic for given keys (Ed25519 signatures are), and
`tests/test_step06_settle.py` checks that the committed files are exactly
what it writes. It makes the rail key with openssl only when none is there.

## rail.json

```
{"rail": "mock", "declaration": "mock-rail/v1", "test_key": true,
 "key_id": "<hex of the raw Ed25519 public key>", "public_key": "keys/rail-key.pub.pem",
 "ledger": "ledger.jsonl", "currency": "USDC", "decimals": 6, "fee_paying": true}
```

`key_id` must be the key `public_key` names. `fee_paying` says the rail
charges a fee on each settlement.

## A settlement object

One line of `ledger.jsonl`, in its JCS form (RFC 8785):

```
{"type": "mock-rail.settlement/v1", "rail": "mock", "sequence": 4,
 "settlement_id": "<64 hex>", "exchange_id": "rehearsal-ex-0004",
 "payer": "<payer key_id>", "payee": "<payee key_id>",
 "currency": "USDC", "amount_atomic": 4200, "decimals": 6, "fee_atomic": 10,
 "final": true, "issued_at": "2026-10-28T00:04:00Z",
 "sig": {"alg": "Ed25519", "canon": "jcs", "value": "<base64url>"}}
```

- `settlement_id` is the SHA-256 (hex) of the object's JCS form without
  `settlement_id` and `sig`. It is the object's own id: a deal refers to the
  settlement by it, `{"rail": "mock", "settlement_id": "..."}`, and never
  copies the object.
- `sig` is Ed25519 by the rail key over the JCS form without `sig`.
- `amount_atomic` and `fee_atomic` are integers in units of
  10^-`decimals` of `currency`. `final` is the rail's finality flag.
- `sequence` runs 1, 2, 3, ... down the ledger. No `settlement_id` repeats.
- `payer` and `payee` are node key ids (the hex of the raw Ed25519 public key).

## The adapter

`mock_rail.verify(line, declaration, spki)` returns one report per property,
`{property, status, method, detail}`, with the five statuses of
agent-action-capsule's interop-profile-v0 §7.1 (`PASS`, `FAIL`,
`NOT_PRESENT`, `NOT_CHECKED`, `INCONCLUSIVE`), the shape of evidencebook's
foreign-evidence adapters (evidencebook v0.8.1, `adapter/adapter.go`).
`mock_rail.py declare` prints what it checks.

| Property | PASS when |
|---|---|
| `object_form` | the line is an object with exactly the members above, integer amounts, boolean `final`, `rail` the declared rail, integers below 2**53 |
| `rail_signature` | `sig` verifies under the declared rail key |
| `settlement_id` | `settlement_id` is the digest above |
| `finality` | `final` is `true` |
| `rail_fee` | `fee_atomic` > 0 and the declaration says `fee_paying` |
| `band` | the value band derives: `<currency>:e<decade>`, decade = floor(log10(amount_atomic / 10^decimals)), from the integers, so 4200 at 6 decimals is `USDC:e-3` |

`mock_rail.py verify <rail dir>` runs it over the whole ledger.

## Step 06's inputs (`../step06/`)

- `exchange.json`: the exchange `{exchange_id, payer, payee, currency,
  amount_atomic, decimals, settlement_ref}`, a stand-in for step 05's deal.
- `disclosure-<node>.json`: the settlement count a node states for the rail,
  `{type: "settlement-disclosure/v1", node, key_id, rail, through_sequence,
  settlements, sig}`, signed with the node's TEST key like a settlement.
- `controllers.json`: `{stand_in: true, controllers: {<key_id>: <controller>}}`,
  rehearsal stand-ins for controller declarations. The filtered volume keeps
  a settlement only when its payer and payee are declared under different
  controllers.

The ledger holds four settlements. The fourth is the exchange step 06
asserts on. The third is a payment from alice to itself, so the filtered
volume is 3 of 4.
