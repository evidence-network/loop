# Step 05's rehearsal inputs

- `settings.json`: each rehearsal node's `share_record_at_completion` and how it
  accepts a push (`accepted_via`). A node it does not name is `off`, the pinned
  capsules plugin's default.
- `controllers.json` and `keys/controller-*/`: one rehearsal controller per
  node, each with a TEST key, published on purpose and never used live. Step 05
  makes a real controller-binding/0 record (`same_controller_asserted`) for each
  node it names, signed by the controller's TEST key and the node's evidence key,
  and the Go binding verifier (`cross_language binding`) checks it
  (evidence-network/loop#134).
