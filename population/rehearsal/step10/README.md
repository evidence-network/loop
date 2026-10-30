# Step 10's rehearsal inputs

- `keys/judge.pem`, `keys/judge.pub.pem`: the rehearsal judge's TEST key (Ed25519, PKCS#8 and SPKI). Step 10
  seals its Results in a scratch book under it, and judges each Result's bundle against `judge.pub.pem`.
- `keys/checkpoint.pem`, `keys/checkpoint.pub.pem`: the TEST key that signs that book's checkpoints.
- `run/`: the committed stand-in run step 10 judges when it is given a run without 08's manifest. It holds
  four stand-in findings (05 Deal, 06 Settle, 07 Ask, 09 Dispute) dated 2026-09-14, each with a few small
  records, and `08-verify/` with a manifest that shows them reproduced, finished 2026-10-02. The window is
  2025-10-01 to 2026-10-01, so all 13 records are in it. These are stand-ins, never a node's records.

The keys are published on purpose and never used live. The live population lists no judge key, so step 10 is
NOT_CHECKED there.
