#!/usr/bin/env python3
"""Build the loop status page and its badge from loop findings and versions.lock.

Usage: status_page.py [FINDINGS] [--lock FILE] [--out DIR]
       (defaults: findings, versions.lock, _site/status)

FINDINGS holds loop.sh runs, one directory each: <YYYY-MM-DD>-rehearsal/ or
<YYYY-MM-DD>-live/, with one <NN>-<name>/finding.json per step (steps/README.md).
The page reads the newest run of each population and shows them side by side,
one column each. It never adds a rehearsal result to a live one.

Writes DIR/index.html (twelve rows, one per step of the loop), DIR/badge.svg
(the worst row of each population) and copies the two runs to DIR/findings/
and the lock to DIR/versions.lock byte for byte, so each link opens the file
that produced the cell.

A cell is green only when its step's finding is PASS and passes
steps/finding.py's check: every proof file present, each matching its sha256.
FAIL is red, and so is a finding the check refuses (MALFORMED). NOT_CHECKED
shows each {what, why, issue} the finding gives. A step with no finding reads
NOT_CHECKED with the issue that owns the step. The page's own words say
"checked", "verified" or "witnessed" only in a PASS cell; text a finding
supplies is shown as a quote of that finding. A green cell whose proof is not
in DIR fails the build, and nothing is written. Standard library only.
"""
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402

FINDINGS = "findings"
LOCK = "versions.lock"
GREEN, RED, NOT_CHECKED = "green", "red", "NOT_CHECKED"
ORDER = (RED, NOT_CHECKED, GREEN)  # worst first
POPULATIONS = ("rehearsal", "live")
RUN_DIR = re.compile(r"^(\d{4}-\d{2}-\d{2})-(rehearsal|live)$")

NODE_SIDE = ("capsules-plugin #49", "https://github.com/Mesh-LLM/capsules-plugin/issues/49")
HARNESS = ("loop #24", "https://github.com/evidence-network/loop/issues/24")


@dataclass(frozen=True)
class Step:
    n: int
    name: str
    asserts: str  # the page's own words: never "checked", "verified" or "witnessed"
    owner: tuple  # (label, url) of the issue that owns the step
    milestone: str | None  # when a step script for it is due


STEPS = (
    Step(1, "Seal", "A node seals a record at its effect boundary, and a second implementation in another language "
                    "accepts it.", NODE_SIDE, "M1"),
    Step(2, "Book", "The record sits in the node's book at a position, a checkpoint covers it, and the checkpoint's "
                    "witness receipt holds.", NODE_SIDE, "M1"),
    Step(3, "Advertise", "The node publishes a signed commitment (checkpoint, disclosure policy digest, predicate "
                         "classes, how to ask) and it is fetched back intact. It carries no counts.", NODE_SIDE, "M1"),
    Step(4, "Admit", "Before an exchange, one node reads the other's history, decides GO, ASK, LIMIT or NO, and seals "
                     "the decision.", NODE_SIDE, "M3"),
    Step(5, "Deal and push", "Both sides seal halves that cite each other and push them at completion; the receiver "
                             "seals a record citing them.", NODE_SIDE, "M2"),
    Step(6, "Settle", "A settlement object for the exchange exists on the rail, with finality.", NODE_SIDE, "M2"),
    Step(7, "Ask", "A request gets a signed artifact or a signed refusal, or its absence is recorded.", NODE_SIDE, "M2"),
    Step(8, "Verify", "An outside party reproduces every finding offline from the bundle, with no account and no "
                      "network call.", HARNESS, None),
    Step(9, "Dispute", "One party disputes, a referee seals a Result, and the close reads AGREED, CONTESTED or "
                       "UNILATERAL.", NODE_SIDE, "M3"),
    Step(10, "Judge", "A pack runs over the window, and its sealed Result cites every record it read.", HARNESS, "M3"),
    Step(11, "Report", "The report renders from the bundle only, its digest is sealed, and every number has a "
                       "denominator.", HARNESS, "M3"),
    Step(12, "Join", "A new node joins with one command on the pinned release, and a probe asks its own responder "
                     "through the public path.", NODE_SIDE, "M1"),
)

# Rows whose place on the page is still to be settled. They are built and shown,
# marked held. Setting DROP_HELD to True drops them; nothing else changes.
HELD_FOR_FLIP = frozenset({4, 5, 6, 9})
DROP_HELD = False
HELD_NOTE = "Held: whether this row stays on the page is still to be settled."

# A PASS finding names what lies outside its assertion in `detail`, one sentence
# per gap, in the form steps/finding.py checks; gaps() reads them back.

# The five numbers. A measured one reads the newest run of its population:
#   start = run.json `started`, which loop.sh writes before the run's first step;
#   event = the `finished` of the marker step's finding, when that finding is a
#           valid PASS.
# minutes = event - start. A number with no marker step, or whose marker step has
# no valid PASS, reads "not measured yet (<milestone>)". A marker step that is a
# valid PASS in a run with no usable start time says exactly why (NO_START and
# the rest of not_measured()). Never 0.
@dataclass(frozen=True)
class Number:
    key: str
    label: str
    unit: str  # the unit, or what it is out of
    milestone: str
    step: int | None  # the step whose finding marks the event; None: no finding carries it yet
    source: str


NUMBERS = (
    Number("first-sealed", "Minutes to the first sealed record", "minutes from the run's start", "M1", 1,
           "step 01 (Seal) finding's finished, minus run.json started"),
    Number("first-answered", "Minutes to the first answered request", "minutes from the run's start", "M1", 12,
           "step 12 (Join) finding's finished (its probe asks the node's own responder), minus run.json started"),
    Number("first-countersigned", "Minutes to the first countersigned report", "minutes from the run's start", "M3",
           11, "step 11 (Report) finding's finished, minus run.json started; a placeholder until M3"),
    Number("limited", "Decisions limited or refused on evidence", "of all admission decisions", "M3", None,
           "step 04 (Admit): no finding carries the decision counts yet"),
    Number("filtered", "Filtered volume beside raw volume", "filtered records of raw records", "M2", None,
           "no finding carries either volume yet; the raw figure is never shown without the filtered one"),
)

e = html.escape


class StatusError(Exception):
    def __init__(self, problems):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class Cell:
    """One step in one population's run."""

    population: str
    state: str
    label: str  # PASS, FAIL, MALFORMED or NOT_CHECKED
    why: str  # HTML, the page's own words
    said: list = field(default_factory=list)  # (what, text): text the finding supplies, shown as a quote
    not_checked: list = field(default_factory=list)  # the finding's {what, why, issue} entries
    gaps: list = field(default_factory=list)  # Gap: what a PASS finding says lies outside its assertion
    proofs: list = field(default_factory=list)  # (label, href relative to DIR)
    links: list = field(default_factory=list)  # (label, href): the finding itself
    pinned: list = field(default_factory=list)  # (name, ref)
    times: tuple = ()  # (started, finished)

    def __post_init__(self):
        if self.state not in ORDER:
            raise ValueError(f"unknown state {self.state!r}")
        if self.state == GREEN and not (self.label == "PASS" and self.proofs):
            raise ValueError("a green cell is a PASS with its proof files")


@dataclass(frozen=True)
class Gap:
    text: str  # the sentence after "Outside this step's assertion: ", up to its issue
    issue: str  # owner/repo#N
    url: str


def gaps(detail):
    """Each gap sentence in a finding's detail, as steps/finding.py defines and checks it."""
    return [Gap(text, issue, "https://github.com/{}/issues/{}".format(*issue.split("#")))
            for text, issue in finding.gaps(str(detail or ""))]


@dataclass
class Row:
    step: Step
    cells: dict  # population -> Cell

    @property
    def held(self):
        return self.step.n in HELD_FOR_FLIP


@dataclass
class Run:
    population: str
    path: Path | None
    findings: dict = field(default_factory=dict)  # step number -> (step dir, record, problems)
    started: str | None = None  # run.json `started`, when run.json is valid for this population

    @property
    def name(self):
        return self.path.name if self.path else None


@dataclass
class Lock:
    sha256: str
    pins: list  # (name, repository, ref)


def latest_runs(findings_dir):
    """{population: the newest <date>-<population> directory under findings_dir, or None}."""
    found = {p: None for p in POPULATIONS}
    root = Path(findings_dir)
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        m = RUN_DIR.match(d.name)
        if m and d.is_dir():
            found[m.group(2)] = d  # sorted by date, so the last one wins
    return found


def read_run(population, path):
    """Each step's finding in one run: (step dir, record or None, the check's problems)."""
    run = Run(population, path)
    if path is None:
        return run
    run.started = run_start(path, population)
    by_step = {}
    for d in sorted(p for p in path.iterdir() if p.is_dir()):
        m = finding.STEP_DIR.match(d.name)
        if m:
            by_step.setdefault(int(m.group(1)), []).append(d)
    for n, dirs in by_step.items():
        if len(dirs) > 1:
            run.findings[n] = (dirs[0], None, [f"more than one directory for step {n:02d}: "
                                               + ", ".join(d.name for d in dirs)])
            continue
        d = dirs[0]
        try:
            r = json.loads((d / "finding.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            run.findings[n] = (d, None, [f"{d.name}: no readable finding.json ({err})"])
            continue
        if not isinstance(r, dict):
            run.findings[n] = (d, None, [f"{d.name}: finding.json is not an object"])
            continue
        try:
            problems = finding.check_record(d, r)
        except (AttributeError, KeyError, TypeError) as err:
            problems = [f"{d.name}: finding.json is malformed ({err!r})"]
        if not problems and r["population"] != population:
            problems = [f"{d.name}: population is {r['population']}, this run is {population}"]
        run.findings[n] = (d, r, problems)
    return run


def run_start(path, population):
    """run.json's `started` (loop.sh), or None when it is missing, unreadable or for another population."""
    try:
        r = json.loads((Path(path) / "run.json").read_text(encoding="utf-8"))
        stamp(r["started"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return r["started"] if r.get("population") == population else None


def href(run, d, name):
    return f"{FINDINGS}/{run.name}/{d.name}/{name}"


def cell(step, run):
    p = run.population
    if run.path is None:
        return Cell(p, NOT_CHECKED, NOT_CHECKED, f"No {p} run yet.")
    if step.n not in run.findings:
        due = f" A step script for it is due at {step.milestone}." if step.milestone else ""
        return Cell(p, NOT_CHECKED, NOT_CHECKED,
                    f"No finding for this step in {code(run.name)}.{due} The row names the issue that owns it.")
    d, r, problems = run.findings[step.n]
    links = [(f"{d.name}/finding.json", href(run, d, "finding.json"))] if (d / "finding.json").is_file() else []
    if problems:
        return Cell(p, RED, "MALFORMED",
                    f"The finding in {code(run.name)} does not pass {code('steps/finding.py check')}, so its result "
                    "counts for nothing.", said=[("the check", x) for x in problems], links=links)
    kw = dict(
        said=[("the step asserts", r["asserts"])] + ([("detail", r["detail"])] if r["detail"] else []),
        proofs=[(x["path"], href(run, d, x["path"])) for x in r["proof"]],
        links=links,
        pinned=[(name, pin["ref"]) for name, pin in sorted(r["pinned"].items())],
        times=(r["started"], r["finished"]),
    )
    if r["result"] == "PASS":
        return Cell(p, GREEN, "PASS", f"PASS in {code(run.name)}: the finding says its assertion was checked on "
                                      f"the proof bytes, and each proof file is here and matches its sha256.",
                    gaps=gaps(r["detail"]), **kw)
    if r["result"] == "FAIL":
        return Cell(p, RED, "FAIL", f"FAIL in {code(run.name)}: the assertion did not hold.", **kw)
    return Cell(p, NOT_CHECKED, NOT_CHECKED, f"{code(NOT_CHECKED)} in {code(run.name)}: the finding lists what "
                                             "could not be established, why, and the issue that owns it.",
                not_checked=list(r["not_checked"]), **kw)


def shown_steps():
    return [s for s in STEPS if not (DROP_HELD and s.n in HELD_FOR_FLIP)]


def rows(runs):
    return [Row(s, {p: cell(s, runs[p]) for p in POPULATIONS}) for s in shown_steps()]


def worst(rs, population):
    return min((r.cells[population].state for r in rs), key=ORDER.index)


def stamp(text):
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


NO_START = "not measured: this run did not record its start time (run.json)"


def passed(run, step):
    """`step`'s finding in run, when it is a valid PASS; else None."""
    if step not in run.findings:
        return None
    _, r, problems = run.findings[step]
    return r if r is not None and not problems and r["result"] == "PASS" else None


def not_measured(run, step, milestone):
    """Why a minutes number has no value: its milestone while the marker step has no PASS, else the start's fault."""
    if run.path is None or passed(run, step) is None:
        return f"not measured yet ({milestone})"
    if not (run.path / "run.json").is_file():
        return NO_START
    if run.started is None:
        return "not measured: this run's run.json gives no start time for this population"
    return "not measured: this run's run.json start is after the step finished"


def minutes(run, step):
    """Minutes from run.json `started` to `step`'s valid PASS `finished`, or None."""
    r = passed(run, step)
    if run.started is None or r is None:
        return None
    try:
        m = (stamp(r["finished"]) - stamp(run.started)).total_seconds() / 60
    except (TypeError, ValueError):
        return None
    return m if m >= 0 else None


def volume(raw, filtered, milestone="M2"):
    """Filtered beside raw, or the placeholder. Never the raw figure alone."""
    if raw is None or filtered is None:
        return f"not measured yet ({milestone})"
    return f"{filtered} filtered of {raw} raw records"


def decided(limited, total, milestone="M3"):
    if limited is None or total is None:
        return f"not measured yet ({milestone})"
    return f"{limited} of {total} decisions"


def number_value(number, run):
    """(text, measured?) for one number in one population's run."""
    if number.key == "filtered":
        return volume(None, None, number.milestone), False
    if number.key == "limited":
        return decided(None, None, number.milestone), False
    m = minutes(run, number.step) if run.path else None
    if m is None:
        return not_measured(run, number.step, number.milestone), False
    return duration(m), True


def duration(m):
    """Minutes as the page states them: "<1 s" under one second, whole seconds under one minute, else minutes
    to one decimal."""
    if m * 60 < 1:
        return "<1 s"
    seconds = round(m * 60)
    return f"{seconds} s" if seconds < 60 else f"{m:.1f} minutes"


def read_lock(path):
    """The pins in versions.lock, or None when there is none. One `<name> <repository> <ref>` per line."""
    path = Path(path)
    if not path.is_file():
        return None
    data = path.read_bytes()
    pins, problems = [], []
    for i, line in enumerate(data.decode("utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            problems.append(f"{path.name}:{i}: not <name> <repository> <ref>")
            continue
        pins.append(tuple(parts))
    if problems:
        raise StatusError(problems)
    return Lock(hashlib.sha256(data).hexdigest(), pins)


def code(text):
    return f"<code>{e(str(text))}</code>"


def link(path, label):
    return f'<a href="{e(path)}">{e(label)}</a>'


def check_rows(rs, out):
    """Every green cell links its proof, and every linked file is in out."""
    problems = []
    for r in rs:
        for c in r.cells.values():
            if c.state == GREEN and not c.proofs:
                problems.append(f"step {r.step.n} {c.population}: green with no proof")
            for _, path in c.proofs + c.links:
                if not (Path(out) / path).is_file():
                    problems.append(f"step {r.step.n} {c.population}: {path} is not in the page's files")
    if problems:
        raise StatusError(problems)


BADGE_COLOURS = {GREEN: "#2e7d32", RED: "#c62828", NOT_CHECKED: "#6b6b66"}


def counts(rs, population):
    """A population's rows counted by state: "4 PASS · 0 FAIL · 8 not checked". FAIL counts MALFORMED too."""
    states = [r.cells[population].state for r in rs]
    return f"{states.count(GREEN)} PASS · {states.count(RED)} FAIL · {states.count(NOT_CHECKED)} not checked"


def badge_label(rs):
    """The badge's words, also its alt text: each population's counts."""
    return ", ".join(f"{p} {counts(rs, p)}" for p in POPULATIONS)


def badge(rs):
    """A flat SVG badge: "loop" beside one row per population, "<population> <counts>", each row coloured by that
    population's worst row. Two rows keep it narrow enough for a phone."""
    rows = [(f"{p} {counts(rs, p)}", BADGE_COLOURS[worst(rs, p)]) for p in POPULATIONS]
    head = 10 + 7 * len("loop")
    w = max(10 + 7 * len(text) for text, _ in rows)
    height = 20 * len(rows)
    rects = [f'<rect x="0" width="{head}" height="{height}" fill="#3d3d3a"/>']
    texts = [f'<text x="{head / 2}" y="{height / 2 + 4}">loop</text>']
    for i, (text, colour) in enumerate(rows):
        rects.append(f'<rect x="{head}" y="{20 * i}" width="{w}" height="20" fill="{colour}"/>')
        texts.append(f'<text x="{head + w / 2}" y="{20 * i + 14}">{e(text)}</text>')
    label = e(badge_label(rs))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{head + w}" height="{height}" role="img" '
            f'aria-label="loop: {label}"><title>loop: {label} (each colour is the worst row of its population)</title>'
            f"{''.join(rects)}"
            f'<g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11" text-anchor="middle">'
            f"{''.join(texts)}</g></svg>\n")


def issue_html(issue):
    issue = str(issue)
    return link(issue, issue) if re.match(r"^https://", issue) else code(issue)


def cell_html(c):
    parts = [f'<p class="label"><code>{e(c.label)}</code></p>', f"<p>{c.why}</p>"]
    for g in c.gaps:
        parts.append(f'<p class="gap"><strong>gap</strong>, outside the assertion: <q class="said">{e(g.text)}</q> '
                     f"issue: {link(g.url, g.issue)}</p>")
    if c.not_checked:
        items = "".join(f'<li>what: <q class="said">{e(str(i["what"]))}</q>; why: <q class="said">{e(str(i["why"]))}'
                        f"</q>; issue: {issue_html(i['issue'])}</li>" for i in c.not_checked)
        parts.append(f'<ul class="nc">{items}</ul>')
    if c.said:
        parts.append("<dl>" + "".join(f'<dt>{e(what)}</dt><dd><q class="said">{e(str(text))}</q></dd>'
                                      for what, text in c.said) + "</dl>")
    links = []
    if c.proofs:
        links.append("<dt>proof</dt><dd>" + " · ".join(link(h, label) for label, h in c.proofs) + "</dd>")
    if c.links:
        links.append("<dt>finding</dt><dd>" + " · ".join(link(h, label) for label, h in c.links) + "</dd>")
    if c.pinned:
        links.append("<dt>pinned</dt><dd>" + ", ".join(f"{code(n)} {code(ref)}" for n, ref in c.pinned) + "</dd>")
    if c.times:
        links.append(f"<dt>ran</dt><dd>{code(c.times[0])} to {code(c.times[1])}</dd>")
    if links:
        parts.append(f"<dl>{''.join(links)}</dl>")
    return f'<td class="cell s-{e(c.state)}" data-population="{e(c.population)}">{"".join(parts)}</td>'


def row_html(r):
    s = r.step
    held = f'<p class="note held">{e(HELD_NOTE)}</p>' if r.held else ""
    due = f" · due {e(s.milestone)}" if s.milestone else ""
    return (f'<tr class="row" id="step-{s.n:02d}"{" data-held" if r.held else ""}>'
            f'<th scope="row"><code>{s.n:02d}</code> {e(s.name)}</th>'
            f'<td><p>{e(s.asserts)}</p>{held}<p class="note">owner: <a href="{e(s.owner[1])}">{e(s.owner[0])}</a>'
            f"{due}</p></td>"
            + "".join(cell_html(r.cells[p]) for p in POPULATIONS) + "</tr>")


def numbers_html(runs):
    out = []
    for nb in NUMBERS:
        cells = []
        for p in POPULATIONS:
            text, measured = number_value(nb, runs[p])
            cells.append(f'<td class="{"measured" if measured else "placeholder"}" data-population="{p}">{e(text)}</td>')
        out.append(f'<tr id="n-{nb.key}"><th scope="row">{e(nb.label)}</th><td>{e(nb.unit)}</td>{"".join(cells)}'
                   f"<td>{e(nb.source)}</td></tr>")
    return "".join(out)


def pins_html(lock):
    if lock is None:
        return f"<p>{code(NOT_CHECKED)}: {LOCK} not present.</p>"
    items = "".join(f"<tr><td>{code(a)}</td><td>{code(b)}</td><td>{code(c)}</td></tr>" for a, b, c in lock.pins)
    return (f"<p>{link(LOCK, LOCK)} {code('sha256=' + lock.sha256)}. Each finding names the pins it ran under; "
            f"{code('steps/finding.py check')} refuses one that is not the lock's entry.</p>"
            '<div class="scroll"><table><thead><tr><th scope="col">pin</th><th scope="col">repository</th>'
            f'<th scope="col">ref</th></tr></thead><tbody>{items}</tbody></table></div>')


def runs_html(runs):
    items = []
    for p in POPULATIONS:
        run = runs[p]
        items.append(f"<li>{e(p)}: " + (link(f"{FINDINGS}/{run.name}/", run.name) if run.path else "no run yet") + "</li>")
    return "<ul>" + "".join(items) + "</ul>"


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Loop status</title>
<style>
:root {{ --bg: #fdfdfb; --fg: #1d1d1b; --muted: #5c5c57; --line: #d9d9d2; --code: #f0f0ea; --accent: #2b5d8a;
        --s-green: #e3efe3; --s-red: #f3e6e6; --s-not: #eeeee9; --gap: #a86a00; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #161615; --fg: #e8e8e3; --muted: #a3a39c; --line: #3a3a36; --code: #262624; --accent: #8db8e0;
          --s-green: #1f3021; --s-red: #3a2222; --s-not: #262624; --gap: #e0a640; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.55 system-ui, sans-serif; }}
main {{ max-width: 80rem; margin: 0 auto; padding: 1.5rem 16px 4rem; }}
h1 {{ font-size: 1.7rem; margin: 0 0 .5rem; }}
h2 {{ font-size: 1.3rem; margin: 2.5rem 0 .5rem; border-bottom: 1px solid var(--line); padding-bottom: .3rem; }}
p {{ max-width: 46rem; }}
a {{ color: var(--accent); }}
img {{ max-width: 100%; height: auto; }}
code {{ background: var(--code); padding: 0 .25em; border-radius: 3px; font-size: .9em; overflow-wrap: anywhere; }}
q.said {{ font-style: italic; overflow-wrap: anywhere; }}
.note {{ color: var(--muted); font-size: .9rem; }}
.scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: .9rem; }}
th, td {{ border: 1px solid var(--line); padding: .5rem; vertical-align: top; text-align: left; }}
td.s-green {{ background: var(--s-green); }}
td.s-red {{ background: var(--s-red); }}
td.s-NOT_CHECKED {{ background: var(--s-not); }}
td.placeholder {{ color: var(--muted); }}
dl {{ margin: .3rem 0; display: grid; grid-template-columns: auto 1fr; gap: .1rem .6rem; }}
dt {{ color: var(--muted); }} dd {{ margin: 0; }}
td p, td ul {{ margin: .2rem 0; }}
p.gap {{ border-left: 3px solid var(--gap); padding-left: .5rem; overflow-wrap: anywhere; }}
</style>
</head>
<body>
<main>
<h1>Loop status</h1>
<p><img src="badge.svg" alt="loop: {badge_alt}"></p>
<p>The loop is the network's test harness: one step per fact, from sealing a record to a new node joining. Each
step writes a finding with its proof files. Each row below is one step; the two columns are the newest rehearsal
run (demo nodes and test keys) and the newest live run (the lab nodes). They are shown side by side and never
added together.</p>
<p>A cell is <code>PASS</code> (green) only when the step's finding says PASS and its proof files are here, each
matching its sha256. <code>FAIL</code> or a finding that does not pass the check (<code>MALFORMED</code>) is red.
<code>NOT_CHECKED</code> means the step could not establish its fact; the cell lists what, why and the owning issue.
A <code>PASS</code> cell lists each gap its finding names: something outside the step's assertion, with its issue.
Italic text is quoted from the finding. The badge counts each population's rows by state; its colour is the worst row. Built from the findings
and {lock} only.</p>
<p>Runs read:</p>
{runs}

<h2 id="steps">The steps</h2>
<div class="scroll"><table><thead><tr><th scope="col">Step</th><th scope="col">Asserts</th>
<th scope="col">Rehearsal</th><th scope="col">Live</th></tr></thead>
<tbody>{rows}</tbody></table></div>

<h2 id="numbers">Five numbers</h2>
<p>Each number is per population, from that population's newest run only. A minutes figure runs from the run's
start (its <code>run.json</code>, written before the first step) to the end of the step that marks the event. A
number that cannot be measured yet says so, with the milestone it waits for; it is never shown as 0.</p>
<div class="scroll"><table><thead><tr><th scope="col">Number</th><th scope="col">Unit</th>
<th scope="col">Rehearsal</th><th scope="col">Live</th><th scope="col">From</th></tr></thead>
<tbody>{numbers}</tbody></table></div>

<h2 id="pins">Pins</h2>
{pins}
</main>
</body>
</html>
"""


def render(rs, runs, lock):
    return PAGE.format(badge_alt=e(badge_label(rs)), lock=e(LOCK),
                       runs=runs_html(runs), rows="".join(row_html(r) for r in rs), numbers=numbers_html(runs),
                       pins=pins_html(lock))


def build(findings_dir, lock_path, out):
    lock = read_lock(lock_path)
    runs = {p: read_run(p, d) for p, d in latest_runs(findings_dir).items()}
    rs = rows(runs)
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for run in runs.values():
        if run.path:
            shutil.copytree(run.path, out / FINDINGS / run.name)
    if lock is not None:
        shutil.copyfile(lock_path, out / LOCK)
    try:
        check_rows(rs, out)
    except StatusError:
        shutil.rmtree(out)
        raise
    (out / "index.html").write_text(render(rs, runs, lock), encoding="utf-8")
    (out / "badge.svg").write_text(badge(rs), encoding="utf-8")
    return rs


def main(argv):
    args = argv[1:]
    opts = {"--out": "_site/status", "--lock": LOCK}
    for flag in opts:
        if flag in args:
            i = args.index(flag)
            opts[flag] = args[i + 1]
            del args[i:i + 2]
    findings_dir = Path(args[0] if args else FINDINGS)
    try:
        rs = build(findings_dir, opts["--lock"], opts["--out"])
    except StatusError as err:
        for p in err.problems:
            print(p, file=sys.stderr)
        print(f"{findings_dir}: the status page was not built", file=sys.stderr)
        return 1
    print(f"{opts['--out']}/index.html: " + ", ".join(f"{p} worst row {worst(rs, p)}" for p in POPULATIONS))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
