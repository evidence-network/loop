# 2026-10-30 demo-2: step 06 at the demo pins

Step 06 over the demo mesh nodes Brattain (payer) and Shockley (provider), at the pins `population/demo` names (`demo-mesh-llm` ethanyzhang/mesh-llm 3ffa5c4f, `demo-capsules` Mesh-LLM/capsules-plugin f494845). It replaces nothing: `findings/2026-10-30-demo/` stays the off-pin run for loop#56.

- Both nodes run `/home/intangible/ml-lab1030/mesh-llm` (sha256 8f7cbd57…) with the capsules plugin installed from the archive with sha256 d4b075a1… (version 0.1.5), measured on each VM at snapshot time. `build.json` records both.
- Snapshot, read-only: each node's `ledger/capsules.jsonl` and `keys/node-key.pub.pem`, copied from the node's `CAPSULES_DATA_DIR` (read from the running process) at 22:48Z, into `$DEMO_DIR/<node>/capsules`. No wallet state, private key or deal secret was copied.
- Run from loop main b7069fa4: `DEMO_DIR=<snapshot> steps/06-settle --population population/demo --out findings/2026-10-30-demo-2/06-settle`. `steps/finding.py check` exits 0.

Result: NOT_CHECKED on Mesh-LLM/capsules-plugin#83. In the window from 17:58Z each node recorded 15 settlement legs and 8 wallet-reported observations; all 8 payment hashes pair across the nodes by role and amount, 0 unpaired. Agreement between the nodes' records is not finality. The step paid nothing.
