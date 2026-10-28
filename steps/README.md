# steps/: the reference loop, one step at a time

`./loop.sh --stage 1 [--population rehearsal|live] [--out DIR] [--steps 01,02,...]`
runs the stage's steps in order. Stage 1 (M1) is steps 01 Seal, 02 Book,
03 Advertise and 12 Join.

`loop.sh` needs bash 4 or later and `timeout` from GNU coreutils. Without
either it stops with exit 2 before any step runs. On macOS, install a newer
bash and coreutils (`brew install bash coreutils`) and put coreutils' gnubin
directory first on PATH.

## A step

A step is an executable `steps/<NN>-<name>` (Python 3, standard library
plus the components pinned in `versions.lock`), run as:

```
steps/<NN>-<name> --population <population dir> --out <findings dir>/<NN>-<name>
```

- It asserts one fact and writes exactly one `finding.json` into its out
  directory with `steps/finding.py`'s `write()`, beside the proof bytes the
  finding names (each with its sha256).
- `PASS`: a verifier checked the assertion and it held, on the proof bytes.
  `FAIL`: it was checked and did not hold. `NOT_CHECKED`: it could not be
  checked; each part not checked is a `{what, why, issue}` entry naming the
  issue that owns it.
- Every component it uses is named in `pinned` by its `versions.lock` name.
  A step never uses an unpinned version.
- It exits 0 once it has written its finding, whatever the result. Non-zero
  means the step itself broke; `loop.sh` then records a `FAIL` for it.
- `steps/<NN>-<name>.setup.sh` installs what the step needs from
  `versions.lock` (CI runs it before the step). Nothing else installs for it.
- A test plants one failure per step and shows the step's finding turn `FAIL`.

`python3 steps/finding.py check <findings dir>` refuses a malformed finding.
Among what it refuses:

- A `NOT_CHECKED` finding whose `asserts` or `detail` claims a check: the
  words "checked", "witnessed" and "verified". Negated, they are allowed:
  "not checked", "not verified", "never verified", "could not be verified",
  "has not been checked". "unchecked", "unverified" and "NOT_CHECKED" are
  other words and are allowed too.
- A `PASS` with `not_checked` entries. A PASS covers its assertion fully.
  Anything outside the assertion that was not checked is not part of the
  result; the step says so in `detail` as a sentence, without claiming it
  was checked: "Outside this step's assertion: ...".
- A proof path that is absolute, starts with `/` or contains `..`. Proof
  paths are relative to the step's directory.

## The population

`population/rehearsal/` is the rehearsal set: demo nodes, the Alice and Bob
test keys, a mock rail. Its keys are test keys, published on purpose, and
never used by a live node. `population/live/` names our lab nodes. A step
reads `population.json` in the directory it is given.
