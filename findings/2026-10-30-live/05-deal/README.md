# Step 05 live, 2026-10-30: why this finding is FAIL

This note sits beside the generated `finding.json` and is not part of its proof list.

- **The FAIL is a key type the pinned verifier lacks, not a binding shown to be wrong.** The 15
  current-build halves that cite a binding digest (stevenmih-1 4, stevenmih-2 4, stevenmih-3 7) name the
  bindings of lab-1 (`9ccf10a2…`) and lab-2 (`68a2b24d…`). Both files are read from each sender's `data_dir`
  (`controller-binding.json`) and their digests match the cited ones. `cross_language binding` (aac-go
  go/v0.15.0) reports `controller_binding_form` FAIL for both.
- **Cause.** The plugin's join ceremony (capsules-plugin#121, at the lab pin `d1f4edc`) binds each node's
  Ed25519 key to its operator's Nostr key. The binding it writes carries `key_types: ["ed25519", "bip340"]`,
  and the bip340 key's `sig` is a Nostr-event object `{created_at, sig}`. That follows
  action-state-group/agent-action-capsule#457 and its spec PR #458, both open. Controller Binding v0 as
  implemented in go/v0.15.0 is Ed25519-only, with an exact member set and hex signatures.
  - The extra `key_types` member breaks the member set, so the form check FAILs.
  - Checked on lab-1's binding outside the step: with `key_types` removed, form and window PASS, but
    signatures still FAIL, because v0.15.0 cannot verify the Nostr-event signature.
- **What stays as expected.** lab-3 publishes no binding: its 8 halves are a not_checked entry. 3 halves
  from lab-4 (`ec79b768`, no `nodes/` listing) are unlisted. No binding applies, so the revocation
  (loop#138) and key-role (AAC#468) entries do not appear in this run.
- **What changes it.** loop#140 reads a binding whose key type the pinned verifier lacks as unsupported
  (NOT_CHECKED, citing AAC#457) instead of FAIL. A verifier that checks the bindings needs AAC#458 and an
  aac-go release with bip340, then a repin and a rerun of this step.
- **Run.** loop main `24c1acf` (loop#136), capsules `d1f4edc`, aac-go go/v0.15.0,
  `LOOP_SEALED_SINCE=2026-10-30T07:20:00Z`, `nodes/` as on main. The generated files are left as the step
  wrote them.
