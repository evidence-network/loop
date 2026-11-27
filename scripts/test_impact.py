#!/usr/bin/env python3
"""Which unit test modules a change can affect: the pre-push check runs those, CI runs them all (evidence-network/loop#489).

    python3 -B scripts/test_impact.py [--base REF] [--explain]     the modules, one per line, or ALL
    python3 -B scripts/test_impact.py --deps MODULE                 what one module is taken to depend on

A test module depends on every tracked file it names, and on what those files name in turn, as far as the code goes
(Python and shell; a string with whitespace or a line that only prints names a path for a person, not for the code):
  - a path written as a string ("scripts/x.py", "$ROOT/steps/finding.py") or as a pathlib chain (ROOT / "lab" / "lab.sh"),
    a file or a directory (a directory covers everything under it);
  - a quoted string that is a top-level entry ("steps", "versions.lock") or the name of a file under one of the code
    directories ("lab.sh", "05-deal");
  - a Python import whose name is a .py file in a code directory (`import check_nodes` with scripts/ on sys.path).
The files reached are read the same way when they are code (Python, shell, the extensionless steps); data files are
dependencies but are not read further.

A change selects each module whose dependencies hold a changed path (committed since the merge base with --base,
default origin/main, plus the working tree's changes). The answer is ALL, every module, when the base cannot be found,
when this script, scripts/unittest_parallel.py or versions.lock changes, or when a changed code file is a dependency of
no module: a path this reading cannot place runs everything rather than nothing. A change to data or documents that no
module names selects none. LOOP_TESTS=all in the environment also gives ALL.
"""
import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Directories whose files are code: read for further dependencies, and a changed file here that no module depends on
# selects every module.
CODE_DIRS = ("scripts", "steps", "lab", "site", "status", "ops", "tests", ".github")
CODE_TOP = ("loop.sh",)
# A change here reruns everything: the selection and the runner themselves, and the tool pins every step test reads.
ALWAYS_ALL = ("scripts/test_impact.py", "scripts/unittest_parallel.py", "versions.lock")

CHAIN = re.compile(r'"([^"\n]+)"(?:\s*/\s*"([^"\n]+)")+')
SEGMENT = re.compile(r'"([^"\n]+)"')
PATHLIKE = re.compile(r"[\w.@+-]+(?:/[\w.@+-]+)+")
QUOTED = re.compile(r"""["']([\w.@+-]+)["']""")


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


class Repo:
    def __init__(self, files):
        self.files = set(files)
        self._refs = {}
        self.dirs = {str(Path(f).parents[i]) for f in self.files for i in range(len(Path(f).parts) - 1)}
        self.top = {Path(f).parts[0] for f in self.files}
        self.by_name = {}
        for f in self.files:
            if is_code(f):
                self.by_name.setdefault(Path(f).name, set()).add(f)
        self.py_by_stem = {}
        for f in self.files:
            if f.endswith(".py") and is_code(f):
                self.py_by_stem.setdefault(Path(f).stem, set()).add(f)

    def resolve(self, path):
        """The tracked file or directory a written path names, or None."""
        path = path.strip().removeprefix("./")
        for cut in ("$ROOT/", "${ROOT}/", "$HERE/../", "$root/"):
            path = path.removeprefix(cut)
        path = path.rstrip("/")
        if path in self.files or path in self.dirs:
            return path
        # A path written from inside a directory still names that file: keep the longest tracked suffix.
        parts = Path(path).parts
        for i in range(1, len(parts)):
            tail = str(Path(*parts[i:]))
            if "/" in tail and (tail in self.files or tail in self.dirs):
                return tail
        return None

    def refs(self, rel):
        """What one tracked file names: tracked files and directories."""
        if rel not in self._refs:
            self._refs[rel] = self._read_refs(rel)
        return self._refs[rel]

    def _read_refs(self, rel):
        try:
            text = (ROOT / rel).read_text(errors="replace")
        except (OSError, UnicodeDecodeError):
            return set()
        if rel.endswith(".py") or text.startswith("#!") and "python" in text.split("\n", 1)[0]:
            strings, chains = py_strings(text)
            # A string with whitespace in it is a message or a command line meant for a person, not a path the code
            # opens; a path inside such a string is read only when the string is a command this code runs (argv).
            strings = [st for st in strings if not re.search(r"\s", st)]
            out = self.py_imports(text)
        else:
            # Shell, workflows and the steps: what the lines say outside their comments.
            # Lines that only print a message (echo, printf, die, warn, say, log) name paths for a person to read.
            talk = re.compile(r"^\s*(#|(echo|printf|die|warn|say|log|refuse|fail|note)\b)")
            body = "\n".join(line for line in text.splitlines() if not talk.match(line))
            strings = [body] + QUOTED.findall(body)
            chains = ["/".join(SEGMENT.findall(m.group(0))) for m in CHAIN.finditer(body)]
            out = set()
        for cand in chains:
            # ROOT / "scripts" (a sys.path entry) names no file; ROOT / "steps" / name is read as all of steps/ only
            # when the name is not written out, which the chain then stops at.
            if (r := self.resolve(cand)) and r not in CODE_DIRS:
                out.add(r)
            elif not r:
                # HERE / "x.py": a file named relative to a variable directory; any code file of that name.
                out |= self.by_name.get(Path(cand).name, set())
        for st in strings:
            for m in PATHLIKE.finditer(st):
                if (r := self.resolve(m.group(0))):
                    out.add(r)
            name = st.strip()
            # A bare code directory ("scripts" for sys.path) names no file; a data directory names all of its files.
            if name in self.top and name not in CODE_DIRS and name not in self.files:
                out.add(name)
            out |= self.by_name.get(name, set())
        out.discard(rel)
        return out

    def py_imports(self, text):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return set()
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module.split(".")[0])
        return {f for n in names for f in self.py_by_stem.get(n, ())}

    def deps(self, rel):
        """Everything one file depends on, following the code files it reaches."""
        seen, todo = {rel}, [rel]
        while todo:
            for r in self.refs(todo.pop()):
                if r not in seen:
                    seen.add(r)
                    if r in self.files and is_code(r):
                        todo.append(r)
        return seen


def py_strings(text):
    """A Python file's string constants outside docstrings, and its pathlib chains (ROOT / "a" / "b") joined."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [text], []
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docs.add(id(first.value))
    chains, in_chain = [], set()
    for n in ast.walk(tree):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div) and id(n) not in in_chain:
            segs, cur = [], n
            while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
                in_chain.add(id(cur))
                if isinstance(cur.right, ast.Constant) and isinstance(cur.right.value, str):
                    segs.insert(0, cur.right.value)
                    in_chain.add(id(cur.right))
                else:
                    segs = []
                cur = cur.left
            if isinstance(cur, ast.Constant) and isinstance(cur.value, str):
                segs.insert(0, cur.value)
                in_chain.add(id(cur))
            if segs:
                chains.append("/".join(segs))
    # A chain's segments count as the path they join ("data" / "day1" is data/day1, not all of data/).
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and id(n) not in docs and id(n) not in in_chain]
    return strings, chains


def is_code(rel):
    """A program or script: read for what it names. Fixtures and data under these directories are not."""
    if rel in CODE_TOP:
        return True
    if rel.split("/", 1)[0] not in CODE_DIRS or "/fixtures/" in rel:
        return False
    name = Path(rel).name
    # Workflows are read by tests (their text), never run by them: a dependency, not followed further.
    return name.endswith((".py", ".sh")) or ("." not in name and (ROOT / rel).is_file()
                                                                 and os.access(ROOT / rel, os.X_OK))


def covers(deps, path):
    return path in deps or any(path.startswith(d + "/") for d in deps)


def changed(base):
    names = set(git("diff", "--name-only", "--no-renames", f"{base}...HEAD").split())
    names |= set(git("diff", "--name-only", "--no-renames", "HEAD").split())
    names |= set(git("ls-files", "--others", "--exclude-standard").split())
    return sorted(names)


def modules(repo):
    return sorted(f for f in repo.files if re.fullmatch(r"tests/test[^/]*\.py", f))


def select(repo, paths):
    """(modules, why): ALL or the selected module names, and the reason for each."""
    if any(p in ALWAYS_ALL for p in paths):
        return "ALL", {p: "always runs everything" for p in paths if p in ALWAYS_ALL}
    deps = {m: repo.deps(m) for m in modules(repo)}
    picked, why = set(), {}
    for p in paths:
        hit = [m for m, d in deps.items() if covers(d, p)]
        if not hit and is_code(p):
            return "ALL", {p: "a code file no test module depends on"}
        for m in hit:
            picked.add(m)
            why.setdefault(Path(m).stem, p)
    return sorted(Path(m).stem for m in picked), why


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--explain", action="store_true", help="say on stderr why each module was selected")
    ap.add_argument("--deps", metavar="MODULE", help="print what MODULE (tests/test_x.py) depends on")
    a = ap.parse_args(argv)
    repo = Repo(git("ls-files").split())
    if a.deps:
        print("\n".join(sorted(repo.deps(a.deps))))
        return 0
    if os.environ.get("LOOP_TESTS") == "all":
        print("ALL")
        return 0
    try:
        base = git("merge-base", a.base, "HEAD").strip()
        paths = changed(base)
    except subprocess.CalledProcessError:
        print("test_impact: no merge base with", a.base, "; running every module", file=sys.stderr)
        print("ALL")
        return 0
    picked, why = select(repo, paths)
    if a.explain:
        for k, v in sorted(why.items()):
            print(f"test_impact: {k}: {v}", file=sys.stderr)
    print("ALL" if picked == "ALL" else "\n".join(picked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
