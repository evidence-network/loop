# Step 05 live, 2026-10-30: why this finding is NOT_CHECKED

This note sits beside the generated `finding.json` and is not part of its proof list.

- **The bindings are not checked, not shown to be wrong.** The 15 current-build halves that cite a binding
  digest (stevenmih-1 4, stevenmih-2 4, stevenmih-3 7) name the bindings of lab-1 (`9ccf10a2…`) and lab-2
  (`68a2b24d…`). Both files are read from each sender's `data_dir` (`controller-binding.json`) and their
  digests match the cited ones. Since loop#140 (PR #142), step 05 reads each as `unsupported_key_type`:
  the binding declares bip340 (action-state-group/agent-action-capsule#457), which aac-go go/v0.15.0 does
  not implement, so its signatures were not verified. This is a not_checked entry, never a PASS.
- **Cause.** The plugin's join ceremony (capsules-plugin#121, at the lab pin `d1f4edc`) binds each node's
  Ed25519 key to its operator's Nostr key. The binding it writes carries `key_types: ["ed25519", "bip340"]`,
  and the bip340 key's `sig` is a Nostr-event object `{created_at, sig}`. That follows AAC#457 and its spec
  PR #458, both open. Controller Binding v0 as implemented in go/v0.15.0 is Ed25519-only.
- **Earlier run.** At loop main `24c1acf` (loop#136) the same bindings read `controller_binding_form` FAIL,
  because the extra `key_types` member breaks the v0 member set. That FAIL overstated a key type the
  verifier lacks.
- **What stays as expected.** lab-3 publishes no binding: its halves are a not_checked entry, with lab-4's
  (`ec79b768`, no `nodes/` listing). No binding is verified, so the revocation (loop#138) and key-role
  (AAC#468) entries do not appear in this run.
- **What changes it.** AAC#458 merged, an aac-go release with bip340, a repin, and a rerun of this step.
  loop#140's interim branch is removed then.
- **Run.** loop main `40825fe` (loop#142, #143), capsules `d1f4edc`, aac-go go/v0.15.0,
  `LOOP_SEALED_SINCE=2026-10-30T07:20:00Z`, `nodes/` as on main. The generated files are left as the step
  wrote them.
