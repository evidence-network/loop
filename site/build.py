#!/usr/bin/env python3
"""Build the daily report page from reports/daily/ only.

Usage: build.py [REPORTS] [--out DIR]   (default: reports/daily, _site)

Writes DIR/index.html, a static page (no scripts), and copies REPORTS to
DIR/reports/daily/ byte for byte so every link on the page opens the raw file.
Reading the reports is site/reports_model.py's job; a report it cannot read
fails the build (exit 1) and nothing is written.

Every count on the page reads "N of M listed nodes". Nodes are listed by node
id, and the page says that order means nothing. No score, rank or total per
node. Standard library only.
"""
import html
import shutil
import sys
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reports_model  # noqa: E402

HERE = Path(__file__).resolve().parent
RAW = "reports/daily"
WORDS = {1: "once", 2: "twice"}

e = html.escape


def count(n, m):
    """The only way the page states a number of nodes."""
    return f'<span class="count">{n} of {m} listed node{"" if m == 1 else "s"}</span>'


def code(text, cls=None):
    return f'<code{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{e(str(text))}</code>'


def outcome(name):
    return code(name, f"outcome o-{name}")


def short(hex_id):
    return f'<code title="{e(hex_id)}">{e(hex_id[:12])}…</code>'


def link(path, label):
    return f'<a href="{e(RAW + "/" + path)}">{e(label)}</a>'


def when(stamp):
    return f'<time datetime="{e(stamp)}">{e(stamp)}</time>'


def attempts(answer):
    n = len(answer.attempts)
    asked = f"asked {WORDS.get(n, code(n) + ' times')}"
    times = []
    for a in answer.attempts:
        if a.at:
            times.append(when(a.at))
        elif a.window:
            times.append(f"during the run, {when(a.window[0])} to {when(a.window[1])}")
    return f"{asked}: {'; '.join(times)}" if times else asked


def answer_cell(a):
    parts = [f"<p>{outcome(a.outcome)}</p>", "<dl>"]
    if a.outcome == "verified":
        parts.append(f"<dt>checkpoints in the answer</dt><dd>{code(f'checkpoints={a.checkpoints}')}</dd>")
        parts.append(f"<dt>anchor</dt><dd>{short(a.anchor)}</dd>")
        parts.append("<dt>epoch</dt><dd>" + (code(a.epoch) if a.epoch else "none: the answer format carries no epoch") + "</dd>")
        if "witnessed" in a.details:
            parts.append(f"<dt>witnessed</dt><dd>{code(a.details['witnessed'])}</dd>")
    for key in ("reason", "why"):
        if key in a.details:
            parts.append(f"<dt>{key}</dt><dd>{code(a.details[key])}</dd>")
    parts.append(f"<dt>attempts</dt><dd>{attempts(a)}</dd>")
    parts.append("</dl>")
    raw = [link(a.files["request"], "request")] if "request" in a.files else []
    if "response" in a.files:
        raw.append(link(a.files["response"], "response bytes"))
    if "absence" in a.files:
        raw.append(link(a.files["absence"], "why absent"))
    raw.append(link(a.files["verification"], "result line"))
    parts.append(f'<p class="raw">{" · ".join(raw)}</p>')
    parts.append(f"<details><summary>result line</summary>{code(a.line, 'line')}</details>")
    return "".join(parts)


def key_cell(node):
    if node.announced_key is None:
        return "no key announced"
    listed = {a.details.get("listed_key") for a in node.answers.values()} - {None, "unknown"}
    vs = f"; {code('listed_key=' + listed.pop())}" if len(listed) == 1 else ""
    return f"{short(node.announced_key)}{vs}"


def node_row(node):
    cells = [
        f'<th scope="row">{short(node.node_id)}<br>{link(node.files["NODE"], "NODE")} · {link(node.files["peer-key"], "peer-key.json")}</th>',
        f"<td>{code(node.node_line)}</td>",
    ]
    for stem in reports_model.QUESTIONS:
        a = node.answers[stem]
        cells.append(f'<td class="answer o-{a.outcome}">{answer_cell(a)}</td>')
    cells.append(f"<td>{key_cell(node)}</td>")
    return "<tr>" + "".join(cells) + "</tr>"


def summary(day):
    m = day.listed
    items = []
    for stem, question in reports_model.QUESTIONS.items():
        outcomes = [n.answers[stem].outcome for n in day.nodes]
        replied = sum(o != "absence" for o in outcomes)
        by_class = ", ".join(
            f"{outcome(o)}: {count(outcomes.count(o), m)}" for o in reports_model.OUTCOMES if o in outcomes
        )
        items.append(f"<li>{code(question)}: {count(replied, m)} answered ({by_class}).</li>")
    return "<ul class=\"summary\">" + "".join(items) + "</ul>"


def day_section(day):
    head = f'<h3 id="d{e(day.date)}">{when(day.date)}</h3>'
    if day.missed is not None:
        return (f'<section class="day missed">{head}<p><strong>Missed:</strong> no run this day. '
                f'The mark says: {code(day.missed)} {link(day.files["MISSED"], "MISSED")}</p></section>')
    run = day.run
    intro = (f"<p>Reporter {short(run['reporter']['node_id'])} (key {short(run['reporter']['key_id'])}) asked "
             f"{count(day.listed, day.listed)} the two questions, "
             f"from {when(run['started'])} to {when(run['finished'])}. {link(day.files['RUN.json'], 'RUN.json')}</p>")
    if not day.nodes:
        return f'<section class="day">{head}{intro}<p>No node was listed this day.</p></section>'
    table = ('<div class="scroll"><table><thead><tr><th scope="col">Node</th><th scope="col">Node says</th>'
             f'<th scope="col">{code("checkpoints")}</th><th scope="col">{code("history_card/1")}</th>'
             '<th scope="col">Key the answers were judged under</th></tr></thead><tbody>'
             + "".join(node_row(n) for n in day.nodes) + "</tbody></table></div>")
    order = '<p class="note">Nodes are listed by node id. The order means nothing.</p>'
    return f'<section class="day">{head}{intro}{summary(day)}{order}{table}</section>'


def render(days):
    if days:
        body = "".join(day_section(d) for d in days)
    else:
        body = "<p>No report yet. The first run writes the first day here.</p>"
    index = "".join(f'<li><a href="#d{e(d.date)}">{when(d.date)}</a>{" (missed)" if d.missed else ""}</li>' for d in days)
    template = Template((HERE / "template.html").read_text(encoding="utf-8"))
    return template.substitute(days=body, day_index=f"<ul class=\"days\">{index}</ul>" if days else "")


def build(reports, out):
    days = reports_model.load(reports)  # raises before anything is written
    page = render(days)
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(reports, out / RAW)
    (out / "index.html").write_text(page, encoding="utf-8")
    return days


def main(argv):
    args = argv[1:]
    out = "_site"
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1]
        del args[i:i + 2]
    reports = Path(args[0] if args else "reports/daily")
    try:
        days = build(reports, out)
    except reports_model.ReportError as err:
        for p in err.problems:
            print(p, file=sys.stderr)
        print(f"{reports}: the page was not built", file=sys.stderr)
        return 1
    print(f"{out}/index.html: {len(days)} day{'' if len(days) == 1 else 's'} from {reports}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
