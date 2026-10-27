# reports/

Written by the daily reporter ([reporter/](../reporter/README.md)).
`reports/daily/<YYYY-MM-DD>/` holds one of:

- `RUN.json` and `README.md`: a run, with one `<node_id>/` per node in `nodes/`;
- `MISSED`: no run that day, and why. A day is never silently absent: the next
  run marks every day it finds without a report, and the `reports` check
  refuses a gap with no mark.

A node's directory holds, for each question (`checkpoints`, `history_card` for
`history_card/1`):

- `<q>.request.json`: what the reporter asked;
- `<q>.response.json`: the bytes its node's plugin returned, the node's answer
  (artifact or signed refusal) beside the plugin's verification; or
  `<q>.absence.txt` when nothing came back, and why;
- `<q>.verification`: one line, starting with
  - `verified`: an artifact that verified under the node's announced key;
  - `no_checkpoint_yet`: a signed `coverage_unsatisfiable`, the node has cut no
    checkpoint yet;
  - `refusal`: another refusal the node signed over this request, with its reason;
  - `no_announced_key`: no key announced for the node, so nothing could be checked;
  - `not_evidence`: a reply that is neither a verified artifact nor a signed refusal;
  - `absence`: no reply.

and `peer-key.json` (the key the answers were judged under) and `NODE`:
`discloses: ...`, `joined, no checkpoint yet`, or `joined, discloses nothing`.

Each node's files say what that node answered. Nothing is scored, ranked or
totalled across nodes.
