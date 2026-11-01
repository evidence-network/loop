# Lab bring-up: three nodes on one host

This runbook brings up the loop's three lab nodes (`lab-1`, `lab-2`, `lab-3`
in [`population/live/`](population/live/population.json)) on one lab host,
joins each with [`join.sh`](join.sh), runs stage 1 once against them, and
packs everything that goes back into this repository into one tarball.

You run it by hand, in a shell on the lab host that you reach from your
laptop. Nothing here pushes: the nodes' credentials cannot write to this
repository, so you copy the tarball back and a maintainer commits it.

`lab/lab.sh` does each step. Every command takes `--dry-run`, which prints
the commands it would run (and, for `pack`, the files it would take) and
changes nothing. Read a dry run before each real step the first time.

```
lab/lab.sh [--dry-run] check|tools|start|status|exchange|join|witness|bind|entries|judge-key|commitments|loop|admit|deal|bundles|settle|twins|dispute|ask|pack|stop|all
```

`all` runs the sections below in order: `check`, `tools`, `start`,
`exchange`, a wait for each node's first checkpoint, `join`, `witness`, `bind`,
`entries`, `commitments`, `loop`, `pack`. It stops at the first command that fails: a build that is
not the pinned one (`check`) or a step setup that failed (`tools`) stops it
before any node starts, with each reason in `$LAB_DIR/build.json`.

## 0. Where things live

Pick a directory on the lab host for this lab, and use it for every command:

```sh
export LAB_DIR=$HOME/loop-lab          # any empty directory you own
```

| Path | What it holds |
| --- | --- |
| `$LAB_DIR/nodes/<name>/` | the node's `HOME` (mode 0700): its mesh key, `config.toml`, and its capsules plugin data under `.local/share/capsules` (plugin key, book, `self-peer-id`) |
| `$LAB_DIR/nodes/<name>/join-book/` | the book `join.sh` creates (`--home`) |
| `$LAB_DIR/controllers/<name>/` | the controller key `join.sh` binds the node key to |
| `$LAB_DIR/join/<name>/` | what `join.sh` wrote (`--out`): binding, book, commitment, probe |
| `$LAB_DIR/run/` | pid files, launch scripts and the mesh invite token (mode 0700), and each node's plugin build mark `<name>.build.json` (the plugin binary's sha256 and since when the node runs it, which `start` writes and step 04 reads; not `$LAB_DIR/build.json`) |
| `$LAB_DIR/logs/<name>.log` | each node's log. It holds the invite token: never copy a log off the host |
| `$LAB_DIR/build.json` | what `check` confirmed about the build, and each `tools` setup's exit code |
| `$LAB_DIR/out/loop-lab-<date>.tgz` | the tarball you bring back |

`config.toml` also grants the capsules plugin the admission hook (`[plugin.openai_exchange_grant]`, `admission = true`, at `backend_selected`), which is what makes the routing node seal an `admission_decision` record for step 04. `start` writes it, so a lab started before this needs `stop` and then `start`.

Ports: `LAB_PORT_BASE` (default 9800) gives each node an API port
(base+1..3), a console (base+11..13) and a QUIC port (base+21..23):

| Node | Role | API | Console | QUIC |
| --- | --- | --- | --- | --- |
| lab-1 | answers; makes the mesh and serves the model | 9801 | 9811 | 9821 |
| lab-2 | answers | 9802 | 9812 | 9822 |
| lab-3 | answers; the reporter node (`LAB_REPORTER`) | 9803 | 9813 | 9823 |

The lab host may run other nodes. `start` refuses a port that is already in
use, and nothing here touches another node's processes, ports or data. If a
port is taken, choose another `LAB_PORT_BASE` and use it for every command.

## 1. Prerequisites

### The build, already on the host

The lab host build and the capsules plugin are built already. This runbook
uses them; it builds nothing. They must be at the [`versions.lock`](versions.lock)
pins:

- `mesh-llm`: mesh-llm main at the `mesh-llm` pin, with PR #2620 at the
  `mesh-llm-pr-2620` pin (#2620 is still open, so the build carries both);
- `capsules`: the capsules plugin at the `capsules` pin.

```sh
grep -E '^(mesh-llm|mesh-llm-pr-2620|capsules) ' versions.lock
```

Tell `lab.sh` where they are:

```sh
export MESH_LLM_BIN=/path/to/mesh-llm          # the lab host build's binary
export MESH_LLM_SRC=/path/to/mesh-llm/source   # the git tree it was built from
export MESH_LLM_RUNTIME=/path/to/native-runtimes   # if the build keeps its runtimes beside it
export CAPSULES_BIN=/path/to/capsules          # the plugin binary
export CAPSULES_SRC=/path/to/capsules/source   # the git tree it was built from
export LAB_MODEL=/path/to/model.gguf           # any small GGUF chat model; lab-1 serves it
export LAB_OPERATOR=<your GitHub handle>       # without @; goes into each nodes/ entry
```

### Confirm each SHA before starting

```sh
lab/lab.sh check
```

It reads the three pins from `versions.lock` and checks, with git:

- the mesh-llm tree contains the `mesh-llm` pin
  (`git merge-base --is-ancestor <pin> HEAD`) and the `mesh-llm-pr-2620` pin,
  and has no uncommitted changes to tracked files;
- the capsules tree is exactly at the `capsules` pin (`git rev-parse HEAD`),
  with no uncommitted changes;

A `capsules` pin that is a tag (such as `v0.1.5`) is resolved to its
commit in the capsules tree (`git rev-parse <tag>^{commit}`, after
`git fetch --tags` if the tree does not have the tag yet), and HEAD must be
that commit; a full-SHA pin is compared as it is. `check` then records
both trees' HEADs, the capsules pin and its commit, both binaries' sha256
and `mesh-llm --version` in `$LAB_DIR/build.json`. It writes
`build.json` on every outcome: when anything fails (a pin missing from a
tree, an unset path, a binary that is not there), `build.json` reads
`"ok": false` with each reason in `failures`, and `check` exits 1 naming
each one. git proves what a tree is at, not what a binary was built from:
compare the two sha256 lines it prints with the ones your build recorded
when it was made. If they differ, or `check` exits 1, stop here and report
what it printed; do not start nodes on a build the lock does not name.

Two cases that look like failures but are not:

- The mesh-llm tree is a local merge of the two pins (its HEAD is neither
  pin): that passes, because the check is containment, not equality.
- The capsules tree sits at a commit before the pin tag, and the tag adds
  only files that are not built (a changelog, install notes): compare with
  `git diff --stat HEAD <tag>`. If nothing built differs, `git checkout
  <tag>` in that tree, then run `check` again; the plugin binary does not
  need rebuilding. If anything built differs, stop: the binary is not the
  pinned plugin.

Point `MESH_LLM_SRC` and `CAPSULES_SRC` at the source trees the binaries
were built from, not at a build manifest or a release bundle: `check` reads
the trees.

### Tools

On the lab host: `git`, `curl`, `python3`, `openssl` 3, `tmux`, `ss`
(iproute2). Then install every stage-1 step's pinned tools (capsulectl and
capsulectl-book for `join.sh`, and what steps 01 to 03 run), each checked
against its release's checksums:

```sh
lab/lab.sh tools
```

`tools` runs every `steps/<NN>-<name>.setup.sh`, even after one fails,
and records each one's exit code under `tools` in `$LAB_DIR/build.json`.
If any setup failed, it names each one with its exit code, exits 1, and
`build.json` reads `"ok": false` (as does every later `check`) until a
`tools` run passes them all. `entries` refuses such a build. Report what it
printed rather than starting nodes without a step's tools.

`tools` also builds step 02's driver
(`~/.cache/evidence-network-loop/02-book/bin/loop-step02-book`), which the lab
test witness signs its receipts with (`lab/lab.sh witness`, section 3). Run
`lab/lab.sh tools` again after every pull that changes `steps/02-book.d/`,
then restart the witness (`kill $(cat $LAB_DIR/run/witness.pid)`, then
`lab/lab.sh witness`) so it signs with the new build.

## 2. Three nodes on one host

```sh
lab/lab.sh --dry-run start      # read it
lab/lab.sh start
```

What `start` does, following the usual three-node pattern (one node makes a
private mesh, two join it by an invite file):

1. Each node gets its own `HOME` under `$LAB_DIR/nodes/<name>` (mode 0700),
   and with it its own mesh key, config, plugin data dir, book and runtime
   cache. Its own ports, as in the table above.
2. Each node runs as `mesh-llm serve`, never `--client`: a client node gets a
   new id at every start, and a node's plugin key is bound to its id.
3. **No relay.** Every node runs with `--disable-iroh-relays` and reaches the
   others over loopback, until Mesh-LLM/capsules-plugin#48 is closed. Never
   `--publish`: `start` stops all three if any node reports a
   `publication_state` other than `private`.
4. `CAPSULES_PEER_KEYS` is unset for every node: each plugin learns the
   others' keys only from the mesh's key announcement.
5. `CAPSULES_HOST_CONSOLE_URL` points each plugin at its own node's console.
   Without it the plugin asks the default console, which on a shared host is
   another node's.
6. lab-1 starts first with `--gguf $LAB_MODEL` and makes the mesh. Its invite
   token goes from its status straight into `$LAB_DIR/run/invite.token`
   (0600): never onto the screen or into an argument. lab-2 and lab-3 start
   with `--join-file` and wait until they can route to lab-1's model.
7. It waits until each plugin knows its own node id
   (`.local/share/capsules/self-peer-id`), then prints `status`.

The three run in one tmux session, `loop-lab`, one window per node:
`tmux attach -t loop-lab` to watch (detach with `C-b d`; do not `C-c` a
node, use `lab/lab.sh stop`). The launch script for each node is
`$LAB_DIR/run/<name>.sh`, so a node can be started again by hand with
`tmux new-window -d -t loop-lab -n <name> "bash $LAB_DIR/run/<name>.sh >> $LAB_DIR/logs/<name>.log 2>&1"`
(through `bash`, never the bare path: where the script cannot be executed, a bare path closes
the window with an empty log). A node that exits within 5 seconds of its launch, or dies while
`start` waits on it, stops `start` at once with the last lines of its log.

Check:

```sh
lab/lab.sh status
```

Each node should be `up`, show a node id, and its console's
`/api/plugin-keys` should list three keys: its own and the other two. If
not, see [A node will not announce its key](#a-node-will-not-announce-its-key).

### Give every book a record

A node answers `checkpoints` with a checkpoint only after it has one, and
the plugin cuts its first checkpoint 300 seconds after activity. Before
that it answers a signed `coverage_unsatisfiable`, which the reporter
records as `no_checkpoint_yet`. So make one exchange per node, then wait:

```sh
lab/lab.sh exchange     # lab-2 and lab-3 each ask lab-1 for one chat completion
sleep 360
```

Each side seals its half, so all three books have a record.

## 3. Join each node, and what goes back

### join.sh, for each node

```sh
lab/lab.sh --dry-run join
lab/lab.sh join
```

For each node this:

1. makes a controller key, `$LAB_DIR/controllers/<name>/controller-key.pem`
   (Ed25519, 0600), unless one is already there;
2. runs the one command, [`join.sh`](join.sh) (see [JOIN-LAB.md](JOIN-LAB.md)):

   ```sh
   ./join.sh --name <name> \
     --node-key $LAB_DIR/nodes/<name>/.local/share/capsules/keys/node-key.pem \
     --controller-key $LAB_DIR/controllers/<name>/controller-key.pem \
     --out $LAB_DIR/join/<name> --home $LAB_DIR/nodes/<name>/join-book
   ```

   The node key is the capsules plugin's own key, read where the plugin
   keeps it, so the commitment `join.sh` writes is under the same key the
   node's `nodes/` entry lists;
3. deletes `join-book/node.seed`, the copy of the node key `join.sh` made
   for its book profile. The join is finished with it, and the plugin's data
   dir keeps the only copy of each node key.

When all three are joined, take the controller keys off the host (JOIN-LAB:
keep the controller key off the node once the binding is signed). Copy
`$LAB_DIR/controllers/` to your laptop with `scp`, keep it as you keep other
private keys, then delete it on the host with `rm -r $LAB_DIR/controllers`.
It never goes into the tarball.

`$LAB_DIR/join/<name>/join.json` lists what the join did and what is still
unchecked. On the lab build, the binding, `book doctor --probe` and the
commitment's final format read as unchecked; that is expected (JOIN-LAB,
"What the lab build does not do yet").

### bind, for each node but the reporter

```sh
lab/lab.sh --dry-run bind
lab/lab.sh bind
```

Run it after `join`, with the nodes running, and before `entries`, `loop` and `pack`: step 12 reads
the plugin's commitment, which names the binding's digest only after the bind. For each node but the
reporter this runs the plugin's own join ceremony (capsules-plugin#121) with a lab test Nostr key,
`$LAB_DIR/controllers/<name>/nostr.key` (hex, 0600, made if absent): `join {"controller": <pubkey>}`
returns an event, [`lab/nostr_sign.py`](lab/nostr_sign.py) signs it (BIP-340), `join
{"controller_event": ...}` completes the binding, and `doctor` must then report
`controller_binding: bound`. The event goes only to the node's own console and is never published to a
relay; the output names the binding digest only. The reporter stays unbound on purpose: the binding is
optional, so one live run of step 12 shows both states. A node that already reports bound is left as it
is unless `LAB_REBIND=1`. The key stays in `$LAB_DIR/controllers/`, which the pack never takes.

### The lab test witness

```sh
lab/lab.sh --dry-run witness
lab/lab.sh witness
```

Run it after `join` and `tools`, before `loop`. It sets up the lab's test
witness, the witness step 02 checks each book's checkpoint receipt against:

1. makes the witness's own Ed25519 key, `$LAB_DIR/witness/witness-key.pem`
   (dir 0700, key 0600), unless one is already there. It never goes into this
   tree or the tarball, and it is never a node's key: `lab/witness.py list`
   refuses a key that is any node's plugin key or controller key;
2. makes a self-signed certificate for 127.0.0.1 beside it
   (`tls-cert.pem`, `tls-key.pem`): the pinned CLL witness client speaks only
   HTTPS;
3. adds the key's public half to
   [`lab/witness/witnesses.json`](lab/witness/witnesses.json) as its own
   entry, `{name: lab-test-witness, key_id, not_before}`. Entries are only
   added: a key already listed is left as it is, and a rotated-out key keeps
   its entry, so the receipts it signed still verify;
4. points each node's join-book profile at the witness
   (`capsulectl profile update --checkpoint-endpoint https://127.0.0.1:<port base+30>
   --checkpoint-public-key <key id>`);
5. starts [`lab/witness.py`](lab/witness.py) in the tmux session, serving
   `POST /checkpoints` on that port. Each receipt names the witness's key id
   and the time it signed. `lab/lab.sh stop` stops it.

Commit the updated `lab/witness/witnesses.json` (the public key only) in a
PR. Until that entry is in the clone the loop runs from, step 02 FAILs
`witness_listed` on each node. This is one organization's test witness for its
own lab, not a witness for the network.

To rotate: move `witness-key.pem` aside, run `lab/lab.sh witness` (a new key,
a new entry, the old entry kept), then restart the witness
(`kill $(cat $LAB_DIR/run/witness.pid)`, then `lab/lab.sh witness`), and
commit the listing.

### The entries

```sh
lab/lab.sh --dry-run entries
lab/lab.sh entries
```

This writes, in this clone (it commits nothing):

- **`nodes/<node_id>.json`** for each node. `node_id` is the plugin's
  `self-peer-id`, `key_id` its plugin key (`keys/node-key.pub.pem`), both read
  by `scripts/reporter_key.py`'s reader from the public files only. `consent`
  is `CONSENT` from [`scripts/check_nodes.py`](scripts/check_nodes.py),
  whatever that constant holds in this clone: pull this repository before
  running `entries`, so the entries carry the statement as it is now.
  `host_build` names the mesh-llm tree, the pins it contains, the binary's
  sha256, and that the node runs with no relay until
  Mesh-LLM/capsules-plugin#48 is closed; `plugin_version` names the plugin
  pin, the commit the plugin tree is at, and its binary's sha256. Then `scripts/check_nodes.py nodes` must say
  `3 entries ok`;
- **`population/live/population.json`** with the three real node ids;
- **`reporter/reporter.json`** with the reporter node's values (below).

`entries` refuses if `$LAB_DIR/build.json` says the build is not the pinned
one.

### The live judge key

```sh
lab/lab.sh --dry-run judge-key
lab/lab.sh judge-key
```

Run it once, after `entries`, while no `lab.sh` or `loop.sh` run is going. It
makes the key steps 10 and 11 seal and check live Results under
(evidence-network/loop#189):

1. makes one Ed25519 key, `$LAB_DIR/judge/judge.pem` (dir 0700, key 0600),
   unless one is already there. It never goes into this tree or the tarball
   (`lab/pack.py` refuses any private key);
2. refuses it if it is any population node's key;
3. writes its public half to `population/live/step10/keys/judge.pub.pem`, and
   refuses to overwrite one that is another key's: a new judge key is a new
   decision on that issue.

Commit `population/live/step10/keys/judge.pub.pem` in a PR. Step 10 then reads
the key from `LOOP_JUDGE_KEY`, default `$LAB_DIR/judge/judge.pem`. No `lab.sh`
command runs steps 10 and 11 yet; run them after `loop`, into the same day's
findings:

```sh
env LAB_DIR="$LAB_DIR" steps/10-judge --population population/live --out findings/<day>-live/10-judge
env LAB_DIR="$LAB_DIR" steps/11-report --population population/live --out findings/<day>-live/11-report
```

The lab account that runs the nodes can read the key, and steps 10 and 11 say
so in every live finding: same operator, not an independent judge.

### The commitments

```sh
lab/lab.sh --dry-run commitments
lab/lab.sh commitments
```

Run it after `bind` and `entries`, with the nodes running. For each live node it asks the node's own console for
the plugin's `commitment` tool (capsules-plugin#75) and writes, in this clone (it commits nothing):

- **`nodes/commitments/<node_id>.json`**: the `commitment` member exactly as the plugin returned it, not
  re-serialized;
- **`nodes/commitments/policies/<node_id>.json`**: the policy bytes (the reply carries them in base64), which
  the commitment's `lcap.policy_uri` and `lcap.policy_digest` name.

The harness signs nothing. `commitments` refuses a commitment that `scripts/check_commitment.py` does not
verify (which also refuses any member outside the profile, so no controller key or npub can ride along; a
binding appears only as `controller_binding.digest`), one with no entry `nodes/<node_id>.json` here, or one not
under the key that entry lists. Run it again after a later bind or checkpoint: a commitment that changed has
its file replaced, one that did not is left alone. `pack` takes both files for each node and stops if either is
missing or holds an `npub`/`nsec`. Step 03 reads them from this repository's `HEAD`, so they count once they
are merged and the lab clone is up to date; the findings PR that carries them is the publication.

## 4. The reporter node

`lab-3` is the reporter node (`LAB_REPORTER` chooses another). It is a lab
node like the others, and it is also the node the daily reporter will ask
from.

**Key custody.** The reporter's key is lab-3's capsules plugin key. The
plugin made it on first start, at
`$LAB_DIR/nodes/lab-3/.local/share/capsules/keys/node-key.pem`, mode 0600,
owned by you. As [reporter/README.md](reporter/README.md) says:

- it stays in that data dir, readable by your user only. Check:
  `stat -c '%U %a' $LAB_DIR/nodes/lab-3/.local/share/capsules/keys/node-key.pem`
  prints your user and `600`. `entries` refuses otherwise;
- it is **never backed up and never copied**: keep `$LAB_DIR/nodes/lab-3`
  out of any backup or sync on the host, never `scp` it, never put it in the
  tarball (`pack` refuses a file holding a private key). `join` reads it in
  place and deletes the one seed copy `join.sh` makes; check
  `ls $LAB_DIR/nodes/lab-3/join-book/node.seed` says no such file;
- if it is lost, the plugin makes a new one, which is a new `key_id`: run
  `entries` again and send the new `reporter.json`. Reports signed under the
  old key stay as they are.

**Its `reporter/reporter.json`.** `entries` fills `node_id` and `key_id`
(as `scripts/reporter_key.py --write` does), `operator` (`LAB_OPERATOR`),
`mesh` (`lab`), `host_build` and `plugin_version` (from `build.json`, as in
the `nodes/` entries), and leaves `retry_after_s` as it is. Compare with
`python3 scripts/reporter_key.py $LAB_DIR/nodes/lab-3/.local/share/capsules`.

**Not yet:** do not install the reporter's systemd timers or run
`scripts/run_daily.sh`. Setting up the daily reporter is separate work and
comes after these entries are merged. When it is set up, it must ask from
lab-3's console (`--console http://127.0.0.1:9813`), not the default 3131.

## 5. Stage 1, once

```sh
lab/lab.sh --dry-run loop
lab/lab.sh loop
```

This runs, from this clone:

```sh
LOOP_CONSOLE=http://127.0.0.1:9813 LOOP_SELF_NODE=lab-3 LOOP_SECOND_CONSOLE=http://127.0.0.1:9811 \
  LAB_DIR=$LAB_DIR LOOP_WITNESS_CA=$LAB_DIR/witness/tls-cert.pem \
  ./loop.sh --stage 1 --population live --out findings/<date>-live
```

`LOOP_WITNESS_CA` is the lab test witness's certificate: step 02 hands it to
capsulectl (as `SSL_CERT_FILE`, for its own calls only) so the book can deliver
its checkpoint over HTTPS. Run step 02 by hand with it set too; without it
the delivery fails TLS and the witness part is NOT_CHECKED, saying why.

`LOOP_CONSOLE` is the reporter node's console: step 12 asks each other node
through it. A node can't be asked through its own console, so
`LOOP_SELF_NODE` names the reporter node and step 12 asks it through
`LOOP_SECOND_CONSOLE`, lab-1's console (lab-2's when lab-1 is the reporter).
Without a second console the reporter node's probe is NOT_CHECKED, not FAIL.
`lab/lab.sh loop` also sets `LOOP_NODE_CONSOLES` (`NAME=URL` per node, comma separated), each node's own
console, so step 12 reads each node's `doctor` for its controller binding. `admit`, `deal` and `dispute` set it too:
steps 04, 05 and 09 ask each node there for the signed checkpoint over each ledger file they copy (loop#288).
The finding's detail says which console asked each node. `loop.sh` prints one line per step; the result is in each
`findings/<date>-live/<NN>-<name>/finding.json`, not in the exit code.

`loop.sh` runs one step per stage-1 number: the file in `steps/` named
exactly `<NN>-<name>` (`01-seal`, `02-book`, `03-advertise`, `12-join`).
The setup scripts, docs, constraints and data beside them
(`01-seal.setup.sh`, `01-seal.constraints.txt`, `02-book.d/`,
`03-advertise.md`, ...) are never run as steps.

Steps 01 and 02 find each node's own key and book through
`population/live/population.json`, which `lab/lab.sh entries` filled:

- `data_dir`: `$LAB_DIR/nodes/<name>/.local/share/capsules`, the plugin's
  data dir, holding `keys/node-key.pem` and `keys/node-key.pub.pem`;
- `book_dir`: `$LAB_DIR/nodes/<name>/join-book`, the `--home` `join.sh`
  made the node's book in.

Both are written with `$LAB_DIR` unexpanded, so no host path goes into the
repository; the steps expand it from the environment, which is why
`lab/lab.sh loop` passes `LAB_DIR` to `loop.sh`. A node whose path is unset
or not readable by the user running the loop is NOT_CHECKED, never a FAIL,
and says which: `book_dir names $LAB_DIR, which this step's environment does
not set` when `LAB_DIR` is not in the environment (the step also prints it),
`book_dir has no readable <file>` when a file it needs is missing, or
`data_dir` (or `book_dir`) `not set or unreadable`. Step 02 finds each book's
profile by its store (`join.sh` names it after `lab-N`, not after the node's
name in `population.json`).

What to expect from each step on the live population today, so each
result is read for what it is. Every step writes one finding, the worst
over the three nodes, and its `detail` lists each node:

- `01 seal`: each node seals one record with its own plugin key from its
  `data_dir`, and the second-language verifier checks Class 1 on the
  sealed bytes, and its producer signature too (`cross_language signature`).
  PASS when every node's capsule verifies under that node's key and that
  key is the one its `nodes/<node_id>.json` publishes. NOT_CHECKED for a
  node whose `data_dir` is unreadable, a node with no `nodes/<node_id>.json`
  listing (the key is then only self-attested), or when
  a pinned tool is missing (run `lab/lab.sh tools`). A FAIL names the node
  and the check, such as an invalid signature, a record sealed with no
  signature, or a different published key.
- `02 book`: for each node, the pinned capsulectl appends a capsule sealed
  with the node's key to the book under its `book_dir` and verifies it
  under that book's checkpoint key. It then delivers the new checkpoint to
  the lab test witness (`capsulectl cll checkpoint publish`), and
  `capsulectl cll checkpoint status` re-verifies the receipt the book stored.
  The step picks the receipt's entry in `lab/witness/witnesses.json` by the
  receipt's key id. PASS when every node's book checks hold and its receipt
  is from a listed key that is not any node's key, is the key it verifies
  under, and signed at or after its entry's `not_before`. NOT_CHECKED for the
  witness part when a book names no witness (run `lab/lab.sh witness`) or its
  witness has not answered (is it running? is `LOOP_WITNESS_CA` set?). A FAIL
  names the node and the check, and covers a delivery capsulectl reports as `failed`.
- `03 advertise`: reads each node's commitment from this repository's
  `nodes/commitments/` and signs nothing for a live node. NOT_CHECKED
  until each node's commitment, which the pinned plugin signs and serves
  (capsules-plugin PR #75), is published there.
- `12 join`: asks each listed node `checkpoints` and `history_card/1` from
  lab-3 and judges each answer under the key the node's host announces.
  Through each node's own console (`LOOP_NODE_CONSOLES`, which `loop` sets)
  it reads the controller binding: the digest the node's `doctor` reports
  must match the one its signed commitment names, or neither names one.
  The pinned plugin's `join` runs the binding ceremony (capsules-plugin PR
  #121); step 12 reads the binding it leaves. The plugin's own `join` and
  `doctor --probe` run by the step (evidence-network/loop#79) and the signed
  join/1 the join switch sends the reporter (step 12 does not read it yet) read
  NOT_CHECKED, so the step reads NOT_CHECKED at best. A FAIL names the node and the check.

A FAIL is reported with the tarball as it is; do not work around it on the
host.

### Step 04, the admission decisions

Stage 1 does not include step 04 (evidence-network/loop#127). `lab/lab.sh admit`
runs `steps/04-admit` on `population/live` into the same `findings/<date>-live/`
as `04-admit/`, then checks the finding contract over the whole directory. It
reads the admission decisions each node's ledger sealed for its exchanges,
and pays nothing. Run it after `exchange` and `loop`:

```sh
lab/lab.sh --dry-run admit
lab/lab.sh admit
```

`pack` takes `04-admit/` with the rest of the day's findings.

### Step 05, both halves of a deal

Stage 1 does not include step 05. It needs one deal whose two halves were each
pushed to and held by the other side, so both nodes of an exchange must have
`share_record_at_completion` on (`counterparty`) when it runs. At the pinned
plugin that is its default, so nodes whose config leaves it unset may already
hold halves from earlier exchanges. Try that first:

```sh
lab/lab.sh --dry-run deal
lab/lab.sh deal
```

If the finding reads NOT_CHECKED for want of a held half, restart with the
push forced on for both sides of one exchange, and run one. `exchange` has
lab-2 and lab-3 ask lab-1, so lab-1 and lab-2 make one deal whose halves both
cross. The reporter's timers ask these nodes, so stop them while the nodes are
down, or their slots read MISSED:

```sh
systemctl --user stop loop-reporter.timer loop-reporter-rehearsal.timer
lab/lab.sh stop
LAB_SHARE_AT_COMPLETION="lab-1 lab-2" lab/lab.sh start
systemctl --user start loop-reporter.timer   # and loop-reporter-rehearsal.timer, if it was running
lab/lab.sh exchange
lab/lab.sh deal
```

`start` passes `CAPSULES_SHARE_RECORD_AT_COMPLETION=counterparty` to each named
node's plugin, which reads it before its config, so a config that turned the
push off does not apply to those nodes. A node not named keeps the plugin's
default, on at the pin, unless its config sets it off. The setting is read at
start, so restart to change it. Each side pushes its record when the exchange
completes, provided it knows the other side's key (the host announces it).

`deal` runs `steps/05-deal` on `population/live` into the same
`findings/<date>-live/` as stage 1, as `05-deal/`, then checks the finding
contract over the whole directory. It starts no exchange and pays nothing: it
reads the halves each node holds. A node with no held half reads NOT_CHECKED.
`pack` takes `05-deal/` with the rest of the day's findings.

### Step 06, settlements recorded

Stage 1 does not include step 06 either. `lab/lab.sh settle` runs `steps/06-settle`
on `population/live` into the same `findings/<date>-live/` as `06-settle/`, then
checks the finding contract over the whole directory. It reads each node's
capsules ledger, lists the settlements recorded there (`<node>/recorded.json`) and
pays nothing. The live population names the Lightning rail, so 06 judges the
loop#243 checks: each payment's reference, its finality by the payer's preimage,
one band per side per exchange, a fee paid to a third party, the two books'
amounts, and each node's `counterparty_count` from `lab/lab.sh bundles` (run it
first, after the paid exchanges). All of them pass for a PASS, whose detail
names the one gap outside it (no public ledger, loop#247). `pack` takes
`06-settle/` with the rest of the day's findings.

What a paid exchange costs, measured live on 2026-11-01 (loop#300; lab-2 pays,
lab-1 sells the Qwen model; the lab host build's mesh-llm payments code):
- The loop sets no amount. `lab.sh exchange` sends one `max_tokens` 8 request; the
  amount is the seller's (lab-1) minimum payment, which is 1,000 msat by default
  and which `mesh-llm wallet min-payment` and `admission-deposit` take only as
  1 to 1000 sats. A cold peer pays a 1,000 msat deposit that covers the 2 msat
  bill, so the exchange costs 1,000 msat; a peer the seller already trusts pays
  no deposit and one final invoice raised to the same 1,000 msat. It is not
  2,000 msat (that figure is the Goose test's direct 2 sat payment).
- The served model must be priced on lab-1 (`mesh-llm wallet pricing <model>`);
  an unpriced model is served free and settles nothing.
- 10 and 100 msat (the seller's minimum set to them in its ledger) both fail: HTTP
  402 "paid inference interrupted", lab-2's charge row (amount 10 or 100) goes to
  `failed` with total 0. The row exists only after mesh-llm's own checks passed
  (buyer admission and `prepare_charge`), so the failure is in the wallet's pay
  step, not the 1,000 msat floor. A failed attempt leaves a payment hash in the
  payer's book alone, which step 06 reads as unproven (`finality`) and unpaired
  (`exchange_match`), so do not probe on the live lab before a run.
- Only lab-1 serves a model, so payments go lab-2 to lab-1 (or lab-3 to lab-1,
  which has no wallet); the wallets cannot ping-pong today.
- The 9,000 msat per direction limit is the lab payment guard Ethan set (with 3,000 msat per
  payment), counted by hand in lifetime msat; nothing in the loop or the lab
  enforces it. The code limit is lab-2's wallet policy
  `daily_budget_msat` 9,000 per UTC day (`wallet policy`). At the end of the
  2026-11-01 probes lab-2 had sent 9,000 msat in all.
- `settle` with a `window.from` after a payment judges that payment alone, but
  `counterparty_count` compares each signed bundle's whole-book count with the
  windowed book, so it reads NOT_CHECKED in a window.

A node pays only when its host has a wallet, in two parts, each run as
intangible on the lab host. Part A, Ethan's: `lab/bin/lab-wallet-create.sh N
[SATS]` creates lab-N's Lexe wallet in its payments directory through a
temporary, unjoined instance with no relay, sets a small automatic policy and
prints one funding invoice (none for SATS 0); lab-N is not touched. It reruns
only until the running lab-N opens the payment ledger the first run made (within
seconds); after that, fund through lab-N's console once `LAB_PAYMENTS` is set,
or stop the lab first. Part B:
`lab/bin/lab-wallet-setup.sh N` checks A's wallet is there and free and
installs the same lexe-wallet build for lab-N; it neither writes config.toml
nor restarts the node. Neither part reads, prints or copies the seed. Then,
while the lab is quiet, `lab/lab.sh stop` and `LAB_PAYMENTS="lab-1 lab-2
lab-3" lab/lab.sh start` restart the whole lab, so lab-1's new invite token
reaches the other two, and `start` writes `[payments] wallet = "lexe-wallet"`
for each named node; `lab-wallet-setup.sh N --balance` then shows lab-N's
balance. Every later `lab/lab.sh start` must keep `LAB_PAYMENTS`, which writes the
same section; `start` refuses, before any node starts, a config with a wallet
that `LAB_PAYMENTS` does not name.

`lab/lab.sh bundles` asks each node for its own terminal settlement records for
exchanges with each other node, from the capsules plugin's owner-only
`settlement-bundle?peer=<node id>` route. The answer is a signed Evidence
Bundle; a refusal (no settlement with that peer) is kept as the node's answer.
Then each node's `settlement_check` checks its own book against each
counterparty's bundle (`counterparty_count`, evidence-network/loop#243), into
`check.<node id>.<peer node id>.json` with the bundle's sha256. Everything
goes into `$LAB_DIR/bundles/` (dir 0700, files 0600), and a run replaces the
last one whole. The records carry each payment's preimage, so the files stay
on the host: never in findings, a pack or a message. Step 06 reads the check
files and publishes only the counts and each bundle's sha256.

### Step 07, the later ask

Stage 1 does not include step 07 either. `lab/lab.sh ask` runs `steps/07-ask` on
`population/live` into the same `findings/<date>-live/` as `07-ask/`, then checks the
finding contract over the whole directory. It asks each node for `checkpoints`,
`history_card/1` and an exchange request, through the reporter's console (the reporter's
own node through a second node's console, as `loop` does), and pays nothing. Run it after
`join`, `entries`, `commitments` and `loop`.

- Each answer is judged under the node's listed key, by the `answers_within` deadline of
  its newest signed commitment for that key: the one `commitments` wrote to
  `nodes/commitments/<node_id>.json`, or a join record under `lab/runs/` from an earlier
  pack, whichever was issued later.
- The step reads the sealed `evidence_request` and its outcome record from the asker's own
  ledger (`<data_dir>/ledger/capsules.jsonl` of the reporter's node, or of the second node
  when the reporter's node is asked).
- The exchange request is about the newest half the asker pushed to the asked node after an
  `exchange`, by a node started with `LAB_SHARE_AT_COMPLETION` (the halves `deal` reads), that
  the asked node's chain cites as a counterparty half. With no such half, the exchange ask
  reads NOT_CHECKED (it needs such a half).
- An exchange refusal for `policy_declined` or `not_authorized` reads NOT_CHECKED
  (Mesh-LLM/capsules-plugin#114). One for `coverage_unsatisfiable` (the node has no
  checkpoint covering the half yet) is checked for its key and left NOT_CHECKED
  (it needs a checkpoint covering the half).
- A refusal for `retention_expired` reads NOT_CHECKED (evidence-network/loop#60): it is right
  only after the node's retention commitment for the subject lapses, and no node publishes one.

`pack` takes `07-ask/` with the rest of the day's findings.

## 6. Pack and bring back

```sh
lab/lab.sh --dry-run pack       # lists every file it will take, and anything missing
lab/lab.sh pack
```

One tarball, `$LAB_DIR/out/loop-lab-<date>.tgz`, whose paths are this
repository's, so a maintainer extracts it at the repository root:

| Path in the tarball | From |
| --- | --- |
| `nodes/<node_id>.json` (three) | `entries` |
| `nodes/commitments/<node_id>.json` and `nodes/commitments/policies/<node_id>.json` (three each) | `commitments` |
| `population/live/population.json` | `entries` |
| `reporter/reporter.json` | `entries` |
| `findings/<date>-live/` | `loop` |
| `lab/runs/<date>/build.json` | `check` |
| `lab/runs/<date>/join/<name>/` | `join` (each node's commitment is `commitment.json` there) |
| `lab/runs/<date>/MANIFEST.json` | `pack`: the sha256 of every file above |

`pack` leaves out every `*.seed` and `*.pem` file, and stops if any file it
would take holds a PEM private key or the mesh invite token. It never takes
`$LAB_DIR/nodes`, `$LAB_DIR/controllers`, `$LAB_DIR/run` or `$LAB_DIR/logs`.
It prints the tarball's sha256.

From your laptop:

```sh
scp '<lab host>:<LAB_DIR>/out/loop-lab-<date>.tgz' .
sha256sum loop-lab-<date>.tgz        # the same as pack printed
```

Then send the maintainers the tarball and the paste-back list below.

### What to paste back

1. The tarball, and the sha256 `pack` printed.
2. The output of `lab/lab.sh check` (or `cat $LAB_DIR/build.json`), and the
   two binary sha256 values your build recorded when it was made.
3. The output of `lab/lab.sh status`, after `start` and again before `pack`
   (node ids, ports, the plugin keys each console lists; no token, no key).
4. The three `node_id`s and the reporter's `key_id`, as `entries` printed them.
5. The per-step lines `loop.sh` printed (`NN name RESULT`).
6. Anything that differed from this runbook, and any step you skipped.

All three nodes keep running after `pack`: the entries are for nodes that
answer. Stop them only if you have to (below).

## 7. Teardown and restart

**Stop:** `lab/lab.sh stop` sends each node SIGTERM and waits up to 30
seconds, so its plugin writes its shutdown checkpoint, then kills anything
left and closes the tmux session. It does not delete anything.

**Restart:** `lab/lab.sh start` again. Each node keeps its `HOME`, so it
keeps its mesh key and node id, its plugin key and its book: the `nodes/`
entries stay valid. lab-1 makes a new mesh with a new invite token, and the
other two join it. Run `status` and check the keys are listed again.
A node whose node id or key changed (its `HOME` was lost) needs a new
`nodes/` entry: run `entries` and `pack` again.

**Restart one node:** `kill -TERM $(cat $LAB_DIR/run/<name>.pid)`, wait for
it to exit, then `tmux new-window -d -t loop-lab -n <name> $LAB_DIR/run/<name>.sh`.
Restarting lab-1 makes a new invite token; restart lab-2 and lab-3 after it
(or `stop` and `start` all three).

**Remove the lab:** `lab/lab.sh stop`, then `rm -r $LAB_DIR` once the
tarball is safely back and merged. That deletes every node key, including
the reporter's; a new lab is new keys and new entries. Before removing a
node that is listed in `nodes/`, open the leave pull request described in
[nodes/README.md](nodes/README.md).

## A node will not announce its key

`curl -s http://127.0.0.1:<console>/api/plugin-keys` should list the node's
own key and its peers'. If a key is missing:

1. **The build.** Key announcement needs a mesh-llm build that has it
   (release 0.78.1 does not). `lab/lab.sh check` must pass.
2. **The plugin started?** Look in `$LAB_DIR/logs/<name>.log` for the
   capsules plugin starting, or failing to (read it on the host; it holds
   the invite token). `$LAB_DIR/nodes/<name>/config.toml` must name
   `CAPSULES_BIN`, and `.local/share/capsules/keys/node-key.pub.pem` must
   exist.
3. **Its own id.** `.local/share/capsules/self-peer-id` must exist. A plugin
   that the host started again, without being told its node's id, refuses
   every signed ask until the next mesh event (Mesh-LLM/capsules-plugin#48;
   fixed by mesh-llm #2620, which the build carries). Restarting that node's
   mesh-llm clears it.
4. **Its own console.** The launch script must set
   `CAPSULES_HOST_CONSOLE_URL` to the node's own console. Without it the
   plugin asks the default console and every answer reads
   `no_announced_key`.
5. **No key map.** `CAPSULES_PEER_KEYS` must be unset (the launch scripts
   unset it): keys come from the mesh only.
6. **Connected.** A node learns announced keys from peers it is connected
   to. `lab/lab.sh status` for each node: lab-2 and lab-3 must have joined
   lab-1's mesh (they wait for lab-1's model at start). If one lost the mesh,
   restart it; if lab-1 restarted, restart the other two after it.
7. **Not a client.** Every node must be `serve`, never `--client` (a client
   gets a new id at every start).
8. **Answers but no checkpoint.** An answer of `coverage_unsatisfiable`
   (recorded as `no_checkpoint_yet`) is not a key problem: the node has not
   cut its first checkpoint yet. Run `exchange` and wait 300 seconds.

## A real step 09 Result: four roles and an injected difference (`LAB_TWINS=1`)

Step 09 reads a Result the plugin sealed; it never makes one. The referee needs
four distinct nodes (the requester and both twins are out of its pool), so with
`LAB_TWINS=1` the lab gains `lab-4` (ports 9804, 9814, 9824) and these roles,
each overridable: requester `lab-3`, twin A `lab-1`, faulty twin B `lab-4`,
referee `lab-2`. Twin A, twin B and the referee serve the model, from the same
Hugging Face ref (`LAB_MODEL_REF`, a download, no spend): mesh-llm gives a local
GGUF path no identity hash, and the referee then refuses the pair as
`not_comparable` (`model_hash_unknown`). The ref the lab's model comes from is
`Qwen/Qwen2.5-0.5B-Instruct-GGUF@9217f5db79a29953eb74d5343926648285ec7e67/qwen2.5-0.5b-instruct-q4_k_m.gguf`
(sha256 `74a4da8c…`, the weights digest in the halves); `/v1/models` lists it as
`…:q4_k_m` (with the revision), but the name the hosts forward under is the one the peers advertise
(`Qwen/Qwen2.5-0.5B-Instruct-GGUF:q4_k_m`, no revision): `start` waits until every host lists every
other node's plugin key, waits `LAB_KEY_SETTLE` seconds (default 40: the plugin re-reads every 30 s with a 5 s timeout) for the plugins to read them (a push
sent earlier is refused `signature_unverified` and never retried), then saves the advertised name for
`exchange` and `twins`. `join` skips a node whose
`$LAB_DIR/join/<node>` exists (join.sh fails on a node that has joined).

**Lab-only fault injection.** Twin B's `config.toml` gets a per-model
`system_prompt` (`LAB_FAULT_SYSTEM_PROMPT`, default: reply with the single word PINEAPPLE) so its
answer differs from twin A's under the same model hash and weights digest. No
temperature, seed or sampler override. `twin-fault.json` records it and step 09
says "INJECTED DIFFERENCE" in the finding. `start` keeps the bracket that `twins` added
to `twin-fault.json` while the fault is unchanged; a changed fault starts the file over, so run `twins`
again. When the run copied this twin-fault.json and a held Result carries its bracket, step 09
judges that bracket: a Result an earlier twins run left in a ledger, signed under a lab node's
announced key, is named and set aside (evidence-network/loop#122); without it every held Result is judged. The requester gets the plugin's
`adjudicate_differing_twins` switch (off by default: it sends the twins' request
and both answers to the referee) and `CAPSULES_OPENAI_API_URL` for its own API.

Order: `stop` any running lab (nodes read their role at start; `start` refuses
to keep a node started without it), `LAB_TWINS=1 start`, `exchange`, `join`,
`LAB_TWINS=1 entries` (writes the four-node population under `$LAB_DIR/population-twins/`; `lab-4` stays out of `population/live` and `nodes/`), `LAB_TWINS=1 twins`
(asks the referee, sends one `x-mesh-twin-bracket` pair, supplies the pair's
text with `referee_supply_twin_texts`, waits for the Result), then `LAB_TWINS=1 dispute`.
