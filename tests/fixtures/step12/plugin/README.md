# The pinned capsules plugin's doctor and join replies

`doctor.json` is what `POST /api/plugins/capsules/tools/doctor` with
`{"probe": true}` returns, and `join.json` what `POST .../tools/join` returns,
for a healthy node, in the shapes of the pinned capsules plugin
(`crates/evidence-plugin/src/doctor.rs`, capsules-plugin PR #81). The check
names, states and member names are the plugin's; the detail text is
abbreviated. `@KEY_ID@` and the other `@...@` markers are filled in by
tests/test_step12_join.py, which signs the commitment pair with a test key.
