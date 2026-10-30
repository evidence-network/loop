# Step 11's rehearsal inputs

- `keys/reporter.pem`, `keys/reporter.pub.pem`: the rehearsal reporter's TEST key (Ed25519, PKCS#8 and SPKI). Step 11
  seals its report Result and the page's digest in a scratch book under it.
- `keys/checkpoint.pem`, `keys/checkpoint.pub.pem`: the TEST key that signs that book's checkpoints.

The keys are published on purpose and never used live. On the live population the page's digest is sealed in a lab
node's own join book, under that node's key.
