# Step 10's rehearsal inputs

- `keys/judge.pem`, `keys/judge.pub.pem`: the rehearsal judge's TEST key (Ed25519, PKCS#8 and SPKI). Step 10
  seals its Results in a scratch book under it, and judges each Result's bundle against `judge.pub.pem`.
- `keys/checkpoint.pem`, `keys/checkpoint.pub.pem`: the TEST key that signs that book's checkpoints.

Both are published on purpose and never used live. The live population lists no judge key, so step 10 is
NOT_CHECKED there.
