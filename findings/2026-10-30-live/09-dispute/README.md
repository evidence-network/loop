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
- **Pin.** The run was on capsules `01f0921e`; `versions.lock` on main has moved since, so `finding.py check`
  reports the pin mismatch, as it does for the 2026-10-29 findings.
