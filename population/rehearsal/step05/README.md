# Step 05's rehearsal inputs

- `settings.json`: each rehearsal node's `share_record_at_completion` and how it
  accepts a push (`accepted_via`). A node it does not name is `off`, the pinned
  capsules plugin's default.
- `controllers.json` and `keys/controller-*/`: one rehearsal controller per
  node, each with a TEST key, published on purpose and never used live. Step 05
  signs a controller declaration with each, binding the node keys it names. The
  declaration's format is the rehearsal's stand-in, not the controller-binding/0
  the pinned capsules plugin reads (capsules-plugin PR #77); no released
  verifier checks controller-binding/0 yet
  (action-state-group/agent-action-capsule#432).
