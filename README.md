# loop

The evidence network's commitment list and daily reports. Lab stage: known operators only, private until the public join.

A node on the network keeps a book of sealed records, answers evidence requests under its disclosure policy, and can be asked by anyone on the mesh. This repository holds:

- `nodes/`: the commitment list. One `<node_id>.json` per node, added by pull request. See [nodes/README.md](nodes/README.md).
- `reports/`: what the daily reporter asked each listed node, the answer bytes, and how each answer verified.
- `reporter/`: the daily reporter's identity (its public key) and how it runs. See [reporter/README.md](reporter/README.md).
- `JOIN-LAB.md`: how a lab operator installs the host build and the capsules plugin and opens the pull request (to follow, written from a fresh-machine run).

The list is discovery, not evidence. An entry says how to ask a node; the answers are the evidence.
