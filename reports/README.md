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

## Size and retention

### What a day costs

Measured with `scripts/repo_size.py measure`: the reporter's own `run`, against
a fake reporter-node console that answers with real capsules answers (built by
capsules main 424f2c7's responder from a node key and a growing checkpoint
chain; a probe test, not a live node), one node's bytes made distinct from
another's. Each `*.response.json` holds a node's whole checkpoint list:

- about 2.4 KB per checkpoint for `checkpoints` and 2.7 KB for `history_card/1`;
  a node cutting 12 checkpoints a day adds about 61 KB a day to its two answers;
- at most 1 MiB per answer (capsules refuses a larger one): past about 380
  checkpoints (`checkpoints`) and 350 (`history_card/1`) the node signs a
  `policy_declined` refusal of about 360 bytes instead, every day after;
- the other files of a node's day (request, verification line, `NODE`,
  `peer-key.json`) are about 2 KB.

One day, 10 nodes: 0.45 MiB at 12 checkpoints each; 18.6 MiB when every answer
is near the 1 MiB cap (2.7 MiB of it in git once packed).

### A year

`tree` is what a checkout holds under `reports/`. `git` is what the history
grows by, packed (`git repack -adf`). `git, no delta` is the same with every file
compressed on its own: the bound when a day's answer shares no bytes with the
day before. Attempts: one answer per question per day, or 4 (en-offline-retry's
retries), each answered in full, which is an upper bound. In practice a retry
follows only an attempt that got no answer.

| a year of | nodes | tree, 1 / 4 attempts | git, 1 / 4 | git no delta, 1 / 4 |
|---|---|---|---|---|
| lab rate: 12 checkpoints a day, capped after ~30 days (measured, 35 days) | 10 | 304 MiB / 1.2 GiB | 7 MiB / 10 MiB | 50 MiB / 196 MiB |
| | 100 | 3.0 GiB / 11.9 GiB | 66 MiB / 95 MiB | 497 MiB / 1.9 GiB |
| every answer near the 1 MiB cap, every day | 10 | 6.6 GiB / 26.6 GiB | n/a | 976 MiB / 3.8 GiB |
| | 100 | 66 GiB / 266 GiB | n/a | 9.5 GiB / 38 GiB |

Git deltas make history cheap while a node's list only grows by appending:
yesterday's checkpoints are the same bytes today, so a day costs only its new
checkpoints. The second row is the bound for a capsules node that keeps
answering near the cap (for example, a list windowed to its latest checkpoints,
which shifts its bytes every day). Use the "no delta" column there.

### Policy

1. **90 days in the tree.** The last 90 days keep every file the reporter
   wrote.
2. **Then a release asset per month.** On the first run after a month is more
   than 90 days old (the month's last day is 90 days back), every
   `*.response.json` of that month moves into one asset, `reports-<YYYY-MM>.tar.xz`, on the
   release `reports-<YYYY-MM>`. The tar is ordered by node, then day, and
   compressed with xz. A month of one node's answers repeats yesterday's
   checkpoints, so it compresses to under 1% (89 MiB of measured answers made a
   0.55 MiB asset).
3. **The digests stay.** `reports/archive/<YYYY-MM>.sha256` lists each moved
   file's SHA-256 by its path, in `sha256sum` form, and the asset's own SHA-256.
   Everything else in a day's directory stays: the request, the verification
   line, `NODE`, `peer-key.json`, `RUN.json`, `README.md`. Anyone can check a
   moved answer: download the asset, unpack it, and run `sha256sum -c`.
4. **Sooner if large.** If `reports/` in the tree passes 1 GiB, the oldest
   month moves even inside 90 days.
5. **History is never rewritten.** Moving a file bounds the checkout, not the
   clone: git keeps every byte once committed, and the record is append-only.
   At the lab rate that is fine, even at 100 nodes. If nodes answer near the cap
   every day, a year of history passes 1 GiB at about 10 nodes. Before that
   happens, answers should go straight to the month's asset with only the
   digests committed.

Not implemented yet. The first move is due 90 days after the first report.
Before then the reports check has to accept an answer whose bytes have moved
(the digest line in place of `<q>.response.json`), on the per-attempt layout.
A test also has to show that the digests in the repository still match the
released bytes.
