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
lab/lab.sh [--dry-run] check|tools|start|status|exchange|join|entries|loop|deal|settle|twins|dispute|pack|stop|all
```

`all` runs the sections below in order: `check`, `tools`, `start`,
`exchange`, a wait for each node's first checkpoint, `join`, `entries`,
`loop`, `pack`. It stops at the first command that fails: a build that is
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
| `$LAB_DIR/run/` | pid files, launch scripts and the mesh invite token (mode 0700) |
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
  LAB_DIR=$LAB_DIR ./loop.sh --stage 1 --population live --out findings/<date>-live
```

`LOOP_CONSOLE` is the reporter node's console: step 12 asks each other node
through it. A node can't be asked through its own console, so
`LOOP_SELF_NODE` names the reporter node and step 12 asks it through
`LOOP_SECOND_CONSOLE`, lab-1's console (lab-2's when lab-1 is the reporter).
Without a second console the reporter node's probe is NOT_CHECKED, not FAIL.
`lab/lab.sh loop` also sets `LOOP_NODE_CONSOLES` (`NAME=URL` per node, comma separated), each node's own
console, so step 12 reads each node's `doctor` for its controller binding.
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
or not readable by the user running the loop is NOT_CHECKED with
`data_dir` (or `book_dir`) `not set or unreadable`, never a FAIL.

What to expect from each step on the live population today, so each
result is read for what it is. Every step writes one finding, the worst
over the three nodes, and its `detail` lists each node:

- `01 seal`: each node seals one record with its own plugin key from its
  `data_dir`, and the second-language verifier checks Class 1 on the
  sealed bytes. PASS when every node's capsule verifies under that node's
  key; the detail then says the producer signature is outside the
  assertion. NOT_CHECKED for a node whose `data_dir` is unreadable or when
  a pinned tool is missing (run `lab/lab.sh tools`). A FAIL names the node
  and the check.
- `02 book`: for each node, the pinned capsulectl appends a capsule sealed
  with the node's key to the book under its `book_dir` and verifies it
  under that book's checkpoint key. NOT_CHECKED at best: the book's
  checkpoint carries no witness receipt on a lab node, and the pinned
  evidencebook has no receipt verifier. A FAIL names the node and the
  check.
- `03 advertise`: reads each node's commitment from this repository's
  `nodes/commitments/` and signs nothing for a live node. NOT_CHECKED
  until each node publishes a commitment it signed itself
  (Mesh-LLM/capsules-plugin#51).
- `12 join`: asks each listed node `checkpoints` and `history_card/1` from
  lab-3 and judges each answer under the key the node's host announces. The
  join, the binding and the signed commitment read NOT_CHECKED on the
  plugin's issues, so the step reads NOT_CHECKED at best. A FAIL names the
  node and the answer.

A FAIL is reported with the tarball as it is; do not work around it on the
host.

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
pays nothing. The live population names no rail, so the finding reads
NOT_CHECKED. `pack` takes `06-settle/` with the rest of the day's findings.

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
`…:q4_k_m`, and `start` saves that name for `twins`. `join` skips a node whose
`$LAB_DIR/join/<node>` exists (join.sh fails on a node that has joined).

**Lab-only fault injection.** Twin B's `config.toml` gets a per-model
`system_prompt` (`LAB_FAULT_SYSTEM_PROMPT`, default: reply with the single word PINEAPPLE) so its
answer differs from twin A's under the same model hash and weights digest. No
temperature, seed or sampler override. `twin-fault.json` records it and step 09
says "INJECTED DIFFERENCE" in the finding. The requester gets the plugin's
`adjudicate_differing_twins` switch (off by default: it sends the twins' request
and both answers to the referee) and `CAPSULES_OPENAI_API_URL` for its own API.

Order: `stop` any running lab (nodes read their role at start; `start` refuses
to keep a node started without it), `LAB_TWINS=1 start`, `exchange`, `join`,
`LAB_TWINS=1 entries` (adds `lab-4` to `population/live`), `LAB_TWINS=1 twins`
(asks the referee, sends one `x-mesh-twin-bracket` pair, supplies the pair's
text with `referee_supply_twin_texts`, waits for the Result), then `dispute`.
