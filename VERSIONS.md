# Versions

[`versions.lock`](versions.lock) is the only place the loop's pins change: the
lab host build, the capsules plugin and the `evidence-request` crate it
verifies answers with, the reporter, and the capsulectl and evidencebook a
verifier re-checks saved answers with. One pin per line:

```
<name> <repository> <ref>
```

`<ref>` is a full 40-hex commit SHA or a released version tag. The `versions`
check (`scripts/check_versions_lock.py`) refuses a branch name or a short SHA,
because either can point somewhere else tomorrow.

The daily reporter writes the lock's sha256 into each day's `RUN.json` as
`versions_lock_sha256`, so each report names the pins it ran under. To see
which lock that was: `git log --format=%H -- versions.lock`, then
`git show <commit>:versions.lock | sha256sum` for each commit until one matches.

## Bumping a pin

Change that one line and open a pull request, for example when capsules 0.1.6
is tagged:

```diff
-capsules Mesh-LLM/capsules-plugin v0.1.5
+capsules Mesh-LLM/capsules-plugin v0.1.6
```

When mesh-llm #2620 merges, the lab host build becomes a single main commit:
replace the `mesh-llm` SHA with that commit and delete the `mesh-llm-pr-2620`
line.
