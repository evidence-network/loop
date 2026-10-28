# stats/

`stats/data/daily/<YYYY-MM-DD>.json` is the scrubbed set: what one day of the
daily reporter's raw reports may show in public. evidence-network/loop-data's
nightly Action writes it with `scripts/scrub_reports.py scrub` and pushes it
here. `scrub_reports.py check` and `site/stats_model.py` refuse any file that
is not exactly this format. The site in `site/` reads it.

One file per day: JSON with sorted keys, a 2-space indent and a trailing
newline. The file holds no bytes of any answer, request, peer key or
verification text.

| key | value |
|---|---|
| `date` | the day, `YYYY-MM-DD`, the file's own name |
| `run` | `settled` (the run finished: `RUN.json`) or `missed` (`MISSED`) |
| `missed_reason` | the reporter's reason, up to its first `:`; `null` when settled |
| `versions_lock_sha256` | the sha256 of the `versions.lock` the run ran under; `null` when missed |
| `reporter_key_id` | the reporter's key id; `null` when missed |
| `listed` | how many nodes the run asked; `0` when missed |
| `nodes` | one object per node asked, sorted by `node_id`; `[]` when missed |

Each node: `node_id`, `node_line` (the reporter's `NODE` line),
`absent_days_running`, `left_on` (`YYYY-MM-DD` or `null`), and `questions`
with one object for each of `checkpoints` and `history_card`:

| key | value |
|---|---|
| `state` | the day's result: `verified`, `no_checkpoint_yet`, `no_announced_key`, `not_evidence`, `refusal` or `absence` |
| `attempts` | each attempt's `at` (UTC) and `state`, earliest first |
| `log_size_at_checkpoint` | `{mmr_size, timestamp}` of the largest checkpoint in a verified answer; else `null` |
| `records_at_checkpoint` | how many records the node's log holds at that checkpoint: the leaf count of an MMR of `mmr_size` nodes (an MMR of n leaves has 2n − popcount(n) nodes); `null` exactly when `log_size_at_checkpoint` is `null` or `mmr_size` is no MMR's size |
| `answer_sha256` | the SHA-256 of the answer's bytes; `null` when nothing came back |

The stats page reads a node's records sealed on a day as its
`records_at_checkpoint` that day minus its last earlier known one. A lower
count is a drop: the page draws a gap with a note, never a negative value.
