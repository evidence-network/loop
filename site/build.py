#!/usr/bin/env python3
"""Build the stats site from the scrubbed daily set and the loop findings.

Usage: build.py [DATA] [--findings DIR] [--lock FILE] [--out DIR] [--rehearsal DIR]
       (defaults: stats/data/daily, findings, versions.lock, _site, none)

Writes DIR/stats/index.html, a static page (no scripts, inline SVG and CSS, no
external assets), served at <pages root>/stats/. It reads as the network over
time, newest day on the right of every chart:
  - today in words, each count out of what it counts, over a pulse band: one
    column per day for the newest 30 days (older days in a thinner band), one
    square per listed node in its day state;
  - small multiples on one day axis: nodes answering, records sealed, book
    growth per node, asks by outcome, days missed, nodes listed with joins and
    leaves. Each is an SVG with a <title> and <desc>, a one-line caption with
    its denominator and window, and its numbers as a table in a <details>;
  - the loop's twelve steps as a tile strip, rehearsal above live, each tile
    linking its status row, and the steps that passed per run;
  - the five numbers as sparklines over runs, and the both-halves count;
  - the nodes, day by day, as a heatmap, with each node's answers in a table;
  - how to read the page, collapsed at the end.
The loop's states and numbers are status/status_page.py's: this module calls
its functions and builds the status page itself into DIR/stats/status/. The
day files are read by site/stats_model.py and copied to DIR/stats/data/daily/.
A day file it cannot read fails the build (exit 1) and nothing is written.

With --rehearsal DIR (stats/data/rehearsal/: the reporter rehearsal, a test,
one scrubbed file per 10-minute UTC slot) holding any slot file, a second page,
DIR/stats/rehearsal/index.html, shows those slots under a TEST label, one row
per slot from the first to the last, newest first; a slot marked missed, and a
slot with no file, reads MISSED. The daily page then carries one TEST line
linking it, and no rehearsal row: the two sets are never mixed. With no slot
file the site is exactly as without --rehearsal. Its files are copied to
DIR/stats/data/rehearsal/.

Rehearsal and live are drawn apart and never added. A missed day, a day with
no file and a count that fell are gaps, never zero. Every count reads "N of M
...". No score, rank or total per node. Nothing links under reports/. The
output depends only on the inputs: no build time, no random ids. Standard
library only.
"""
import datetime as dt
import html
import re
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
REHEARSAL = "rehearsal"
REHEARSAL_DATA = "data/rehearsal"
STATUS = "status"
WORDS = {1: "once", 2: "twice"}
RUNS = 30  # the newest runs a sparkline draws, per population
WINDOW_DAYS = 7  # the runs the first-sealed median reads: the newest run of a population and the six days before it
# A step's state on the strip: (CSS class, the label status_page gives it). FAIL and MALFORMED are both red, told
# apart by their label and border; NOT_CHECKED is never either.
TALLY = ("PASS", "FAIL", "MALFORMED", status_page.NOT_CHECKED)
# A day's state of one question, in plain words (hover text: the state names themselves are shown as code).
SAYS = {"verified": "signed answer", "no_checkpoint_yet": "signed: no checkpoint yet",
        "no_announced_key": "a reply, no key announced", "not_evidence": "a reply that is not evidence",
        "refusal": "signed refusal", "absence": "nothing came back"}
NODE_SAYS = dict(SAYS, verified="signed answers to both questions",
                 partial="a signed answer to one question only")
ANSWERED = ("verified", "no_checkpoint_yet", "no_announced_key", "not_evidence")
# Ask outcomes, in stacking order from the axis up.
ASKS = (("answered", "a reply came back"), ("refused", "a signed refusal"), ("absent", "nothing came back"),
        ("not-asked", "listed, not in the day file"))

e = html.escape


def count(n, m, what="listed node"):
    """The only way the page states a count: always out of something."""
    return f'<span class="count">{n} of {m} {what}{"" if m == 1 else "s"}</span>'


def value(text):
    """A measured value whose denominator the sentence around it names."""
    return f'<span class="value">{e(str(text))}</span>'


def code(text, cls=None):
    return f'<code{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{e(str(text))}</code>'


def outcome(name):
    return code(name, f"outcome o-{name}")


def short(hex_id):
    return f'<code title="{e(hex_id)}">{e(hex_id[:12])}…</code>'


def when(stamp):
    return f'<time datetime="{e(stamp)}">{e(stamp)}</time>'


def num(v):
    """A drawn number as the page writes it: no trailing zeros, at most two decimals."""
    return f"{round(v, 2):g}"


def nice(n):
    """The smallest of 1, 2, 5, 10, 20, 50, ... at or above n: the y axis's top value."""
    step = 1
    while True:
        for m in (1, 2, 5):
            if m * step >= n:
                return m * step
        step *= 10


# Drawing

def svg(key, cls, w, h, title, desc, body, fixed=False, unit=None, base=None):
    """An SVG chart with its <title> and <desc>. `fixed` draws it at its own size (it scrolls in its box);
    otherwise CSS scales it, never below its own width. `unit` and `base` are the y scale: a value v is drawn
    v * unit units tall, from the line y = base."""
    size = f' width="{w}" height="{h}"' if fixed else ""
    scale = f' data-unit="{num(unit)}" data-base="{num(base)}"' if unit is not None else ""
    return (f'<svg class="{cls}" id="{key}"{size} viewBox="0 0 {w} {h}" role="img" '
            f'aria-labelledby="{key}-t {key}-d"{scale}><title id="{key}-t">{e(title)}</title>'
            f'<desc id="{key}-d">{e(desc)}</desc>{body}</svg>')


def hatch(key):
    """The diagonal hatch a gap is filled with, one per SVG (an id is per page)."""
    return (f'<defs><pattern id="{key}-h" width="6" height="6" patternUnits="userSpaceOnUse" '
            f'patternTransform="rotate(45)"><line class="hatch" x1="0" y1="0" x2="0" y2="6"/></pattern></defs>')


class Axis:
    """The day axis every small multiple shares: one column per date, the newest on the right."""

    W, LEFT, RIGHT, TOP, PH, BOTTOM = 340, 40, 10, 22, 112, 34

    def __init__(self, cal):
        self.cal = cal
        self.n = max(len(cal), 1)
        self.cw = (self.W - self.LEFT - self.RIGHT) / self.n
        self.base = self.TOP + self.PH
        self.H = self.base + self.BOTTOM

    def x(self, i):
        return self.LEFT + i * self.cw

    def mid(self, i):
        return self.x(i) + self.cw / 2

    def shown(self):
        """The dates labelled: the newest, and every k-th back from it, never closer than 48 units."""
        k = max(1, int(-(-48 // self.cw)))
        return sorted(i for i in range(len(self.cal)) if (len(self.cal) - 1 - i) % k == 0)

    def frame(self, key, ymax, unit, label):
        """Weekend tints, the axis, the y labels (0 and the top) and the date labels."""
        parts = [hatch(key)]
        for i, (date, _) in enumerate(self.cal):
            if dt.date.fromisoformat(date).weekday() >= 5:
                parts.append(f'<rect class="weekend" x="{num(self.x(i))}" y="{self.TOP}" width="{num(self.cw)}" '
                             f'height="{self.PH}"/>')
        if ymax is not None:
            top = self.base - ymax * unit
            parts.append(f'<line class="grid" x1="{self.LEFT}" y1="{num(top)}" x2="{self.W - self.RIGHT}" '
                         f'y2="{num(top)}"/>'
                         f'<text class="code tick" x="{self.LEFT - 6}" y="{num(top + 4)}">{ymax}</text>'
                         f'<text class="code tick" x="{self.LEFT - 6}" y="{self.base + 4}">0</text>')
        parts.append(f'<line class="axis" x1="{self.LEFT}" y1="{self.base}" x2="{self.W - self.RIGHT}" '
                     f'y2="{self.base}"/>')
        for i in self.shown():
            newest = " newest" if i == len(self.cal) - 1 else ""
            parts.append(f'<text class="code xdate{newest}" x="{num(self.mid(i))}" y="{self.base + 16}">'
                         f"{e(self.cal[i][0][5:])}</text>")
        if self.cal:
            x = self.mid(len(self.cal) - 1)
            parts.append(f'<path class="newest-mark" d="M{num(x - 4)} {self.base + 30} L{num(x + 4)} '
                         f'{self.base + 30} L{num(x)} {self.base + 23} Z"/>')
        if label:
            parts.append(f'<text class="ylabel" x="{self.LEFT}" y="{self.TOP - 8}">{e(label)}</text>')
        return "".join(parts)

    def gap(self, key, i, why, cls="gap"):
        """A hatched column: no value that day, and the reason on hover."""
        date = self.cal[i][0]
        return (f'<rect class="{cls}" data-k="{date}" data-gap="{e(why)}" x="{num(self.x(i))}" y="{self.TOP}" '
                f'width="{num(self.cw)}" height="{self.PH}" fill="url(#{key}-h)"><title>{e(date)}: no value, '
                f"{e(why)}</title></rect>")


def day_gap(day):
    """Why a date has no value in a chart, or None when it has a settled run."""
    if day is None:
        return "no day file"
    if day.missed:
        return "the reporter missed this day"
    return None


def chart(key, title, caption, svg_html, legend_html, table_html, wide=False):
    """One small multiple: title, caption (denominator and window), the SVG, its legend and its numbers."""
    return (f'<figure class="chart{" wide" if wide else ""}" id="f-{key}"><figcaption><h3>{e(title)}</h3>'
            f'<p class="caption">{caption}</p></figcaption><div class="scroll">{svg_html}</div>{legend_html}'
            f"<details><summary>The numbers</summary><div class=\"scroll\">{table_html}</div></details></figure>")


def swatch(cls, label):
    return (f'<li><svg width="14" height="14" aria-hidden="true"><rect class="{cls}" x="1" y="1" width="12" '
            f'height="12" rx="2"/></svg> {label}</li>')


def legend(items):
    return '<ul class="legend">' + "".join(swatch(c, t) for c, t in items) + "</ul>"


GAP_KEYS = [("gap", "hatched: the reporter missed the day, or a count fell"), ("nofile", "dashed: no day file")]


def state_legend(extra=()):
    return legend([(f"st-{s}", outcome(s)) for s in stats_model.layout.DAY_STATES] + list(extra))


def window_words(cal):
    if not cal:
        return "no day yet"
    return f"{when(cal[0][0])} to {when(cal[-1][0])}, {count(len(cal), len(cal), 'day')} shown"


def th(*cols):
    return "<thead><tr>" + "".join(f'<th scope="col">{c}</th>' for c in cols) + "</tr></thead>"


def td_num(k, v):
    return f'<td class="num" data-k="{e(k)}">{num(v)}</td>'


def quoted(text):
    """Plain words, with a milestone or a file name in them shown as code."""
    return re.sub(r"\b(M\d|run\.json)\b", lambda m: code(m.group(1)), e(text))


def td_gap(k, why, span=1):
    span = f' colspan="{span}"' if span > 1 else ""
    return f'<td class="gapcell" data-k="{e(k)}" data-gap="{e(why)}"{span}>gap: {quoted(why)}</td>'


def td_text(k, text):
    return f'<td data-k="{e(k)}">{e(text)}</td>'


# Today, and the pulse band

def today(days, cal):
    if not days:
        return "<p class=\"today\">No day yet. The first scrubbed day file draws the first column here.</p>"
    newest = max(days, key=lambda d: d.date)
    missed = sum(1 for _, d in cal if d and d.missed)
    window = f"{count(missed, len(cal), 'day')} missed, {when(cal[0][0])} to {when(cal[-1][0])}"
    if newest.missed:
        return (f'<p class="today">On {when(newest.date)}, the newest day, the reporter missed its run: '
                f"{code(newest.missed_reason)} · {window}</p>")
    answered = sum(1 for n in newest.nodes if any(a.state != "absence" for a in n.answers.values()))
    point = next((p for p in stats_model.sealed_per_day(days) if p.date == newest.date), None)
    if point and point.sealed is not None:
        sealed = (f"records sealed: {value(point.sealed)}, from {count(point.nodes, point.listed)} with an "
                  "earlier count")
    else:
        sealed = f"records sealed: no value, {e(point.why) if point else 'no day file'}"
    return (f'<p class="today">On {when(newest.date)}, the newest day: {count(answered, newest.listed)} answered '
            f"at least one question · {sealed} · {window}</p>")


def band_column(x, w, date, day, tall, cell, key):
    """One day of the band: a square per listed node (checkpoints above, history_card below), or a gap."""
    parts = []
    tip_date = e(date)
    if day is None:
        return (f'<rect class="nofile" data-k="{date}" data-gap="no day file" x="{num(x)}" y="0" width="{num(w)}" '
                f'height="{tall}"><title>{tip_date}: no day file</title></rect>')
    if day.missed:
        return (f'<rect class="gap" data-k="{date}" data-gap="the reporter missed this day" x="{num(x)}" y="0" '
                f'width="{num(w)}" height="{tall}" fill="url(#{key}-h)"><title>{tip_date}: missed, '
                f"{e(day.missed_reason)}</title></rect>")
    half = (cell - 2) / 2
    for j, node in enumerate(day.nodes):
        y = tall - (j + 1) * (cell + 2) + 2
        for k, stem in enumerate(stats_model.QUESTIONS):
            state = node.answers[stem].state
            tip = f"{date} · {node.node_id[:12]}… · {stats_model.QUESTIONS[stem]}: {SAYS[state]}"
            parts.append(f'<rect class="st-{state}" data-k="{date}|{stem}" data-v="{state}" x="{num(x)}" '
                         f'y="{num(y + k * (half + 2))}" width="{num(w)}" height="{num(half)}">'
                         f"<title>{e(tip)}</title></rect>")
    for j in range(len(day.nodes), day.listed):
        y = tall - (j + 1) * (cell + 2) + 2
        parts.append(f'<rect class="notasked" x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{cell}">'
                     f"<title>{tip_date}: listed, not in the day file</title></rect>")
    return "".join(parts)


# The band's drawing width: the content column at desktop (72rem less its gutters and the box's padding).
# CSS scales it to the column and never below BAND_MIN px, so its BAND_FONT-unit labels stay 12px or larger
# (BAND_FONT * BAND_MIN / BAND_W >= 12); narrower than that, it scrolls in its box.
BAND_W, BAND_MIN, BAND_FONT = 1100, 880, 15
BAND_COL = 48  # the widest a day's column gets, so a few days do not become giant blocks
BAND_CELL = 28  # the tallest a node's square gets


def band(days):
    """The pulse band across the content width: the newest dates as full columns, right-aligned, each as wide as
    the width allows up to BAND_COL; older ones as thin columns just left of them; spare width on the left."""
    older, shown = stats_model.window(days)
    if not shown:
        return ""
    key, left, right, gap = "band", 4, 8, 12
    thin = min(5, BAND_W * 0.2 / len(older)) if older else 0
    col = min(BAND_COL, (BAND_W - left - right - (len(older) * (thin + 1) + gap if older else 0)) / len(shown))
    cell = max(6, min(BAND_CELL, col - 6))
    listed = max([d.listed for _, d in older + shown if d] + [1])
    tall = listed * (cell + 2)
    top = 26
    x0 = BAND_W - right - len(shown) * col  # the window's first column; everything left of it is spare or older
    xo = x0 - gap - len(older) * (thin + 1)
    height = top + tall + 50
    centre = lambda i: x0 + i * col + col / 2  # noqa: E731
    parts = [hatch(key), f'<g transform="translate(0 {top})">']
    for i, (date, day) in enumerate(older):
        parts.append(band_column(xo + i * (thin + 1), thin, date, day, tall, cell, key))
    for i, (date, day) in enumerate(shown):
        if dt.date.fromisoformat(date).weekday() >= 5:
            parts.append(f'<rect class="weekend" x="{num(x0 + i * col)}" y="0" width="{num(col)}" height="{tall}"/>')
        parts.append(band_column(centre(i) - cell / 2, cell, date, day, tall, cell, key))
    parts.append("</g>")
    first, left_on = stats_model.first_seen(days), stats_model.left_on(days)
    start = stats_model.calendar(days)[0][0]
    for i, (date, _) in enumerate(shown):
        x = centre(i)
        joined = sorted(n for n, d in first.items() if d == date and d != start)
        gone = sorted(n for n, d in left_on.items() if d == date)
        for kind, ids, d in (("join", joined, f"M{num(x - 7)} 20 L{num(x + 7)} 20 L{num(x)} 8 Z"),
                             ("leave", gone, f"M{num(x - 7)} 8 L{num(x + 7)} 8 L{num(x)} 20 Z")):
            if ids:
                verb = "joined" if kind == "join" else "left"
                tip = f"{date}: " + ", ".join(f"{n[:12]}… {verb}" for n in ids)
                parts.append(f'<path class="tick-{kind}" d="{d}"><title>{e(tip)}</title></path>')
    every = max(1, -(-64 // int(col)))  # a label at least 64 units from the next
    for i in (i for i in range(len(shown)) if (len(shown) - 1 - i) % every == 0):
        newest = " newest" if i == len(shown) - 1 else ""
        parts.append(f'<text class="code xdate{newest}" x="{num(centre(i))}" y="{top + tall + 20}">'
                     f"{e(shown[i][0][5:])}</text>")
    x = centre(len(shown) - 1)
    parts.append(f'<path class="newest-mark" d="M{num(x - 6)} {top + tall + 44} L{num(x + 6)} {top + tall + 44} '
                 f'L{num(x)} {top + tall + 32} Z"/>')
    if older:
        parts.append(f'<text class="older" x="{num(xo)}" y="{top + tall + 20}">older</text>')
    missed = sum(1 for _, d in shown if d and d.missed)
    nofile = sum(1 for _, d in shown if d is None)
    desc = (f"One column per day from {shown[0][0]} to {shown[-1][0]}, newest on the right"
            + (f", and {len(older)} older days in a thin band on the left" if older else "")
            + ". Each listed node is a square, its upper half the checkpoints answer and its lower half the "
              f"history_card/1 answer, coloured by its day state. {missed} of {len(shown)} days missed (hatched), "
              f"{nofile} of {len(shown)} with no day file (dashed). A triangle up marks a node's first day, "
              "a triangle down the day it left.")
    title = "Each day's listed nodes, by day state"
    return svg(key, "band", BAND_W, height, title, desc, "".join(parts))


def band_table(days):
    """Per date, per question, how many listed nodes settled in each state; every day file linked."""
    cal = stats_model.calendar(days)
    states = stats_model.layout.DAY_STATES
    head = th("Date", "Run", "Listed", "Question", *(outcome(s) for s in states), "Day file")
    rows = []
    for date, day in reversed(cal):
        if day is None or day.missed:
            why = day_gap(day)
            file = f'<a href="{DATA}/{e(day.file)}">{code(day.file)}</a>' if day else "none"
            rows.append(f'<tr><th scope="row">{when(date)}</th><td>{"missed" if day else "none"}</td>'
                        f'<td class="num">{day.listed if day else "none"}</td><td></td>'
                        f"{td_gap(date, why, len(states))}<td>{file}</td></tr>")
            continue
        for k, (stem, question) in enumerate(stats_model.QUESTIONS.items()):
            got = [n.answers[stem].state for n in day.nodes]
            cells = "".join(td_num(f"{date}|{stem}|{s}", got.count(s)) for s in states)
            lead = (f'<th scope="row" rowspan="2">{when(date)}</th><td rowspan="2">settled</td>'
                    f'<td class="num" rowspan="2">{day.listed}</td>') if k == 0 else ""
            file = (f'<td rowspan="2"><a href="{DATA}/{e(day.file)}">{code(day.file)}</a></td>') if k == 0 else ""
            rows.append(f"<tr>{lead}<td>{code(question)}</td>{cells}{file}</tr>")
    return f'<table class="band-table">{head}<tbody>{"".join(rows)}</tbody></table>'


def pulse_section(days):
    cal = stats_model.window(days)[1]
    if not days:
        return today(days, cal)
    lead = today(days, cal)
    keys = state_legend([("notasked", "listed, not in the day file"), ("gap-key", "missed day (hatched)"),
                         ("nofile", "no day file (dashed)")])
    return (f'{lead}<figure class="pulse" id="f-band"><div class="scroll band-box">{band(days)}</div>'
            f'<figcaption class="caption">Squares are listed nodes, by node id, out of the nodes listed that day; '
            f"{window_words(cal)}. The newest day is on the right, under the marker.</figcaption>{keys}"
            f'<details><summary>The numbers, and each day file</summary><div class="scroll">{band_table(days)}'
            "</div></details></figure>")


# The small multiples

def stacked(axis, key, i, date, segments, unit):
    """One day's stacked bar: (series, count, CSS class, words) from the axis up. Zero-height segments are drawn
    too, so every value in the table has its mark."""
    parts, y = [], axis.base
    for series, n, cls, words in segments:
        h = n * unit
        y -= h
        parts.append(f'<rect class="bar {cls}" data-k="{date}|{series}" data-v="{n}" x="{num(axis.x(i) + 1)}" '
                     f'y="{num(y)}" width="{num(max(axis.cw - 2, 1))}" height="{num(h)}">'
                     f"<title>{e(date)}: {n} {e(words)}</title></rect>")
    return "".join(parts)


def answering_chart(cal):
    axis, key = Axis(cal), "c-answering"
    settled = [d for _, d in cal if d and not d.missed]
    ymax = nice(max([d.listed for _, d in cal if d] + [1]))
    unit = axis.PH / ymax
    parts = [axis.frame(key, ymax, unit, "nodes")]
    rows = []
    line = []
    for i, (date, day) in enumerate(cal):
        why = day_gap(day)
        if day is not None:
            line.append((i, day.listed))
        else:
            line.append(None)
        if why:
            parts.append(axis.gap(key, i, why, "gap" if day else "nofile"))
            rows.append(f'<tr><th scope="row">{when(date)}</th>'
                        + (td_num(f"{date}|listed", day.listed) if day else "<td>none</td>")
                        + td_gap(date, why, len(stats_model.NODE_DAY) + 1) + "</tr>")
            continue
        got = [stats_model.node_day(n) for n in day.nodes]
        segs = [(s, got.count(s), f"st-{s}", NODE_SAYS[s]) for s in stats_model.NODE_DAY]
        segs.append(("not-asked", day.listed - len(day.nodes), "notasked", "listed, not in the day file"))
        parts.append(stacked(axis, key, i, date, segs, unit))
        rows.append(f'<tr><th scope="row">{when(date)}</th>{td_num(f"{date}|listed", day.listed)}'
                    + "".join(td_num(f"{date}|{s}", n) for s, n, _, _ in segs) + "</tr>")
    parts.append(listed_line(axis, line, unit, "listed"))
    nodes = sum(len(d.nodes) for d in settled)
    title = "Nodes answering, per day"
    desc = (f"Stacked per day: each node asked, once, by how it answered; the line on top is the nodes listed that "
            f"day, the denominator. {len(settled)} of {len(cal)} days have a settled run; the rest are gaps, "
            f"never zero. {nodes} node-days in all.")
    svg_html = svg(key, "multiple", axis.W, axis.H, title, desc, "".join(parts), unit=unit, base=axis.base)
    head = th("Date", "Listed", *(outcome(s) if s != "partial" else "partial" for s in stats_model.NODE_DAY),
              "not asked")
    table = f'<table>{head}<tbody>{"".join(reversed(rows))}</tbody></table>'
    keys = legend([("st-verified", f"{outcome('verified')} to both questions"),
                   ("st-partial", f"partial: {outcome('verified')} to one question only")]
                  + [(f"st-{s}", outcome(s)) for s in stats_model.NODE_DAY[2:]]
                  + [("notasked", "listed, not in the day file"), ("listed-key", "nodes listed")])
    caption = (f"Of the nodes listed each day; {window_words(cal)}. A node with two different answers counts once, "
               "under its better-answered question.")
    return chart(key, "Nodes answering", caption, svg_html, keys, table)


def listed_line(axis, line, unit, series, cls="listed"):
    """A step line of one value per day (None breaks it), each day's value a point."""
    parts, seg = [], []
    for item in line + [None]:
        if item is None:
            if seg:
                d = " ".join(f"{'M' if j == 0 else 'L'}{num(axis.x(i))} {num(axis.base - v * unit)} "
                             f"L{num(axis.x(i) + axis.cw)} {num(axis.base - v * unit)}" for j, (i, v) in enumerate(seg))
                parts.append(f'<path class="{cls}" d="{d}"/>')
            seg = []
            continue
        seg.append(item)
    for item in line:
        if item is None:
            continue
        i, v = item
        date = axis.cal[i][0]
        parts.append(f'<circle class="{cls}-pt" data-k="{date}|{series}" data-v="{v}" cx="{num(axis.mid(i))}" '
                     f'cy="{num(axis.base - v * unit)}" r="2.5"><title>{e(date)}: {v} {series}</title></circle>')
    return "".join(parts)


def sealed_chart(days, cal):
    axis, key = Axis(cal), "c-sealed"
    shown = {d for d, _ in cal}
    points = [p for p in stats_model.sealed_per_day(days) if p.date in shown]
    ymax = nice(max([p.sealed for p in points if p.sealed is not None] + [1]))
    unit = axis.PH / ymax
    parts = [axis.frame(key, ymax, unit, "records")]
    rows, index = [], {d: i for i, (d, _) in enumerate(cal)}
    for p in points:
        i = index[p.date]
        day = cal[i][1]
        if p.sealed is None:
            parts.append(axis.gap(key, i, p.why, "nofile" if day is None else "gap"))
            rows.append(f'<tr><th scope="row">{when(p.date)}</th>{td_gap(p.date, p.why, 3)}</tr>')
            continue
        y = axis.base
        yesterday = (dt.date.fromisoformat(p.date) - dt.timedelta(days=1)).isoformat()
        for part in p.parts:
            h = part.sealed * unit
            y -= h
            longer = part.since != yesterday
            tip = (f"{p.date}: {part.sealed} records sealed by {part.node_id[:12]}…, since its count on {part.since}")
            parts.append(f'<rect class="bar sealed{" longer" if longer else ""}" data-k="{p.date}|{part.node_id[:8]}" '
                         f'data-v="{part.sealed}" x="{num(axis.x(i) + 1)}" y="{num(y)}" '
                         f'width="{num(max(axis.cw - 2, 1))}" height="{num(h)}"><title>{e(tip)}</title></rect>')
            if longer and part.since in index:
                x1 = axis.x(index[part.since] + 1)
                parts.append(f'<path class="bracket" d="M{num(x1 + 1)} {num(y - 3)} L{num(x1 + 1)} {num(y - 7)} '
                             f'L{num(axis.x(i) + axis.cw - 1)} {num(y - 7)} L{num(axis.x(i) + axis.cw - 1)} '
                             f'{num(y - 3)}"><title>{e(p.date)}: the count covers the days since {part.since}'
                             "</title></path>")
            rows.append(f'<tr><th scope="row">{when(p.date)}</th><td>{code(part.node_id[:8])}</td>'
                        f'{td_num(f"{p.date}|{part.node_id[:8]}", part.sealed)}<td>{when(part.since)}</td></tr>')
        if p.sealed == 0:
            parts.append(f'<rect class="zeromark" x="{num(axis.x(i) + 1)}" y="{num(axis.base - 1.5)}" '
                         f'width="{num(max(axis.cw - 2, 1))}" height="1.5"><title>{e(p.date)}: 0 records sealed, '
                         f"from {p.nodes} of {p.listed} listed nodes</title></rect>")
    drops = [(p, nid, since) for p in points for nid, since in p.drops]
    title = "Records sealed per day"
    desc = (f"Bars per day, stacked by node: each node's records at its checkpoint minus its last earlier count. "
            f"A lighter bar under a bracket covers more than one day. {sum(p.sealed is not None for p in points)} "
            f"of {len(points)} days have a value; a missed day, a day with no file, a count that fell or no earlier "
            "count to compare with is a hatched gap, never zero. Live nodes only; rehearsal is never added.")
    svg_html = svg(key, "multiple", axis.W, axis.H, title, desc, "".join(parts), unit=unit, base=axis.base)
    table = (f'<table>{th("Date", "Node", "Records sealed", "Since the count on")}<tbody>'
             f'{"".join(reversed(rows))}</tbody></table>')
    notes = "".join(f"<li>{when(p.date)}: node {short(nid)} holds fewer records at its checkpoint than on "
                    f"{when(since)}. A drop is not drawn as a negative value; the day is a gap.</li>"
                    for p, nid, since in drops)
    keys = legend([("sealed", "records sealed since the day before"),
                   ("sealed longer", "since an older count (bracketed)"), ("gap-key", "gap")])
    if notes:
        keys += f'<ul class="drops">{notes}</ul>'
    caption = (f"Of the listed nodes with an earlier count to compare; {window_words(cal)}. The live nodes on the "
               "commitment list only: rehearsal nodes run only in the loop and are never added here.")
    return chart(key, "Records sealed per day", caption, svg_html, keys, table)


def book_chart(cal):
    axis, key = Axis(cal), "c-book"
    ids = sorted({n.node_id for _, d in cal if d for n in d.nodes})
    sizes = [n.answers["checkpoints"].log_size["mmr_size"] for _, d in cal if d for n in d.nodes
             if n.answers["checkpoints"].log_size]
    ymax = nice(max(sizes + [1]))
    unit = axis.PH / ymax
    parts = [axis.frame(key, ymax, unit, "log size (mmr_size)")]
    for i, (date, day) in enumerate(cal):
        why = day_gap(day)
        if why:
            parts.append(axis.gap(key, i, why, "gap" if day else "nofile"))
    cells = {}
    for nid in ids:
        seg, segs = [], []
        for i, (date, day) in enumerate(cal):
            node = next((n for n in day.nodes if n.node_id == nid), None) if day and not day.missed else None
            a = node.answers["checkpoints"] if node else None
            if a and a.log_size:
                v = a.log_size["mmr_size"]
                seg.append((i, v, a.log_size["timestamp"]))
                cells[(date, nid)] = ("v", v)
                continue
            if seg:
                segs.append(seg)
            seg = []
            if a and a.state == "no_checkpoint_yet":
                cells[(date, nid)] = ("gap", "no checkpoint yet")
                parts.append(f'<line class="nocheckpoint" data-k="{date}|{nid[:8]}" data-gap="no checkpoint yet" '
                             f'x1="{num(axis.x(i) + 1)}" y1="{num(axis.base - 2)}" x2="{num(axis.x(i) + axis.cw - 1)}" '
                             f'y2="{num(axis.base - 2)}"><title>{e(date)} · {nid[:12]}…: no checkpoint yet</title>'
                             "</line>")
            elif day and not day.missed:
                cells[(date, nid)] = ("none", "not listed" if node is None else f"no size: {a.state}")
        if seg:
            segs.append(seg)
        for s in segs:
            if len(s) > 1:
                d = " ".join(f"{'M' if j == 0 else 'L'}{num(axis.mid(i))} {num(axis.base - v * unit)}"
                             for j, (i, v, _) in enumerate(s))
                parts.append(f'<path class="book" d="{d}"/>')
            for i, v, stamp in s:
                parts.append(f'<circle class="book-pt" data-k="{cal[i][0]}|{nid[:8]}" data-v="{v}" '
                             f'cx="{num(axis.mid(i))}" cy="{num(axis.base - v * unit)}" r="2.5"><title>'
                             f"{e(cal[i][0])} · {nid[:12]}…: mmr_size {v} at {e(stamp)}</title></circle>")
        if segs:
            i, v, _ = segs[-1][-1]
            parts.append(f'<text class="code end" x="{num(axis.mid(i) - 4)}" y="{num(axis.base - v * unit - 6)}">'
                         f"{nid[:8]}</text>")
    rows = []
    for date, day in reversed(cal):
        why = day_gap(day)
        if why:
            rows.append(f'<tr><th scope="row">{when(date)}</th>{td_gap(date, why, max(len(ids), 1))}</tr>')
            continue
        tds = []
        for nid in ids:
            kind, v = cells.get((date, nid), ("none", "not listed"))
            k = f"{date}|{nid[:8]}"
            tds.append(td_num(k, v) if kind == "v" else td_gap(k, v) if kind == "gap" else f"<td>{e(v)}</td>")
        rows.append(f'<tr><th scope="row">{when(date)}</th>{"".join(tds)}</tr>')
    title = "Book growth per node"
    desc = (f"One line per node: the log size (mmr_size) at its newest checkpoint, as the reporter read it each "
            f"day, for {len(ids)} nodes. A node with no checkpoint yet is a dashed line on the axis, labelled so; "
            "a missed day or a day with no file is a hatched gap.")
    svg_html = svg(key, "multiple", axis.W, axis.H, title, desc, "".join(parts), unit=unit, base=axis.base)
    table = (f'<table>{th("Date", *(code(n[:8]) for n in ids))}<tbody>{"".join(rows)}</tbody></table>')
    keys = legend([("book-key", "a node's log size; the line ends in the first eight hex digits of its id"),
                   ("nocheckpoint-key", "no checkpoint yet (dashed, on the axis)")])
    caption = f"Log size per node, not added across nodes; {window_words(cal)}."
    return chart(key, "Book growth per node", caption, svg_html, keys, table)


def asks_chart(cal):
    axis, key = Axis(cal), "c-asks"
    ymax = nice(max([2 * d.listed for _, d in cal if d] + [1]))
    unit = axis.PH / ymax
    parts = [axis.frame(key, ymax, unit, "asks")]
    rows, line = [], []
    asked = 0
    for i, (date, day) in enumerate(cal):
        why = day_gap(day)
        line.append(None if why else (i, 2 * day.listed))
        if why:
            parts.append(axis.gap(key, i, why, "gap" if day else "nofile"))
            rows.append(f'<tr><th scope="row">{when(date)}</th><td>none</td>{td_gap(date, why, len(ASKS))}</tr>')
            continue
        states = [a.state for n in day.nodes for a in n.answers.values()]
        n = {"answered": sum(s in ANSWERED for s in states), "refused": states.count("refusal"),
             "absent": states.count("absence"), "not-asked": 2 * (day.listed - len(day.nodes))}
        asked += 2 * day.listed
        parts.append(stacked(axis, key, i, date, [(s, n[s], f"ask-{s}", w) for s, w in ASKS], unit))
        rows.append(f'<tr><th scope="row">{when(date)}</th>{td_num(f"{date}|asks", 2 * day.listed)}'
                    + "".join(td_num(f"{date}|{s}", n[s]) for s, _ in ASKS) + "</tr>")
    parts.append(listed_line(axis, line, unit, "asks"))
    title = "Asks per day: answered, refused, absent"
    desc = (f"Stacked per day: the asks, two per listed node, by outcome; the line on top is the asks that day, "
            f"the denominator. {asked} asks over the settled days shown. A missed day or a day with no file is a "
            "hatched gap, never zero.")
    svg_html = svg(key, "multiple", axis.W, axis.H, title, desc, "".join(parts), unit=unit, base=axis.base)
    table = (f'<table>{th("Date", "Asks", "answered", "refused", "absent", "not asked")}<tbody>'
             f'{"".join(reversed(rows))}</tbody></table>')
    keys = legend([("ask-answered", f"answered: {', '.join(outcome(s) for s in ANSWERED)}"),
                   ("ask-refused", f"refused: {outcome('refusal')}"), ("ask-absent", f"absent: {outcome('absence')}"),
                   ("notasked", "listed, not in the day file"), ("listed-key", "asks that day")])
    caption = f"Of the asks each day, two per listed node; {window_words(cal)}."
    return chart(key, "Asks: answered, refused, absent", caption, svg_html, keys, table)


def missed_chart(cal):
    axis, key = Axis(cal), "c-missed"
    axis.PH = 22
    axis.base = axis.TOP + axis.PH
    axis.H = axis.base + axis.BOTTOM
    parts = [axis.frame(key, None, None, None)]
    rows = []
    for i, (date, day) in enumerate(cal):
        kind = "no day file" if day is None else "missed" if day.missed else "settled"
        cls = {"no day file": "nofile", "missed": "gap", "settled": "settled"}[kind]
        tip = f"{date}: " + (f"missed, {day.missed_reason}" if day and day.missed else kind)
        fill = f' fill="url(#{key}-h)"' if kind == "missed" else ""
        parts.append(f'<rect class="{cls}" data-k="{date}" data-v="{kind}" x="{num(axis.x(i) + 1)}" y="{axis.TOP}" '
                     f'width="{num(max(axis.cw - 2, 1))}" height="{axis.PH}"{fill}><title>{e(tip)}</title></rect>')
        reason = code(day.missed_reason) if day and day.missed else ""
        rows.append(f'<tr><th scope="row">{when(date)}</th>{td_text(date, kind)}<td>{reason}</td></tr>')
    missed = sum(1 for _, d in cal if d and d.missed)
    nofile = sum(1 for _, d in cal if d is None)
    title = "Days missed"
    desc = (f"One cell per day: {missed} of {len(cal)} days missed (hatched, the reason on hover), {nofile} of "
            f"{len(cal)} with no day file (dashed), the rest settled.")
    svg_html = svg(key, "multiple strip", axis.W, axis.H, title, desc, "".join(parts))
    table = f'<table>{th("Date", "Run", "Reason")}<tbody>{"".join(reversed(rows))}</tbody></table>'
    keys = legend([("settled", "settled"), ("gap-key", "missed"), ("nofile", "no day file")])
    caption = (f"{count(missed, len(cal), 'day')} missed and {count(nofile, len(cal), 'day')} with no day file; "
               f"{window_words(cal)}. Never a rate.")
    return chart(key, "Days missed", caption, svg_html, keys, table)


def listed_chart(days, cal):
    axis, key = Axis(cal), "c-listed"
    ymax = nice(max([d.listed for _, d in cal if d] + [1]))
    unit = axis.PH / ymax
    parts = [axis.frame(key, ymax, unit, "nodes listed")]
    line, rows, events = [], [], []
    first, left_on = stats_model.first_seen(days), stats_model.left_on(days)
    start = stats_model.calendar(days)[0][0] if days else None
    for i, (date, day) in enumerate(cal):
        line.append((i, day.listed) if day else None)
        if day is None:
            parts.append(axis.gap(key, i, "no day file", "nofile"))
            rows.append(f'<tr><th scope="row">{when(date)}</th>{td_gap(date, "no day file")}</tr>')
        else:
            rows.append(f'<tr><th scope="row">{when(date)}</th>{td_num(f"{date}|listed", day.listed)}</tr>')
        ticks = [("join", n) for n in sorted(n for n, d in first.items() if d == date and d != start)]
        ticks += [("leave", n) for n in sorted(n for n, d in left_on.items() if d == date)]
        for j, (kind, nid) in enumerate(ticks):
            x, y = axis.mid(i), axis.TOP + 4 + j * 9
            d = (f"M{num(x - 4)} {num(y + 7)} L{num(x + 4)} {num(y + 7)} L{num(x)} {num(y)} Z" if kind == "join" else
                 f"M{num(x - 4)} {num(y)} L{num(x + 4)} {num(y)} L{num(x)} {num(y + 7)} Z")
            verb = "joined" if kind == "join" else "left"
            parts.append(f'<path class="tick-{kind}" data-k="{date}|{kind}|{nid[:8]}" data-v="{verb}" d="{d}">'
                         f"<title>{e(date)}: {nid[:12]}… {verb}</title></path>")
            events.append(f'<tr><th scope="row">{when(date)}</th><td>{code(nid[:8])}</td>'
                          f"{td_text(f'{date}|{kind}|{nid[:8]}', verb)}</tr>")
    parts.append(listed_line(axis, line, unit, "listed"))
    title = "Nodes listed, joining and leaving"
    desc = (f"A step line of the nodes on the commitment list each day, with a triangle up the first day a node "
            f"is in a day file and a triangle down the day it left. {len(events)} joins and leaves in the window; the "
            "first day of the record marks no joins. A day with no file is a gap.")
    svg_html = svg(key, "multiple", axis.W, axis.H, title, desc, "".join(parts), unit=unit, base=axis.base)
    table = (f'<table>{th("Date", "Listed")}<tbody>{"".join(reversed(rows))}</tbody></table>'
             + (f'<table>{th("Date", "Node", "Event")}<tbody>{"".join(reversed(events))}</tbody></table>'
                if events else "<p>No node joined or left in the window.</p>"))
    keys = legend([("listed-key", "nodes listed"), ("join-key", "joined (first day in a day file)"),
                   ("leave-key", "left")])
    caption = f"Nodes on the commitment list each day, a count of nodes, not a total over days; {window_words(cal)}."
    return chart(key, "Nodes listed, joining and leaving", caption, svg_html, keys, table)


def multiples_section(days):
    cal = stats_model.window(days)[1]
    if not cal:
        return "<p>No day yet. The first scrubbed day file draws the first column here.</p>"
    return ('<div class="multiples">' + answering_chart(cal) + sealed_chart(days, cal) + book_chart(cal)
            + asks_chart(cal) + missed_chart(cal) + listed_chart(days, cal) + "</div>")


# The loop

def runs_of(findings):
    """Every run under findings, per population, newest first."""
    root, out = Path(findings), {p: [] for p in status_page.POPULATIONS}
    for d in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
        m = status_page.RUN_DIR.match(d.name)
        if m and d.is_dir():
            out[m.group(2)].append(status_page.read_run(m.group(2), d))
    return out


def median(values):
    v = sorted(values)
    return (v[len(v) // 2] + v[(len(v) - 1) // 2]) / 2


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


def tally(cells):
    labels = [c.label for c in cells]
    return "; ".join(f"{code(lab)} {count(labels.count(lab), len(cells), 'step')}" for lab in TALLY)


def gaps_list(rs, population):
    """Each PASS step's gaps: what its finding says lies outside its assertion, with the issue linked."""
    items = [f'<li><a href="{STATUS}/#step-{row.step.n:02d}">{code(f"{row.step.n:02d}")} '
             f"{e(row.step.name)}</a>: gap, outside the assertion: <q>{e(g.text)}</q> issue: "
             f'<a href="{e(g.url)}">{e(g.issue)}</a></li>'
             for row in rs for g in row.cells[population].gaps]
    if not items:
        return ""
    return f'<p class="note">A ringed step passed with a gap its finding names:</p><ul class="gaps">{"".join(items)}</ul>'


def strip(rs, population):
    """The twelve steps as tiles in a row, each in its state, linking to its status row."""
    tiles = []
    for row in rs:
        c, s = row.cells[population], row.step
        tip = (f"{s.n:02d} {s.name}, {population}: {c.label}"
               + "".join(f". Gap: {g.text} ({g.issue})" for g in c.gaps)
               + (f". {status_page.HELD_NOTE}" if row.held else ""))
        cls = f"tile s-{c.state} l-{c.label}" + (" held" if row.held else "") + (" ringed" if c.gaps else "")
        gaps = f' data-gaps="{len(c.gaps)}"' if c.gaps else ""
        tiles.append(f'<li><a class="{cls}" href="{STATUS}/#step-{s.n:02d}" data-step="{s.n}" '
                     f'data-label="{e(c.label)}"{gaps} title="{e(tip)}"><span class="n code">{s.n:02d}</span>'
                     f'<span class="name">{e(s.name)}</span><span class="lab code">{e(c.label)}</span></a></li>')
    return f'<ol class="strip">{"".join(tiles)}</ol>'


def passes_spark(population, runs, steps):
    """PASS steps per run, oldest to newest, of the steps shown; one point per run."""
    key = f"s-pass-{population}"
    rs = list(reversed(runs[:RUNS]))
    w, h, left, right, top, bottom = 300, 72, 30, 34, 10, 20
    if not rs:
        body = (f'<rect class="frame" x="0.5" y="0.5" width="{w - 1}" height="{h - 1}"/>'
                f'<text class="none" x="{w / 2}" y="{h / 2 + 4}">no run yet</text>')
        return svg(key, "spark", w, h, f"PASS steps per {population} run", f"No {population} run yet.", body,
                   fixed=True), "<p>No run yet.</p>"
    n = len(steps)
    unit = (h - top - bottom) / n
    base = h - bottom
    step_x = (w - left - right) / max(len(rs) - 1, 1)
    x = lambda i: left + (i * step_x if len(rs) > 1 else (w - left - right) / 2)  # noqa: E731
    vals = []
    for r in rs:
        cells = [status_page.cell(s, r) for s in steps]
        vals.append(sum(c.label == "PASS" for c in cells))
    parts = [f'<line class="axis" x1="{left}" y1="{base}" x2="{w - right}" y2="{base}"/>',
             f'<line class="grid" x1="{left}" y1="{top}" x2="{w - right}" y2="{top}"/>',
             f'<text class="code tick" x="{left - 6}" y="{top + 4}">{n}</text>',
             f'<text class="code tick" x="{left - 6}" y="{base + 4}">0</text>']
    if len(rs) > 1:
        parts.append('<path class="spark" d="' + " ".join(
            f"{'M' if i == 0 else 'L'}{num(x(i))} {num(base - v * unit)}" for i, v in enumerate(vals)) + '"/>')
    for i, (r, v) in enumerate(zip(rs, vals)):
        parts.append(f'<circle class="spark-pt" data-k="{r.name}" data-v="{v}" cx="{num(x(i))}" '
                     f'cy="{num(base - v * unit)}" r="3"><title>{e(r.name)}: {v} of {n} steps PASS</title></circle>')
    parts.append(f'<text class="code end-value" x="{num(x(len(rs) - 1) + 8)}" y="{num(base - vals[-1] * unit + 4)}">'
                 f"{vals[-1]}</text>")
    parts.append(f'<text class="code xdate" x="{left}" y="{h - 4}">{e(rs[0].name[5:10])}</text>')
    if len(rs) > 1:
        parts.append(f'<text class="code xdate newest" x="{num(x(len(rs) - 1))}" y="{h - 4}">'
                     f"{e(rs[-1].name[5:10])}</text>")
    desc = (f"{len(rs)} {population} runs, oldest on the left. Each point is the steps whose finding is PASS in "
            f"that run, of the {n} steps shown; the newest run has {vals[-1]} of {n}.")
    body = "".join(parts)
    table = (f'<table>{th("Run", "PASS", "Of steps")}<tbody>'
             + "".join(f'<tr><th scope="row">{code(r.name)}</th>{td_num(r.name, v)}<td class="num">{n}</td></tr>'
                       for r, v in reversed(list(zip(rs, vals)))) + "</tbody></table>")
    return svg(key, "spark", w, h, f"PASS steps per {population} run", desc, body, fixed=True, unit=unit,
               base=base), table


def loop_section(rs, runs, all_runs):
    steps = [row.step for row in rs]
    figures = []
    for p in status_page.POPULATIONS:
        run = runs[p]
        source = f"the newest run, {code(run.name)}" if run.path else "no run yet"
        cells = [row.cells[p] for row in rs]
        spark, table = passes_spark(p, all_runs[p], steps)
        n_runs = len(all_runs[p][:RUNS])
        figures.append(
            f'<figure class="pop" data-population="{p}"><figcaption><strong>{p.capitalize()}</strong>: {source}. '
            f"{tally(cells)}.</figcaption>{strip(rs, p)}{gaps_list(rs, p)}"
            f'<div class="spark-row"><p class="caption">Steps that passed per run, of the steps shown; '
            f"{count(n_runs, n_runs, 'run') if n_runs else 'no run yet'}, up to the newest thirty.</p>"
            f'<div class="scroll">{spark}</div><details><summary>The numbers</summary><div class="scroll">{table}'
            "</div></details></div></figure>")
    held = [row.step for row in rs if row.held]
    note = ""
    if held:
        note = (f'<p class="note">Dashed: {", ".join(code(f"{s.n:02d}") for s in held)}. '
                f"{e(status_page.HELD_NOTE)}</p>")
    return "".join(figures) + note


# The five numbers

def number_text(nb, run):
    """status_page's number in the newest run, out of the run's own length; a placeholder until it can be
    measured."""
    text, measured = status_page.number_value(nb, run)
    if not measured:
        if text == f"not measured yet ({nb.milestone})":
            return f"not measured yet ({code(nb.milestone)})"
        return e(text).replace("(run.json)", f"({code('run.json')})")
    m, total = status_page.minutes(run, nb.step), run_minutes(run)
    part, whole = status_page.duration(m), status_page.duration(total)
    if part.endswith(" minutes"):
        part = part.removesuffix(" minutes")  # "1.0 of the run's 3.0 minutes"
    ask = status_page.unsealed_ask(run, nb.step) if nb.step == status_page.ASK_STEP else None
    note = f" ({e(ask)}, answered but not sealed before it was sent)" if ask else ""
    return f'<span class="count">{e(part)} of the run\'s {e(whole)}</span>{note}'


def number_spark(nb, population, runs):
    """One number over a population's runs: a point per run where measured; a dotted track with its milestone
    where it never was. For the first sealed record, the median over the window as a short bar."""
    key = f"n-{nb.key}-{population}"
    rs = list(reversed(runs[:RUNS]))
    w, h, left, right, top, bottom = 300, 56, 30, 34, 8, 18
    base = h - bottom
    if not rs:
        body = (f'<rect class="frame" x="0.5" y="0.5" width="{w - 1}" height="{h - 1}"/>'
                f'<text class="none" x="{w / 2}" y="{h / 2 + 4}">no run yet</text>')
        return svg(key, "spark", w, h, f"{nb.label}, {population}", f"No {population} run yet.", body,
                   fixed=True), "<p>No run yet.</p>", None
    vals = [status_page.minutes(r, nb.step) if nb.step and r.path else None for r in rs]
    step_x = (w - left - right) / max(len(rs) - 1, 1)
    x = lambda i: left + (i * step_x if len(rs) > 1 else (w - left - right) / 2)  # noqa: E731
    got = [v for v in vals if v is not None]
    rows = []
    if not got:
        parts = [f'<line class="track" x1="{left}" y1="{base}" x2="{w - right}" y2="{base}"/>',
                 f'<text class="code milestone" x="{left}" y="{base - 10}">not measured yet ({nb.milestone})</text>']
        for i, r in enumerate(rs):
            why = status_page.number_value(nb, r)[0]
            parts.append(f'<circle class="unmeasured" data-k="{r.name}" data-gap="{e(why)}" cx="{num(x(i))}" '
                         f'cy="{base}" r="2.5"><title>{e(r.name)}: {e(why)}</title></circle>')
            rows.append(f'<tr><th scope="row">{code(r.name)}</th>{td_gap(r.name, why)}</tr>')
        desc = (f"{len(rs)} {population} runs; the number is not measured yet in any of them and waits for "
                f"{nb.milestone}. No value is drawn.")
        return (svg(key, "spark", w, h, f"{nb.label}, {population}", desc, "".join(parts), fixed=True),
                f'<table>{th("Run", "Minutes")}<tbody>{"".join(reversed(rows))}</tbody></table>', None)
    ymax = nice(max(got))
    unit = (base - top) / ymax
    parts = [f'<line class="axis" x1="{left}" y1="{base}" x2="{w - right}" y2="{base}"/>',
             f'<text class="code tick" x="{left - 6}" y="{top + 4}">{ymax}</text>',
             f'<line class="grid" x1="{left}" y1="{top}" x2="{w - right}" y2="{top}"/>']
    seg = []
    for i, v in enumerate(vals + [None]):
        if v is None:
            if len(seg) > 1:
                parts.append('<path class="spark" d="' + " ".join(
                    f"{'M' if j == 0 else 'L'}{num(x(k))} {num(base - u * unit)}" for j, (k, u) in enumerate(seg))
                    + '"/>')
            seg = []
        else:
            seg.append((i, v))
    for i, (r, v) in enumerate(zip(rs, vals)):
        if v is None:
            why = status_page.number_value(nb, r)[0]
            parts.append(f'<circle class="unmeasured" data-k="{r.name}" data-gap="{e(why)}" cx="{num(x(i))}" '
                         f'cy="{base}" r="2.5"><title>{e(r.name)}: {e(why)}</title></circle>')
            rows.append(f'<tr><th scope="row">{code(r.name)}</th>{td_gap(r.name, why)}</tr>')
            continue
        parts.append(f'<circle class="spark-pt" data-k="{r.name}" data-v="{num(v)}" cx="{num(x(i))}" '
                     f'cy="{num(base - v * unit)}" r="3"><title>{e(r.name)}: {e(status_page.duration(v))}'
                     "</title></circle>")
        rows.append(f'<tr><th scope="row">{code(r.name)}</th>{td_num(r.name, v)}</tr>')
    med = None
    if nb.key == "first-sealed":
        newest = rs[-1].name[:10]
        start = (dt.date.fromisoformat(newest) - dt.timedelta(days=WINDOW_DAYS - 1)).isoformat()
        inside = [(i, v) for i, (r, v) in enumerate(zip(rs, vals)) if r.name[:10] >= start]
        window = [v for _, v in inside if v is not None]
        if window:
            m = median(window)
            x1, x2 = x(inside[0][0]) - 6, x(inside[-1][0]) + 6
            parts.append(f'<line class="median" data-k="median" data-v="{num(m)}" x1="{num(x1)}" '
                         f'y1="{num(base - m * unit)}" x2="{num(x2)}" y2="{num(base - m * unit)}"><title>median '
                         f"of {len(window)} of {len(inside)} runs from {start} to {newest}: "
                         f"{e(status_page.duration(m))}</title></line>")
            rows.insert(0, f'<tr><th scope="row">median, {when(start)} to {when(newest)}</th>{td_num("median", m)}'
                           "</tr>")
            med = (m, len(window), len(inside), start, newest)
    desc = (f"{len(rs)} {population} runs, oldest on the left: the minutes from the run's start where measured, "
            f"{len(got)} of {len(rs)} runs; a run where it is not measured is a hollow mark on the axis, never zero."
            + (f" The bar is the median over the runs from {med[3]} to {med[4]}." if med else ""))
    table = f'<table>{th("Run", "Minutes")}<tbody>{"".join(reversed(rows))}</tbody></table>'
    return (svg(key, "spark", w, h, f"{nb.label}, {population}", desc, "".join(parts), fixed=True, unit=unit,
                base=base), table, med)


def numbers_section(runs, all_runs):
    rows = []
    for nb in status_page.NUMBERS:
        cells = []
        for p in status_page.POPULATIONS:
            spark, table, med = number_spark(nb, p, all_runs[p])
            median_line = ""
            if med:
                median_line = (f'<p class="note">median {value(status_page.duration(med[0]))} of '
                               f"{count(med[1], med[2], 'run')} from {when(med[3])} to {when(med[4])}</p>")
            cells.append(f'<div class="num-cell" data-population="{p}"><p><strong>{p.capitalize()}</strong>: '
                         f"{number_text(nb, runs[p])}</p>{median_line}<div class=\"scroll\">{spark}</div>"
                         f"<details><summary>The numbers</summary><div class=\"scroll\">{table}</div></details></div>")
        rows.append(f'<section class="number" id="n-{nb.key}"><h3>{e(nb.label)}</h3><p class="caption">'
                    f"{e(nb.unit)}, per run; due {code(nb.milestone)}</p>"
                    f'<div class="pair">{"".join(cells)}</div></section>')
    halves = ('<section class="number" id="n-halves"><h3>Exchanges with both halves held</h3><p class="caption">'
              f"A count of exchanges, out of the exchanges seen: not measured yet ({code('M2')}). It has no source "
              f"until step {code('05')} (Deal and push) has findings, and it is never shown as zero.</p></section>")
    return "".join(rows) + halves


# The nodes, day by day

def node_ids(days):
    return sorted({n.node_id for d in days for n in d.nodes})


def heatmap(days, cal):
    ids = node_ids([d for _, d in cal if d])
    left = stats_model.left_on(days)
    key, label_w, cell, top, row_h, mark_h = "heatmap", 132, 26, 74, 40, 14
    width, height = label_w + cell * len(cal) + 8, top + row_h * len(ids) + 4
    parts = [hatch(key),
             f'<text class="key" x="0" y="{top - 30}">upper: <tspan class="code">checkpoints</tspan></text>',
             f'<text class="key" x="0" y="{top - 12}">lower: <tspan class="code">history_card/1</tspan></text>']
    for i, (date, _) in enumerate(cal):
        x = label_w + i * cell + cell / 2
        if (len(cal) - 1 - i) % 2 == 0:
            parts.append(f'<text class="code date" transform="translate({num(x + 4)} {top - 6}) rotate(-90)">'
                         f"{e(date[5:])}</text>")
    for j, nid in enumerate(ids):
        y = top + j * row_h
        parts.append(f'<g class="node" data-node="{e(nid)}"><text class="code id" x="0" y="{y + 15}">'
                     f"{e(nid[:12])}…<title>{e(nid)}</title></text>")
        if nid in left:
            parts.append(f'<text class="leftlabel" x="0" y="{y + 32}">left <tspan class="code">'
                         f"{e(left[nid][5:])}</tspan></text>")
        for i, (date, day) in enumerate(cal):
            x = label_w + i * cell
            node = next((n for n in day.nodes if n.node_id == nid), None) if day else None
            if day is None or day.missed:
                why = day_gap(day)
                cls = "nofile" if day is None else "gap"
                fill = f' fill="url(#{key}-h)"' if day else ""
                parts.append(f'<rect class="{cls}" data-k="{nid[:8]}|{date}" data-gap="{why}" x="{x + 2}" '
                             f'y="{y + 2}" width="{cell - 4}" height="{2 * mark_h + 2}"{fill}>'
                             f"<title>{e(date)} · {nid[:12]}…: {why}</title></rect>")
            elif node is None:
                parts.append(f'<rect class="out" x="{x + 2}" y="{y + 2}" width="{cell - 4}" height="{2 * mark_h + 2}">'
                             f"<title>{e(date)} · {nid[:12]}…: not asked, not on the list this day</title></rect>")
            else:
                for k, stem in enumerate(stats_model.QUESTIONS):
                    state = node.answers[stem].state
                    tip = f"{date} · {nid[:12]}… · {stats_model.QUESTIONS[stem]}: {SAYS[state]}"
                    parts.append(f'<rect class="st-{state}" data-k="{nid[:8]}|{date}|{stem}" data-v="{state}" '
                                 f'x="{x + 2}" y="{y + 2 + k * (mark_h + 2)}" width="{cell - 4}" height="{mark_h}">'
                                 f"<title>{e(tip)}</title></rect>")
            if left.get(nid) == date:
                parts.append(f'<line class="leftmark" x1="{x}" y1="{y - 2}" x2="{x}" y2="{y + 2 * mark_h + 8}"/>')
        parts.append("</g>")
    desc = (f"{len(ids)} nodes on rows, by node id (the order means nothing), and {len(cal)} days on columns, "
            "newest on the right. Each day is two marks, checkpoints above and history_card/1 below, coloured by "
            "day state; a missed day is hatched, a day with no file dashed, a day the node was not listed empty, "
            "and a line marks the day a node left.")
    return svg(key, "heatmap", width, height, "Each node, each day, by day state", desc, "".join(parts),
               fixed=True), ids


def attempts(answer):
    n = len(answer.attempts)
    items = "".join(f"<li>{when(a.at)} {outcome(a.state)}</li>" for a in answer.attempts)
    return f'asked {WORDS.get(n, code(n) + " times")}<ul class="attempts">{items}</ul>'


def answer_cell(a, k):
    parts = [f'<p data-k="{e(k)}">{outcome(a.state)}</p>', "<dl>"]
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


def node_table(nid, cal):
    rows = []
    for date, day in reversed(cal):
        node = next((n for n in day.nodes if n.node_id == nid), None) if day else None
        if day is None or day.missed:
            why = day_gap(day)
            rows.append(f'<tr><th scope="row">{when(date)}</th>{td_gap(f"{nid[:8]}|{date}", why, 3)}</tr>')
            continue
        if node is None:
            rows.append(f'<tr><th scope="row">{when(date)}</th><td colspan="3">not asked: not on the list this day'
                        "</td></tr>")
            continue
        fact = stats_model.layout.absence_fact(node.absent_running)
        says = code(node.node_line) + (f'<br>{code(fact, "fact")}' if fact else "")
        if node.left_on:
            says += f"<br>left on {when(node.left_on)}"
        rows.append(f'<tr><th scope="row">{when(date)}</th><td>{says}</td>'
                    + "".join(answer_cell(node.answers[stem], f"{nid[:8]}|{date}|{stem}")
                              for stem in stats_model.QUESTIONS) + "</tr>")
    head = th("Date", "Node says", code("checkpoints"), code("history_card/1"))
    return f'<table class="node-table">{head}<tbody>{"".join(rows)}</tbody></table>'


def nodes_section(days):
    if not days:
        return "<p>No day yet. The first scrubbed day file draws the first column here.</p>"
    cal = stats_model.window(days)[1]
    n = len(cal)
    settled = sum(1 for _, d in cal if d and not d.missed)
    missed = sum(1 for _, d in cal if d and d.missed)
    svg_html, ids = heatmap(days, cal)
    tables = "".join(f'<details class="node-detail" data-node="{e(nid)}"><summary>Node {short(nid)}</summary>'
                     f'<div class="scroll">{node_table(nid, cal)}</div></details>' for nid in ids)
    keys = state_legend([("out", "not on the list that day"), ("gap-key", "missed day (hatched)"),
                         ("nofile", "no day file (dashed)")])
    return (f'<figure class="chart wide" id="f-heatmap"><figcaption><p class="caption">From {when(cal[0][0])} to '
            f"{when(cal[-1][0])}: {count(settled, n, 'day')} with a settled run, {count(missed, n, 'day')} missed, "
            f"{count(n - settled - missed, n, 'day')} with no day file. One row per node, by node id; the order "
            f"means nothing.</p></figcaption><div class=\"scroll\">{svg_html}</div>{keys}{tables}</figure>")


def rehearsal_link(slots):
    """The daily page's one line about the rehearsal: a TEST label and a link, never its rows."""
    if not slots:
        return ""
    return ('\n<p class="note"><strong>TEST</strong>: the reporter rehearsal, a test of the daily run every 10 '
            'minutes, is on <a href="rehearsal/">its own page</a>, apart from the daily rows here.</p>')


# The rehearsal page's own rules, after the daily page's: the TEST label and MISSED stand out in both schemes.
REHEARSAL_STYLE = """<style>
.test { display: inline-block; border: 2px solid var(--s-red); color: var(--fg); font-weight: 700;
        padding: 0 .4em; border-radius: 3px; letter-spacing: .05em; }
td.missed { background: var(--o-none); font-weight: 600; }
</style>"""


def slot_cell(node):
    states = " / ".join(code(node.answers[stem].state, f"outcome o-{node.answers[stem].state}")
                        for stem in stats_model.QUESTIONS)
    return f'<td class="o-{stats_model.node_day(node)}">{short(node.node_id)} {states}</td>'


def slot_row(slot, day):
    when_ = f'<td>{when(slot)}</td>'
    if day is None:
        return (f'<tr data-slot="{e(slot)}">{when_}<td class="missed">MISSED</td>'
                '<td colspan="2">no report and no MISSED mark for this slot</td></tr>')
    if day.missed:
        return (f'<tr data-slot="{e(slot)}">{when_}<td class="missed">MISSED</td>'
                f'<td colspan="2">the reporter says {code(day.missed_reason)}</td></tr>')
    answered = sum(1 for n in day.nodes if all(a.state in stats_model.layout.ANSWERED for a in n.answers.values()))
    cells = "".join(slot_cell(n) for n in day.nodes) or "<td>no node is listed</td>"
    return (f'<tr data-slot="{e(slot)}">{when_}<td>settled</td><td>{count(answered, day.listed)} answered both '
            f'questions</td><td><table class="slot-nodes"><tr>{cells}</tr></table></td></tr>')


def render_rehearsal(slots):
    """The rehearsal page: TEST, one row per 10-minute slot, newest first; MISSED where no run settled."""
    head = (HERE / "template.html").read_text(encoding="utf-8").split("</head>", 1)[0]
    head = head.replace("<title>Loop stats</title>", "<title>Loop stats: TEST reporter rehearsal</title>")
    cal = stats_model.slots(slots)
    settled = sum(1 for _, d in cal if d is not None and not d.missed)
    rows = "\n".join(slot_row(slot, day) for slot, day in reversed(cal))
    return f"""{head}{REHEARSAL_STYLE}
</head>
<body>
<main>
<h1><span class="test">TEST</span> Reporter rehearsal</h1>
<p class="lede"><strong>TEST data.</strong> The daily reporter's run, rehearsed every 10 minutes: the same questions to
the same nodes, one report per 10-minute UTC slot. These rows are not the daily set, are never added to it, and are
removed when the rehearsal ends. The daily rows are on <a href="../">the stats page</a>.</p>
<p class="today">{count(settled, len(cal), 'slot')} with a settled run, from {when(cal[0][0])} to {when(cal[-1][0])}
(UTC). A slot with no settled run reads MISSED, as a missed day does.</p>
<table class="rehearsal">
<caption><span class="test">TEST</span> One row per 10-minute slot, newest first. Nodes by node id; the order
means nothing.</caption>
<tr><th scope="col">slot (UTC)</th><th scope="col">run</th><th scope="col">nodes</th>
<th scope="col">each node: checkpoints / history_card/1</th></tr>
{rows}
</table>
</main>
</body>
</html>
"""


def render(days, rs, runs, all_runs, slots=()):
    template = Template((HERE / "template.html").read_text(encoding="utf-8"))
    return template.substitute(badge_alt=e(status_page.badge_label(rs)), rehearsal=rehearsal_link(slots),
                               pulse=pulse_section(days),
                               multiples=multiples_section(days), loop=loop_section(rs, runs, all_runs),
                               numbers=numbers_section(runs, all_runs), nodes=nodes_section(days))


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


def build(data, findings, lock, out, rehearsal=None):
    days = stats_model.load(data)  # raise before anything is written
    slots = []
    if rehearsal is not None and Path(rehearsal).is_dir():
        slots = stats_model.load(rehearsal, stats_model.layout.Rehearsal)
    runs = {p: status_page.read_run(p, d) for p, d in status_page.latest_runs(findings).items()}
    history = runs_of(findings)  # reads every run too, so a refused one raises before anything is written
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
    (site / "index.html").write_text(render(days, rs, runs, history, slots), encoding="utf-8")
    if slots:
        (site / REHEARSAL_DATA).mkdir(parents=True)
        for d in slots:
            shutil.copyfile(Path(rehearsal) / d.file, site / REHEARSAL_DATA / d.file)
        (site / REHEARSAL).mkdir()
        (site / REHEARSAL / "index.html").write_text(render_rehearsal(slots), encoding="utf-8")
    (out / "index.html").write_text(ROOT_PAGE, encoding="utf-8")
    return days


def main(argv):
    args = argv[1:]
    opts = {"--out": "_site", "--findings": "findings", "--lock": "versions.lock", "--rehearsal": None}
    for opt in opts:
        if opt in args:
            i = args.index(opt)
            opts[opt] = args[i + 1]
            del args[i:i + 2]
    data = Path(args[0] if args else "stats/data/daily")
    try:
        days = build(data, opts["--findings"], opts["--lock"], opts["--out"], opts["--rehearsal"])
    except (stats_model.StatsError, status_page.StatusError) as err:
        for p in err.problems:
            print(p, file=sys.stderr)
        print(f"{data}: the stats site was not built", file=sys.stderr)
        return 1
    print(f"{opts['--out']}/{SITE}/index.html: {len(days)} day{'' if len(days) == 1 else 's'} from {data}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
