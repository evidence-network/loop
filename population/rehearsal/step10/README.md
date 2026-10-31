# Step 10's rehearsal inputs

- `keys/judge.pem`, `keys/judge.pub.pem`: the rehearsal judge's TEST key (Ed25519, PKCS#8 and SPKI). Step 10
  seals its Results in a scratch book under it, and judges each Result's bundle against `judge.pub.pem`.
- `keys/checkpoint.pem`, `keys/checkpoint.pub.pem`: the TEST key that signs that book's checkpoints.
- `run/`: the committed stand-in run the tests copy as their base run (step 10 never judges it by itself). It is a real
  rehearsal run of steps 05 Deal, 06 Settle, 07 Ask and 09 Dispute, made by `population/make_step10_standin.py`:
  their findings are dated 2026-09-14, and the real step 08 reproduces them (`08-verify/verified.json` is its
  manifest, finished 2026-10-02), so every record is up to the as-of instant.
  `tests/test_step10_judge.py` runs 08 again on a copy and requires the same manifest. These are rehearsal
  records, never a node's.

The keys are published on purpose and never used live. The live judge key is the lab's own: only its public
half is committed, at `population/live/step10/keys/judge.pub.pem` (see `steps/10-judge.md`, Sealing).
