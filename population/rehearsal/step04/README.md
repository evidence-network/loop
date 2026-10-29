# Step 04's rehearsal inputs

Each scenario under `scenarios/<name>/` is one offer that `bob` (the buyer)
receives from `alice` (the seller):

- `offer.json`: the offer's deal id, the time it was made, and two numbers,
  `price_per_inference` and `deal_amount`, each written as
  `{"value": "<decimal>", "unit": "USDC" | "sats"}`. It stands in for step 05's
  deal terms.
- `terms.json` (optional): an operator's own Terms Pack, as `--terms` would
  pass it. A scenario without this file is decided under the pack the step was
  given, which by default is `steps/04-admit.d/terms-pack.json`.

The scenarios:

| Scenario | Offer | Outcome |
|---|---|---|
| `go` | under every bound | GO |
| `no-price` | 0.05 USDC per inference, over the 0.01 bound | NO |
| `no-amount` | a deal of 50000 sats, over the 10000 bound | NO |
| `go-adjusted` | the same numbers as `no-price`, under an adjusted pack whose price bound is 0.1 USDC | GO |
