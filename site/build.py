#!/usr/bin/env python3
"""Build the stats site from the scrubbed daily set and the loop findings.

Usage: build.py [DATA] [--findings DIR] [--lock FILE] [--out DIR]
       (defaults: stats/data/daily, findings, versions.lock, _site)

Writes DIR/stats/index.html, a static page (no scripts, inline SVG, no
external assets), served at <pages root>/stats/:
  - the twelve-step loop as a path, one per population (rehearsal and live,
    side by side, never added together), each step coloured by its state;
  - the five numbers, per population;
  - a timeline per node: answered, refused, absent or missed, per question
    per day, and the day it left;
  - the explainer, and each day in detail.
The loop's states and numbers are status/status_page.py's: this module calls
its functions and builds the status page itself into DIR/stats/status/. The
day files are read by site/stats_model.py and copied to DIR/stats/data/daily/.
A day file it cannot read fails the build (exit 1) and nothing is written.

Every count reads "N of M ...". No score, rank or total per node. Nothing
links under reports/: the reporter's raw files are not part of this site.
Standard library only.
"""
import html
import shutil
import sys
from pathlib import Path
from string import Template

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "status"))
import stats_model  # noqa: E402
import status_page  # noqa: E402

SITE = "stats"
DATA = "data/daily"
STATUS = "status"
WORDS = {1: "once", 2: "twice"}
LABELS = {status_page.GREEN: "PASS", status_page.RED: "FAIL or MALFORMED",
          status_page.NOT_CHECKED: status_page.NOT_CHECKED}
# A day's state of one question, in the timeline's words.
KIND = {"verified": "answered", "no_checkpoint_yet": "answered", "no_announced_key": "answered",
        "not_evidence": "answered", "refusal": "refused", "absence": "absent"}
SAYS = {"verified": "signed answer", "no_checkpoint_yet": "signed: no checkpoint yet",
        "no_announced_key": "a reply, no key announced", "not_evidence": "a reply that is not evidence",
        "refusal": "signed refusal", "absence": "nothing came back"}

e = html.escape


def count(n, m, what="listed node"):
    """The only way the page states a count: always out of something."""
    return f'<span class="count">{n} of {m} {what}{"" if m == 1 else "s"}</span>'


def code(text, cls=None):
    return f'<code{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{e(str(text))}</code>'


def outcome(name):
    return code(name, f"outcome o-{name}")


def short(hex_id):
    return f'<code title="{e(hex_id)}">{e(hex_id[:12])}…</code>'


def when(stamp):
    return f'<time datetime="{e(stamp)}">{e(stamp)}</time>'


# The loop

def run_minutes(run):
    """Minutes from run.json `started` to the last finding's `finished`: the run's length, or None."""
    if run.started is None:
        return None
    ends = [r["finished"] for _, r, problems in run.findings.values() if r is not None and not problems]
    try:
        m = max((status_page.stamp(t) - status_page.stamp(run.started)).total_seconds() / 60 for t in ends)
    except (TypeError, ValueError):
        return None
    return m if m >= 0 else None


def path_svg(rs, population):
    """The steps as one path, left to right then back, each a circle in its state, linking to its status row."""
    per_row, gap, r = 6, 58, 15
    spots = []
    for i, row in enumerate(rs):
        line, col = divmod(i, per_row)
        x = 34 + gap * (col if line % 2 == 0 else per_row - 1 - col)
        spots.append((row, x, 46 + 92 * line))
    height = 46 + 92 * ((len(rs) - 1) // per_row) + 46
    width = 34 * 2 + gap * (per_row - 1)
    track = " ".join(f"{'M' if i == 0 else 'L'}{x} {y}" for i, (_, x, y) in enumerate(spots))
    back = f"M{spots[-1][1]} {spots[-1][2]} L{spots[0][1] - 26} {spots[-1][2]} L{spots[0][1] - 26} {spots[0][2]} " \
           f"L{spots[0][1]} {spots[0][2]}" if len(spots) > per_row else ""
    steps = []
    for row, x, y in spots:
        c = row.cells[population]
        s = row.step
        tip = f"{s.n:02d} {s.name}, {population}: {c.label}" + (f". {status_page.HELD_NOTE}" if row.held else "")
        steps.append(
            f'<a href="{STATUS}/#step-{s.n:02d}"><g class="step s-{e(c.state)}{" held" if row.held else ""}" '
            f'data-step="{s.n}" data-label="{e(c.label)}"><title>{e(tip)}</title>'
            f'<circle cx="{x}" cy="{y}" r="{r}"/>'
            f'<text class="code n" x="{x}" y="{y + 4}">{s.n:02d}</text>'
            f'<text class="name" x="{x}" y="{y + r + 13}">{e(s.name)}</text></g></a>')
    states = [row.cells[population].state for row in rs]
    label = (f"The loop's steps in the {population} run: "
             + "; ".join(f"{LABELS[st]} {states.count(st)} of {len(rs)}" for st in status_page.ORDER))
    return (f'<svg class="path" viewBox="0 0 {width} {height}" role="img" aria-label="{e(label)}">'
            f'<path class="track" d="{track}"/>' + (f'<path class="track back" d="{back}"/>' if back else "")
            + "".join(steps) + "</svg>")


def tally(rs, population):
    states = [row.cells[population].state for row in rs]
    return "; ".join(f"{code(LABELS[st])} {count(states.count(st), len(rs), 'step')}" for st in status_page.ORDER)


def loop_section(rs, runs):
    figures = []
    for p in status_page.POPULATIONS:
        run = runs[p]
        source = (f"the newest run, {code(run.name)}" if run.path else "no run yet")
        held = [row.step for row in rs if row.held]
        note = ""
        if held:
            note = (f'<p class="note">Dashed: {", ".join(code(f"{s.n:02d}") for s in held)}. '
                    f"{e(status_page.HELD_NOTE)}</p>")
        figures.append(f'<figure class="pop" data-population="{p}"><figcaption><strong>{p.capitalize()}</strong>: '
                       f"{source}</figcaption>{path_svg(rs, p)}<p>{tally(rs, p)}.</p>{note}</figure>")
    return f'<div class="pair">{"".join(figures)}</div>'


def number_cell(nb, run):
    """status_page's number, out of the run's own length; a placeholder until it can be measured."""
    p = f'data-population="{run.population}"'
    _, measured = status_page.number_value(nb, run)
    if not measured:
        return f'<td class="placeholder" {p}>not measured yet ({code(nb.milestone)})</td>'
    m, total = status_page.minutes(run, nb.step), run_minutes(run)
    return f'<td class="measured" {p}><span class="count">{m:.1f} of the run\'s {total:.1f} minutes</span></td>'


def numbers_section(runs):
    rows = []
    for nb in status_page.NUMBERS:
        cells = "".join(number_cell(nb, runs[p]) for p in status_page.POPULATIONS)
        rows.append(f'<tr id="n-{nb.key}"><th scope="row">{e(nb.label)}<br><span class="note">{e(nb.unit)}, due '
                    f"{code(nb.milestone)}</span></th>{cells}</tr>")
    return ('<div class="scroll"><table class="numbers"><thead><tr><th scope="col">Number</th>'
            '<th scope="col">Rehearsal</th><th scope="col">Live</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>")


# The nodes, day by day

def node_ids(days):
    return sorted({n.node_id for d in days for n in d.nodes})


def timeline_svg(days):
    cal = stats_model.calendar(days)
    ids = node_ids(days)
    left = stats_model.left_on(days)
    label_w, cell, top, row_h = 132, 16, 64, 30
    width, height = label_w + cell * len(cal) + 8, top + row_h * len(ids) + 4
    parts = []
    for i, (date, _) in enumerate(cal):
        x = label_w + i * cell + cell / 2
        parts.append(f'<text class="code date" transform="translate({x + 3} {top - 6}) rotate(-90)">{e(date)}</text>')
    for j, nid in enumerate(ids):
        y = top + j * row_h
        parts.append(f'<g class="node" data-node="{e(nid)}"><text class="code id" x="0" y="{y + 12}">'
                     f"{e(nid[:12])}…<title>{e(nid)}</title></text>")
        if nid in left:
            parts.append(f'<text class="leftlabel" x="0" y="{y + 25}">left on '
                         f'<tspan class="code">{e(left[nid])}</tspan></text>')
        for i, (date, day) in enumerate(cal):
            x = label_w + i * cell
            node = next((n for n in day.nodes if n.node_id == nid), None) if day else None
            for k, stem in enumerate(stats_model.QUESTIONS):
                if day is None:
                    kind, says = "none", "no day file"
                elif day.missed:
                    kind, says = "missed", "the reporter missed this day"
                elif node is None:
                    kind, says = "out", "not asked: not on the list this day"
                else:
                    state = node.answers[stem].state
                    kind, says = KIND[state], SAYS[state]
                tip = f"{date} · {nid[:12]}… · {stats_model.QUESTIONS[stem]}: {says}"
                parts.append(f'<rect class="t-{kind}" data-date="{date}" data-q="{stem}" x="{x + 1}" '
                             f'y="{y + 1 + k * 10}" width="{cell - 2}" height="9"><title>{e(tip)}</title></rect>')
            if left.get(nid) == date:
                parts.append(f'<line class="leftmark" x1="{x}" y1="{y - 2}" x2="{x}" y2="{y + 22}"/>')
        parts.append("</g>")
    return (f'<svg class="timeline" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Each node, each day: answered, refused, absent, missed or not asked">{"".join(parts)}</svg>')


def legend():
    items = [("answered", "a reply came back: " + ", ".join(outcome(s) for s, k in KIND.items() if k == "answered")),
             ("refused", f"a signed {outcome('refusal')}"),
             ("absent", f"nothing came back at any attempt: {outcome('absence')}"),
             ("missed", "the reporter did not run that day"),
             ("out", "not asked: not on the list that day, or after it left"),
             ("none", "no day file for that date")]
    return '<ul class="legend">' + "".join(
        f'<li><svg width="14" height="12" aria-hidden="true"><rect class="t-{k}" x="1" y="1" width="12" height="10"/>'
        f"</svg> <strong>{k if k not in ('out', 'none') else {'out': 'not asked', 'none': 'no file'}[k]}</strong>: "
        f"{text}</li>" for k, text in items) + "</ul>"


def nodes_section(days):
    if not days:
        return "<p>No day yet. The first scrubbed day file draws the first column here.</p>"
    cal = stats_model.calendar(days)
    n = len(cal)
    settled = sum(1 for _, d in cal if d and not d.missed)
    missed = sum(1 for _, d in cal if d and d.missed)
    return (f"<p>From {when(cal[0][0])} to {when(cal[-1][0])}: {count(settled, n, 'day')} with a settled run, "
            f"{count(missed, n, 'day')} missed, {count(n - settled - missed, n, 'day')} with no day file.</p>"
            '<p class="note">One row per node, by node id; the order means nothing. Each day is two marks: '
            f"{code('checkpoints')} above, {code('history_card/1')} below. A line marks the day a node left.</p>"
            + legend() + f'<div class="scroll">{timeline_svg(days)}</div>')


# Each day

def attempts(answer):
    n = len(answer.attempts)
    items = "".join(f"<li>{when(a.at)} {outcome(a.state)}</li>" for a in answer.attempts)
    return f'asked {WORDS.get(n, code(n) + " times")}<ul class="attempts">{items}</ul>'


def answer_cell(a):
    parts = [f"<p>{outcome(a.state)}</p>", "<dl>"]
    parts.append(f"<dt>attempts</dt><dd>{attempts(a)}</dd>")
    if a.log_size:
        parts.append(f"<dt>log size at checkpoint</dt><dd>{code('mmr_size=' + str(a.log_size['mmr_size']))} at "
                     f"{when(a.log_size['timestamp'])}</dd>")
    if a.answer_sha256:
        parts.append(f"<dt>answer</dt><dd>{code('sha256=' + a.answer_sha256, 'sha')}</dd>")
    else:
        parts.append("<dt>answer</dt><dd>none: nothing came back</dd>")
    parts.append("</dl>")
    return f'<td class="answer o-{e(a.state)}">{"".join(parts)}</td>'


def node_row(node):
    fact = stats_model.layout.absence_fact(node.absent_running)
    head = short(node.node_id)
    if fact:
        head += f'<br>{code(fact, "fact")}'
    if node.left_on:
        head += f"<br>left on {when(node.left_on)}"
    cells = [f'<th scope="row">{head}</th>', f"<td>{code(node.node_line)}</td>"]
    cells += [answer_cell(node.answers[stem]) for stem in stats_model.QUESTIONS]
    return "<tr>" + "".join(cells) + "</tr>"


def summary(day):
    m = day.listed
    items = []
    for stem, question in stats_model.QUESTIONS.items():
        states = [n.answers[stem].state for n in day.nodes]
        replied = sum(s != "absence" for s in states)
        by_state = ", ".join(f"{outcome(s)}: {count(states.count(s), m)}"
                             for s in stats_model.layout.DAY_STATES if s in states)
        items.append(f"<li>{code(question)}: {count(replied, m)} replied ({by_state}).</li>")
    return '<ul class="summary">' + "".join(items) + "</ul>"


def day_section(day):
    head = f'<h3 id="d{e(day.date)}">{when(day.date)}</h3>'
    file = f'<a href="{DATA}/{e(day.file)}">{code(day.file)}</a>'
    if day.missed:
        return (f'<section class="day missed">{head}<p><strong>Missed:</strong> no run this day. '
                f"The day file says: {code(day.missed_reason)} {file}</p></section>")
    intro = (f"<p>The reporter (key {short(day.reporter_key_id) if day.reporter_key_id else 'not recorded'}) asked "
             f"{count(len(day.nodes), day.listed)} the two questions, under {code('versions.lock')} "
             f"{short(day.versions_lock_sha256) if day.versions_lock_sha256 else 'not recorded'}. {file}</p>")
    if not day.nodes:
        return f'<section class="day">{head}{intro}<p>No node was asked this day.</p></section>'
    table = ('<div class="scroll"><table><thead><tr><th scope="col">Node</th><th scope="col">Node says</th>'
             f'<th scope="col">{code("checkpoints")}</th><th scope="col">{code("history_card/1")}</th>'
             "</tr></thead><tbody>" + "".join(node_row(n) for n in day.nodes) + "</tbody></table></div>")
    order = '<p class="note">Nodes are listed by node id. The order means nothing.</p>'
    return f'<section class="day">{head}{intro}{summary(day)}{order}{table}</section>'


def days_section(days):
    if not days:
        return "<p>No day yet. The first scrubbed day file appears here.</p>"
    index = "".join(f'<li><a href="#d{e(d.date)}">{when(d.date)}</a>{" (missed)" if d.missed else ""}</li>'
                    for d in days)
    return f'<ul class="days">{index}</ul>' + "".join(day_section(d) for d in days)


def render(days, rs, runs):
    template = Template((HERE / "template.html").read_text(encoding="utf-8"))
    return template.substitute(loop=loop_section(rs, runs), numbers=numbers_section(runs),
                               nodes=nodes_section(days), days=days_section(days))


ROOT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="0; url=stats/">
<title>Evidence network loop</title>
</head>
<body>
<p>The evidence network's loop stats are at <a href="stats/">stats/</a>.</p>
</body>
</html>
"""


def build(data, findings, lock, out):
    days = stats_model.load(data)  # raise before anything is written
    runs = {p: status_page.read_run(p, d) for p, d in status_page.latest_runs(findings).items()}
    rs = status_page.rows(runs)
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    site = out / SITE
    try:
        status_page.build(findings, lock, site / STATUS)
    except status_page.StatusError:
        shutil.rmtree(out, ignore_errors=True)
        raise
    (site / DATA).mkdir(parents=True)
    for d in days:
        shutil.copyfile(Path(data) / d.file, site / DATA / d.file)
    (site / "index.html").write_text(render(days, rs, runs), encoding="utf-8")
    (out / "index.html").write_text(ROOT_PAGE, encoding="utf-8")
    return days


def main(argv):
    args = argv[1:]
    opts = {"--out": "_site", "--findings": "findings", "--lock": "versions.lock"}
    for opt in opts:
        if opt in args:
            i = args.index(opt)
            opts[opt] = args[i + 1]
            del args[i:i + 2]
    data = Path(args[0] if args else "stats/data/daily")
    try:
        days = build(data, opts["--findings"], opts["--lock"], opts["--out"])
    except (stats_model.StatsError, status_page.StatusError) as err:
        for p in err.problems:
            print(p, file=sys.stderr)
        print(f"{data}: the stats site was not built", file=sys.stderr)
        return 1
    print(f"{opts['--out']}/{SITE}/index.html: {len(days)} day{'' if len(days) == 1 else 's'} from {data}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
