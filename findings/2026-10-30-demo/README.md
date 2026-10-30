# 2026-10-30 demo: step 06 off-pin (loop#56)

Step 06 run over the demo mesh nodes Brattain (payer) and Shockley (provider) in place of new lab wallets. This was accepted by Ethan and the coordinator as the step 06 evidence for loop#56, and it is **labelled off-pin**:

- Nodes run mesh-llm 2dc55de with capsules plugin 0.1.5, not the versions.lock pin (capsules d1f4edc). `finding.json`'s `pinned` block records the loop's pins, not the nodes' build.
- The population was hand-built: two nodes, `"rail": null`, Brattain's live data dir and a copy of Shockley's `ledger/capsules.jsonl` taken at 2026-10-30T07:33Z.
- The step ran from loop main c6d7d64 (`steps/06-settle --population <hand-built> --out .../06-settle`).

The exchange: one paid request from Brattain to Shockley at 2026-10-30T07:32Z (Q4 model, max_tokens 32, Shockley priced at 50,000,000 msat per million). Brattain's wallet reports it `completed`, spent 1,950 msat, paid from its existing balance with no new funding. Brattain recorded the terms, payer_observed and delivered legs, and Shockley recorded payee_observed. Both carry payment_hash 0b9cf98a7de48966df0d16fa6a0483db363b7ecacca9c7bfea3239993d755960.

Gap: Brattain's spendable balance fell by 1,941 msat (3,401,780 to 3,399,839), not 1,950. The 9 msat difference is not reconciled.

Result: NOT_CHECKED on Mesh-LLM/capsules-plugin#83. Live 06 has no rail's own settlement object, so finality is not verified. No wallet state, key or seed is included.
