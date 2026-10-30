# Step 05 live, 2026-10-30: why this finding is NOT_CHECKED

This note sits beside the generated `finding.json` and is not part of its proof list.

- **The bindings apply.** The 15 current-build halves that cite a binding digest (stevenmih-1 4, stevenmih-2 4,
  stevenmih-3 7) name the bindings of lab-1 (`9ccf10a2…`) and lab-2 (`68a2b24d…`). Both files are read from each
  sender's `data_dir` (`controller-binding.json`) and their digests match the cited ones. At aac-go go/v0.16.0
  (AAC#458, AAC#471) `cross_language binding` verifies them: the bindings carry
  `key_types: ["ed25519", "bip340"]` and a Nostr-event signature for the bip340 key, and form, both signatures and
  window PASS for each, with the half's Ed25519 key named. Applying is not a trust verdict.
- **What stays NOT_CHECKED.** Revocation of the bindings that apply (loop#138: no revocation statement is read from
  the lab) and the half's key's role in the binding (AAC#468: the verifier takes either of the two keys). lab-3
  publishes no binding, so the halves of an unbound sender are a not_checked entry too. Three held halves come from a
  sender with no `nodes/` listing (lab-4, `ec79b768`); their producer signature verifies under the key the node
  accepted but the key is only self-attested (loop#174).
- **Earlier runs.** At go/v0.15.0 the same bindings read `unsupported_key_type` (loop#140, a not_checked entry),
  and before that `controller_binding_form` FAIL at loop main `24c1acf` (loop#136), because the extra `key_types`
  member breaks the v0 member set. Both overstated a key type the verifier then lacked; this run replaces them.
- **Run.** loop#168 head `ac0ca73` (aac-go go/v0.16.0), capsules `d1f4edc`,
  `LOOP_SEALED_SINCE=2026-10-30T07:20:00Z`, `nodes/` as on main. The generated files are left as the step wrote
  them.
