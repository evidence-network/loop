# reports/

Written by the daily reporter ([reporter/](../reporter/README.md)).
`reports/daily/<YYYY-MM-DD>/` holds one of:

- `RUN.json` and `README.md`: a run, with one `<node_id>/` per node in `nodes/`;
- `MISSED`: no run that day, and why. A day is never silently absent: the next
  run marks every day it finds without a report, and the `reports` check
  refuses a gap with no mark.

A node's directory holds `attempts/`: one `<YYYYMMDDTHHMMSSZ>/` per time the
reporter asked it, written once and never changed. A question that got no
answer is asked again at the times in `reporter/reporter.json`
`retry_after_s` (now +1 h, +3 h, +6 h after the run starts); an answer, a
signed refusal included, is never asked again. An attempt holds `ATTEMPT`
(`at=<time> by=run pass=<n>`, or `by=ask` for an ask-again), `peer-key.json`,
and the files below for each question it asked. Its verification line can also
say why no answer came:

- `unreachable`: the reporter's node could not open a stream to the node
  (the plugin's `could not reach peer`);
- `timeout`: the request went out and nothing came back in time (the plugin's
  `peer does not answer evidence requests`, or no reply from the reporter's own
  node within its timeout);
- `absence`: no reply, for another reason.

Beside `attempts/`, the day as the run settled it after its last attempt, for
each question (`checkpoints`, `history_card` for `history_card/1`):

- `<q>.request.json`: what the reporter asked;
- `<q>.response.json`: the bytes its node's plugin returned, the node's answer
  (artifact or signed refusal) beside the plugin's verification, copied from
  the attempt that got it; or `<q>.absence.txt` when nothing came back, with
  each attempt's time and why;
- `<q>.verification`: one line, starting with
  - `verified`: an artifact that verified under the node's announced key;
  - `no_checkpoint_yet`: a signed `coverage_unsatisfiable`, the node has cut no
    checkpoint yet;
  - `refusal`: another refusal the node signed over this request, with its reason;
  - `no_announced_key`: no key announced for the node, so nothing could be checked;
  - `not_evidence`: a reply that is neither a verified artifact nor a signed
    refusal (including one the plugin could not read);
  - `absence`: no answer after the last attempt, with every attempt time
    (`attempts=...`).

and `peer-key.json` (the key the answers were judged under, as of the last
attempt) and `NODE`: `discloses: ...`, `joined, no checkpoint yet`, or
`joined, discloses nothing`. The day's `README.md` says of a node that answered
nothing `absent today` or `absent 3 days running`: a fact about that node.

An ask-again after the run (`scripts/daily_report.py ask`) adds one attempt
directory and nothing else; the files the run settled stay as they were.
`scripts/report_layout.py` reads a node's day from its attempts, the later
ones included.

Each node's files say what that node answered. Nothing is scored, ranked or
totalled across nodes.
