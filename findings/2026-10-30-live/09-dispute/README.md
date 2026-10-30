# Step 09 live, 2026-10-30: what this finding does and does not show

This note sits beside the generated `finding.json` and is not part of its proof list.

- **The disagreement is injected.** Twin B (lab-4) has a per-model `system_prompt` in its config
  (lab-only fault injection, `LAB_TWINS=1`, see `twin-fault.json`); the weights and model hash are the same
  as twin A's. The Result is real (sealed by the capsules plugin on lab nodes), but this is not organic twin
  traffic.
- **Only two copies of the Result were checked.** The referee (stevenmih-2) holds its issued copy and the
  requester (stevenmih-3) holds the delivered copy. The judged twins (stevenmih-1, stevenmih-4) hold none: the
  plugin delivers a verdict only to nodes with `share_adjudications` on, and it is off by default. The finding
  states "0 held Result line(s)" for both. The six checks hold for the copies that exist; nothing here shows a
  judged twin receiving and recording the ruling.
- **It cannot be re-run on the committed population.** The run used a four-node population (`LAB_TWINS=1 entries`
  adds lab-4). lab-4 is a twins-only node and is deliberately not in `population/live/population.json` or
  `nodes/` (ddfb92e), so `steps/09-dispute` on the committed population would report NOT_CHECKED for twin B's
  half. The node-to-key bindings behind four of the six checks (`referee_signed`, `cites_halves`,
  `recorded_received`, `matches_issued`) were read from each node's data directory at run time and are not in
  the proof; lab-4's declared entry is in git history at 7fb0029. `result_signed` and `result_is_adjudication`
  re-check from the committed bytes alone.
- **Pin, and which Result is new.** Rerun 2026-10-30T08:34Z on capsules `d1f4edc` (the lab build of step 05).
  The copied ledgers hold two Results, and `finding.json`, `checks.json` and `outcome.json` count and pass both:
  `02439259…` (bracket `lab-twins-20261030T083448Z`, sealed 08:34Z) is the only one produced at `d1f4edc`;
  `4df68071…` (bracket `lab-twins-20261030T014545Z`, sealed 01:45Z) is carried over in the same data dirs from
  the first run on `01f0921e`. So "2 held Result line(s)" per node is one new and one old-pin Result. Step 09
  has no bracket or time filter, so the generated files are left as the step wrote them rather than edited by hand.
