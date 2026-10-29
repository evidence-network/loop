// The watch page: the network as a graph, one day (or rehearsal slot, or on-demand run) at a time.
// Handwritten, standard DOM and SVG, with the vendored d3 for zoom and pan and watch-layout.js for
// the places. It reads the JSON the page carries inline (the same bytes as data.json beside it);
// served, it reads its own data.json again every minute and redraws in place. That relative read
// is its only one. Text from the data is set with textContent, never as markup.
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var data = JSON.parse(document.getElementById("watch-data").textContent);
  var W = data.size[0], H = data.size[1], CX = W / 2, CY = H / 2;
  var STEP = { deal: "05", settle: "06", ask07: "07" };
  var SAYS = { answered: "answered", refused: "signed refusal", absent: "nothing came back" };
  var RINGS = { checkpoints: 21, "history_card/1": 27 };
  var svg = document.getElementById("graph");
  var world = document.getElementById("world");
  var card = document.getElementById("card");
  var hint = card.firstElementChild ? card.firstElementChild.cloneNode(true) : null;
  var slider = document.getElementById("day");
  var dayLabel = document.getElementById("day-label");
  var status = document.getElementById("status");
  var stampEl = document.getElementById("stamp");
  var summary = document.getElementById("summary");
  var dayName = document.getElementById("day-name");
  var notes = document.getElementById("measured");
  var playDays = document.getElementById("play-days");
  var replay = document.getElementById("replay");
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var state = { view: "live", day: 0, timer: null, places: [], angle: 0, drawn: null, lit: null, shown: null,
                pending: null, updated: new Date() };
  var els = { nodes: {}, pairs: [] };

  function el(tag, attrs, text) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function html(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  // A build or plugin string quotes commit and binary digests in full, then what it contains and its binary's
  // digest; the card, kept small, shows its first part with the digests to 12 digits (data.json has the rest).
  function abbr(s) {
    return String(s).split(/ \(contains |, binary |; /)[0].replace(/\b([\da-f]{12})[\da-f]{28}(?:[\da-f]{24})?\b/g, "$1...");
  }
  function short(id) { return id.slice(0, 8); }
  function view() { return data.views[state.view]; }
  function day() { return view().days[state.day]; }
  function unit(d) { return d && d.set === "ondemand" ? "on-demand run" : view().unit; }
  function hhmmss(t) { return t.toISOString().slice(11, 19); }

  // --- Zoom, pan and rotate -------------------------------------------------
  // d3.zoom moves the world group (wheel or pinch to zoom, drag to pan); Shift and drag rotates the
  // layout about the middle of the graph, by moving the places, so the labels stay upright.
  var zoom = d3.zoom().scaleExtent([0.4, 5])
    .filter(function (ev) { return (!ev.ctrlKey || ev.type === "wheel") && !ev.button && !ev.shiftKey; })
    .on("zoom", function (ev) { world.setAttribute("transform", ev.transform.toString()); });
  var zsel = d3.select(svg).call(zoom).on("dblclick.zoom", null);

  function rotated(p) {
    var a = state.angle, dx = p[0] - CX, dy = p[1] - CY;
    return [CX + dx * Math.cos(a) - dy * Math.sin(a), CY + dx * Math.sin(a) + dy * Math.cos(a)];
  }
  function place(id) { return rotated(state.places[state.day][id] || [CX, CY]); }

  function reset() {
    state.angle = 0;
    zsel.call(zoom.transform, d3.zoomIdentity);
    reposition();
  }
  function turn(deg) { state.angle += deg * Math.PI / 180; reposition(); }

  var grab = null;
  svg.addEventListener("pointerdown", function (ev) {
    if (!ev.shiftKey) return;
    var t = d3.zoomTransform(svg), box = svg.getBoundingClientRect();
    var c = t.apply([CX, CY]);
    grab = { cx: box.left + c[0] * box.width / W, cy: box.top + c[1] * box.height / H, a: null };
    grab.a = Math.atan2(ev.clientY - grab.cy, ev.clientX - grab.cx);
    svg.setPointerCapture(ev.pointerId);
    ev.preventDefault();
  });
  svg.addEventListener("pointermove", function (ev) {
    if (!grab) return;
    var a = Math.atan2(ev.clientY - grab.cy, ev.clientX - grab.cx);
    state.angle += a - grab.a;
    grab.a = a;
    reposition();
  });
  svg.addEventListener("pointerup", function () { grab = null; });
  svg.addEventListener("keydown", function (ev) {
    var k = ev.key, done = true;
    if (k === "+" || k === "=") zsel.call(zoom.scaleBy, 1.25);
    else if (k === "-" || k === "_") zsel.call(zoom.scaleBy, 0.8);
    else if (k === "ArrowLeft") zsel.call(zoom.translateBy, 40, 0);
    else if (k === "ArrowRight") zsel.call(zoom.translateBy, -40, 0);
    else if (k === "ArrowUp") zsel.call(zoom.translateBy, 0, 40);
    else if (k === "ArrowDown") zsel.call(zoom.translateBy, 0, -40);
    else if (k === "[") turn(-15);
    else if (k === "]") turn(15);
    else if (k === "0") reset();
    else if (k === "Escape") hide();
    else done = false;
    if (done) ev.preventDefault();
  });
  document.getElementById("zoom-in").addEventListener("click", function () { zsel.call(zoom.scaleBy, 1.25); });
  document.getElementById("zoom-out").addEventListener("click", function () { zsel.call(zoom.scaleBy, 0.8); });
  document.getElementById("rotate-left").addEventListener("click", function () { turn(-15); });
  document.getElementById("rotate-right").addEventListener("click", function () { turn(15); });
  document.getElementById("reset").addEventListener("click", reset);

  // --- The graph -------------------------------------------------------------

  function stop() {
    if (state.timer) clearInterval(state.timer);
    state.timer = null;
    playDays.textContent = "Play the " + view().unit + "s";
    replay.textContent = "Replay this " + view().unit;
    dayName.textContent = view().unit.charAt(0).toUpperCase() + view().unit.slice(1);
    if (state.pending) { var next = state.pending; state.pending = null; apply(next); }
  }

  // The places, day by day, for the view: watch-layout.js, from the seeds in the data.
  function layoutView() {
    var seeds = {};
    Object.keys(data.nodes).forEach(function (id) { seeds[id] = data.nodes[id].seed; });
    state.places = watchLayout(d3, view().days, seeds, data.size);
  }

  function askKey(id, q) { return id + "|" + q; }
  function name(id) { return id.indexOf("bound:") === 0 ? "the bound group" : "node " + short(id); }
  function linkText(p, d) {
    var a = dotOf(d, p.a), b = dotOf(d, p.b);
    var got = d.edges.filter(function (x) {
      return a.members.indexOf(x.from) >= 0 && b.members.indexOf(x.to) >= 0 ||
        b.members.indexOf(x.from) >= 0 && a.members.indexOf(x.to) >= 0;
    });
    return name(p.a) + " and " + name(p.b) + ": " + (got.length ? got.map(edgeText).join("; ")
      : "no exchange in a committed finding");
  }
  function edgeText(x) {
    return "step " + STEP[x.kind] + " " + x.kind.replace("ask07", "ask") + ", " + short(x.from) + " to " +
      short(x.to) + ": " + SAYS[x.outcome];
  }
  function askText(id, a) {
    return (a.at ? a.at.slice(11, 19) + " UTC, " : "") + "the reporter asks " + short(id) + " " + a.question + ": " +
      SAYS[a.outcome];
  }
  function ringText(r) {
    return r.community + (r.history ? ": history, in " : ": no history since " + (r.since || "its stream started") +
      ", in ") + r.streams.join(", ");
  }
  // The dot a node or a dot id is drawn as on day d.
  function dotOf(d, id) {
    return d.drawn.filter(function (x) { return x.id === id || x.members.indexOf(id) >= 0; })[0];
  }
  function nodeOf(d, id) { return d.nodes.filter(function (x) { return x.node_id === id; })[0]; }

  // lit: null for everything, or {asks: {key: true}, edges: {index: true}} during a replay.
  function draw(lit) {
    var d = day();
    var arriving = state.drawn !== state.view + state.day;
    state.drawn = state.view + state.day;
    state.lit = lit || null;
    while (world.lastChild) world.removeChild(world.lastChild);
    els = { nodes: {}, pairs: [] };
    world.appendChild(el("title", {}, view().label + ", " + d.label + ": the nodes and their pairs"));
    var before = state.day > 0 ? state.places[state.day - 1] : {};
    var lines = el("g", { "class": "pairs" });
    d.links.forEach(function (p) {
      var a = dotOf(d, p.a), b = dotOf(d, p.b);
      var on = p.line === "solid" && (!lit || d.edges.some(function (x, i) {
        return lit.edges[i] && (a.members.indexOf(x.from) >= 0 && b.members.indexOf(x.to) >= 0 ||
          b.members.indexOf(x.from) >= 0 && a.members.indexOf(x.to) >= 0);
      }));
      var line = el("line", { "class": "pair " + (on ? "solid" : "dotted") + (p.line === "solid" && !on ? " dim" : ""),
        "data-line": p.line, "data-a": p.a, "data-b": p.b });
      line.appendChild(el("title", {}, linkText(p, d)));
      lines.appendChild(line);
      var label = p.kinds.length ? el("text", { "class": "steplabel" },
        p.kinds.map(function (k) { return STEP[k]; }).join(" ")) : null;
      if (label) lines.appendChild(label);
      els.pairs.push({ p: p, line: line, label: label });
    });
    world.appendChild(lines);
    d.drawn.forEach(function (dot) {
      var one = dot.members.length === 1 && !dot.bound ? nodeOf(d, dot.members[0]) : null;
      var cls = "node" + (dot.bound ? " bound st-verified" : one.left ? " left" : " st-" + one.state) +
        (one && one.first_day ? " joined" : "");
      var g = el("g", { "class": cls, tabindex: "0", role: "button", "data-node": dot.id,
        "aria-label": dot.bound ? "bound: " + dot.members.map(short).join(" and ")
          : "node " + short(dot.id) + (one.left ? ", left on " + one.left_on : ", " + (one.state || "")) });
      var fresh = dot.members.every(function (m) { return !(m in before); });
      var body = el("g", { "class": arriving && fresh ? "body arrive" : "body" });
      // A single node's two ask rings; a bound group's asks are in its card, node by node.
      (one && !one.left ? one.asked || [] : []).forEach(function (a) {
        var dim = lit && !lit.asks[askKey(one.node_id, a.question)];
        var ring = el("circle", { "class": "ask o-" + a.outcome + (dim ? " dim" : ""), r: RINGS[a.question],
          "data-question": a.question, "data-outcome": a.outcome });
        ring.appendChild(el("title", {}, askText(one.node_id, a)));
        body.appendChild(ring);
      });
      // One ring per community its history cards cover; empty, the same for a new stream and a cleared one.
      dot.rings.forEach(function (r, i) {
        var ring = el("circle", { "class": "community c-" + r.community + (r.history ? "" : " empty"), r: 33 + 6 * i,
          "data-community": r.community, "data-history": String(r.history) });
        ring.appendChild(el("title", {}, ringText(r)));
        body.appendChild(ring);
      });
      body.appendChild(el("circle", { "class": "dot", r: dot.bound ? 17 : 14 }));
      var cards = dot.members.reduce(function (k, m) { return k + ((data.nodes[m] || {}).history_cards || []).length; }, 0);
      body.appendChild(el("circle", { "class": "badge", cx: 24, cy: -24, r: 9 }));
      body.appendChild(el("text", { "class": "badgetext", x: 24, y: -20, "data-cards": cards }, String(cards)));
      var y = 33 + 6 * Math.max(dot.rings.length - 1, 0) + 16;
      body.appendChild(el("text", { "class": "nodelabel code", y: y }, dot.bound ? "bound"
        : short(dot.id)));
      var tags = [];
      if (one && one.left) tags.push("left " + one.left_on);
      else if (one && one.first_day) tags.push("first " + view().unit);
      dot.rings.forEach(function (r) { if (!r.history) tags.push("no history since " + (r.since || "its start")); });
      tags.forEach(function (t, i) { body.appendChild(el("text", { "class": "tag", y: y + 16 * (i + 1) }, t)); });
      g.appendChild(body);
      g.addEventListener("mouseenter", function () { show(dot.id); });
      g.addEventListener("focus", function () { show(dot.id); });
      g.addEventListener("mouseleave", hide);
      g.addEventListener("blur", hide);
      world.appendChild(g);
      els.nodes[dot.id] = g;
    });
    reposition();
    var u = unit(d);
    dayLabel.textContent = (view().test ? "TEST " : "") + (d.set === "ondemand" ? d.label + " (UTC), not a day"
      : u + " " + d.date + " (UTC)") + ", " + (state.day + 1) + " of " + view().days.length + ", run " +
      d.run.replace("_", " ");
    var shown = d.nodes.filter(function (n) { return !n.left; });
    var asks = [].concat.apply([], shown.map(function (n) { return n.asked || []; }));
    var count = function (o) { return asks.filter(function (x) { return x.outcome === o; }).length; };
    var solid = d.pairs.filter(function (p) { return p.line === "solid"; }).length;
    summary.textContent = d.run === "settled"
      ? shown.length + " of " + d.listed + " listed nodes in the " + u + " file; asks: " + count("answered") +
        " answered, " + count("refused") + " refused, " + count("absent") + " absent, of " + asks.length +
        "; pairs: " + solid + " solid of " + d.pairs.length
      : d.run === "missed" ? "The reporter missed this " + u + ": no asks to draw."
      : "No " + u + " file: nothing to draw.";
    if (!lit) status.textContent = "";
    if (state.shown && els.nodes[state.shown]) show(state.shown); else hide();
    notes.textContent = "";
    ["ask", "deal", "settle", "ask07"].forEach(function (k) {
      var m = d.measured[k];
      var what = k === "ask" ? "the reporter's asks" : "step " + STEP[k] + " " + k.replace("ask07", "ask") + " edges";
      var li = html("li", m.measured ? "" : "unmeasured");
      if (m.measured && k === "ask") {
        li.textContent = what + ": " + asks.length + " rings, from " + m.source;
      } else if (m.measured) {
        li.textContent = what + ": " + m.drawn + " drawn, " + m.not_drawn + " not drawn, from " + m.finding +
          " (" + m.result + ")" + (m.not_drawn ? "; not drawn: " + m.why_not_drawn : "");
      } else {
        li.textContent = what + ": " + m.says;
      }
      notes.appendChild(li);
    });
    marks();
  }

  // A dot's place: its node's, or the middle of a bound group's nodes. The layout itself is per node.
  function placeDot(id) {
    var dot = dotOf(day(), id);
    var ps = (dot ? dot.members : [id]).map(place);
    return [ps.reduce(function (k, p) { return k + p[0]; }, 0) / ps.length,
      ps.reduce(function (k, p) { return k + p[1]; }, 0) / ps.length];
  }

  // Moves what is drawn to the current places (after a rotation), without drawing it again.
  function reposition() {
    if (!view().days.length) return;
    Object.keys(els.nodes).forEach(function (id) {
      var p = placeDot(id);
      els.nodes[id].setAttribute("transform", "translate(" + p[0].toFixed(1) + " " + p[1].toFixed(1) + ")");
    });
    els.pairs.forEach(function (x) {
      var a = placeDot(x.p.a), b = placeDot(x.p.b);
      x.line.setAttribute("x1", a[0].toFixed(1)); x.line.setAttribute("y1", a[1].toFixed(1));
      x.line.setAttribute("x2", b[0].toFixed(1)); x.line.setAttribute("y2", b[1].toFixed(1));
      if (x.label) {
        x.label.setAttribute("x", ((a[0] + b[0]) / 2).toFixed(1));
        x.label.setAttribute("y", ((a[1] + b[1]) / 2 + 4).toFixed(1));
      }
    });
  }

  // A pulse that travels along a pair's line, from the sender to the receiver; a ring that spreads
  // from a node when its ask settles. Neither runs under prefers-reduced-motion: the state just changes.
  function travel(x) {
    if (still) return;
    var a = placeDot(x.from), b = placeDot(x.to), t0 = null;
    var dot = x.outcome === "refused" ? el("rect", { "class": "traveller o-refused", width: 10, height: 10 })
      : el("circle", { "class": "traveller o-" + x.outcome, r: 5 });
    world.appendChild(dot);
    function frame(t) {
      if (t0 === null) t0 = t;
      var f = Math.min((t - t0) / 700, 1), px = a[0] + (b[0] - a[0]) * f, py = a[1] + (b[1] - a[1]) * f;
      if (dot.nodeName === "rect") { dot.setAttribute("x", px - 5); dot.setAttribute("y", py - 5); }
      else { dot.setAttribute("cx", px); dot.setAttribute("cy", py); }
      if (f < 1) requestAnimationFrame(frame); else if (dot.parentNode) dot.parentNode.removeChild(dot);
    }
    requestAnimationFrame(frame);
  }
  function ping(id, a) {
    var dot = dotOf(day(), id);
    if (still || !dot || !els.nodes[dot.id]) return;
    els.nodes[dot.id].appendChild(el("circle", { "class": "ping o-" + a.outcome, r: RINGS[a.question] }));
  }

  // --- The card: in its own slot beside the graph, so it never covers it -------------

  function row(dl, k, v) { dl.appendChild(html("dt", "", k)); dl.appendChild(html("dd", "", v)); }

  function cut(s) { return s.length > 16 ? s.slice(0, 12) + "..." : s; }
  function cardList(cards) {
    var ul = html("ul", "cards");
    cards.forEach(function (c) {
      ul.appendChild(html("li", "", "stream " + (c.stream ? cut(c.stream) : "not named") + " (" + c.community + "): " +
        (c.history ? "" : "no history since " + (c.since || "its start") + "; ") +
        (c.witnesses.length ? "witnessed by " + c.witnesses.map(cut).join(", ") : "unwitnessed")));
    });
    return ul;
  }

  function show(id) {
    var d = day();
    var dot = dotOf(d, id);
    if (!dot) return hide();
    state.shown = dot.id;
    card.textContent = "";
    if (dot.bound) return showGroup(d, dot);
    var n = nodeOf(d, id);
    var meta = data.nodes[id] || {};
    var u = unit(d);
    card.appendChild(html("h3", "code", "node " + short(id)));
    var dl = html("dl");
    row(dl, "operator", meta.listed ? meta.operator : "not in nodes/ today");
    row(dl, "build", meta.listed ? abbr(meta.host_build) : "-");
    row(dl, "plugin", meta.listed ? abbr(meta.plugin_version) : "-");
    var cards = meta.history_cards || [];
    dl.appendChild(html("dt", "", "history cards"));
    var dd = html("dd");
    if (!meta.commitment) {
      dd.textContent = "0: no commitment in the committed files";
    } else {
      dd.appendChild(document.createTextNode(cards.length + " stapled to its commitment"));
      if (cards.length) dd.appendChild(cardList(cards));
    }
    dl.appendChild(dd);
    if (meta.binding && meta.binding.says) {
      row(dl, "binding", meta.binding.says + ": " + meta.binding.why + (meta.binding.cites ? "; see " +
        meta.binding.cites : ""));
    }
    var files = view().days.slice(0, state.day + 1).filter(function (x) {
      return x.run !== "no_file" && x.set !== "ondemand";
    }).length;
    row(dl, view().unit + "s listed", n.days_listed + " of " + files + " " + view().unit + " files to this " + u);
    if (n.left) {
      row(dl, "this " + u, "left on " + n.left_on + "; not asked");
    } else {
      row(dl, "this " + u, n.state + " (checkpoints " + n.questions.checkpoints + ", history_card/1 " +
        n.questions["history_card/1"] + ")");
      row(dl, "asks", n.asks.answered + " answered, " + n.asks.refused + " refused, " + n.asks.absent +
        " absent, of 2 questions");
      row(dl, "records", n.records_at_checkpoint === null ? "not known: no checkpoint size in the answer"
        : n.records_at_checkpoint + " at the checkpoint");
    }
    row(dl, "reporter", d.reporter_runs_on === id ? "runs on this node (it asks it through a second console)"
      : d.reporter_runs_on ? "runs on node " + short(d.reporter_runs_on) : "not known this " + unit(d));
    card.appendChild(dl);
  }

  // A bound group: each subject, the verifier's pin, and each node's asks and cards.
  function showGroup(d, dot) {
    var g = data.groups.filter(function (x) { return x.id === dot.id; })[0];
    card.appendChild(html("h3", "", "bound: " + dot.members.length + " nodes, one controller"));
    var dl = html("dl");
    row(dl, "verifier", g.verifier);
    dot.members.forEach(function (m) {
      var n = nodeOf(d, m), meta = data.nodes[m] || {};
      dl.appendChild(html("dt", "code", "node " + short(m)));
      var dd = html("dd");
      dd.appendChild(document.createTextNode((meta.operator || "not in nodes/") + "; asks " + n.asks.answered +
        " answered, " + n.asks.refused + " refused, " + n.asks.absent + " absent" +
        (d.reporter_runs_on === m ? "; the reporter runs on it" : "")));
      dd.appendChild(cardList(meta.history_cards || []));
      dl.appendChild(dd);
    });
    card.appendChild(dl);
  }
  function hide() {
    state.shown = null;
    card.textContent = "";
    if (hint) card.appendChild(hint.cloneNode(true));
  }

  // --- Counts over time: one small chart per count, under the graph ----------------

  // A point's time: a day at its start, a slot or an on-demand run at its own.
  function when(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2})(\d{2})(\d{2})?Z)?$/.exec(s);
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  }
  function cellText(p) {
    return p.value === null ? p.says : String(p.value) + (p.not_drawn ? " (" + p.not_drawn + " not drawn)" : "");
  }

  function charts() {
    document.querySelectorAll(".metrics").forEach(function (box) {
      box.hidden = box.getAttribute("data-view") !== state.view;
    });
    var v = view();
    v.metrics.forEach(function (m) {
      var fig = document.querySelector('.metric[data-view="' + state.view + '"][data-metric="' + m.key + '"]');
      if (!fig) return;
      var table = fig.querySelector("table");
      while (table.rows.length > 1) table.deleteRow(1);
      m.points.forEach(function (p) {
        var tr = table.insertRow(-1);
        tr.setAttribute("data-date", p.date);
        tr.setAttribute("data-set", p.set);
        var th = html("th", "", p.label);
        th.setAttribute("scope", "row");
        tr.appendChild(th);
        var td = html("td", p.value === null ? "unmeasured" : "", cellText(p));
        td.setAttribute("data-v", "");
        tr.appendChild(td);
      });
      var box = fig.querySelector(".chart");
      box.textContent = "";
      var cw = 272, ch = 110, l = 30, r = 8, t = 10, b = 24;
      var s = el("svg", { viewBox: "0 0 " + cw + " " + ch, width: cw, height: ch });
      var pts = m.points;
      var known = pts.filter(function (p) { return p.value !== null; });
      s.appendChild(el("line", { "class": "axis", x1: l, y1: ch - b, x2: cw - r, y2: ch - b }));
      if (!pts.length || !known.length) {
        s.appendChild(el("text", { "class": "none", x: cw / 2, y: ch / 2, "text-anchor": "middle" },
          !pts.length ? "nothing to draw yet" : /^not measured yet/.test(pts[0].says) ? "not measured yet"
            : pts[pts.length - 1].says));
        box.appendChild(s);
        return;
      }
      var t0 = when(pts[0].date), t1 = when(pts[pts.length - 1].date);
      var max = Math.max.apply(null, known.map(function (p) { return p.value; }).concat([1]));
      var x = function (p) { return t1 === t0 ? (l + cw - r) / 2 : l + (when(p.date) - t0) / (t1 - t0) * (cw - r - l); };
      var y = function (p) { return ch - b - p.value / max * (ch - b - t); };
      s.appendChild(el("text", { x: l - 4, y: t + 4, "text-anchor": "end" }, String(max)));
      s.appendChild(el("text", { x: l - 4, y: ch - b + 4, "text-anchor": "end" }, "0"));
      s.appendChild(el("text", { x: l, y: ch - 6, "text-anchor": "start" }, pts[0].date.slice(0, 10)));
      if (pts.length > 1) s.appendChild(el("text", { x: cw - r, y: ch - 6, "text-anchor": "end" },
        pts[pts.length - 1].date.slice(0, 10)));
      var cur = day();
      if (cur) s.appendChild(el("line", { "class": "now", x1: x(cur), y1: t, x2: x(cur), y2: ch - b }));
      // The days' line breaks where a day has no value; an on-demand run is a hollow point of its own.
      var run = [];
      var flush = function () {
        if (run.length > 1) s.appendChild(el("polyline", { "class": "line", points: run.join(" ") }));
        run = [];
      };
      pts.forEach(function (p) {
        if (p.set === "ondemand") return;
        if (p.value === null) { flush(); return; }
        run.push(x(p).toFixed(1) + "," + y(p).toFixed(1));
      });
      flush();
      pts.forEach(function (p) {
        if (p.value === null) return;
        var mark = p.set === "ondemand"
          ? el("path", { "class": "od", d: "M" + x(p).toFixed(1) + " " + (y(p) - 5).toFixed(1) + " l5 5 l-5 5 l-5 -5 z" })
          : el("circle", { "class": "pt", cx: x(p).toFixed(1), cy: y(p).toFixed(1), r: 3 });
        mark.appendChild(el("title", {}, p.label + ": " + cellText(p)));
        s.appendChild(mark);
      });
      box.appendChild(s);
    });
  }
  // Only the "now" line moves with the slider.
  function marks() { charts(); }

  // --- Moving through the days ------------------------------------------------------

  function go(i) {
    stop();
    state.day = i;
    slider.value = String(i);
    draw();
  }

  function setView(name, keep) {
    stop();
    state.view = name;
    document.querySelectorAll("button[data-view]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-view") === name));
    });
    document.querySelectorAll("details.fallback").forEach(function (t) {
      t.hidden = t.getAttribute("data-view") !== name;
    });
    document.body.setAttribute("data-view", name);
    slider.max = String(Math.max(view().days.length - 1, 0));
    layoutView();
    if (!keep) { state.shown = null; hide(); }
    if (!view().days.length) {
      dayLabel.textContent = "no " + view().unit + " files yet";
      notes.textContent = "";
      while (world.lastChild) world.removeChild(world.lastChild);
      charts();
      return;
    }
    var i = view().days.length - 1;
    if (keep) {
      for (var j = view().days.length - 1; j >= 0; j--) if (view().days[j].date <= keep) { i = j; break; }
    }
    state.drawn = keep ? state.view + i : null;
    go(i);
  }

  playDays.addEventListener("click", function () {
    if (state.timer) return stop();
    var i = 0;
    go(0);
    playDays.textContent = "Stop";
    state.timer = setInterval(function () {
      if (++i >= view().days.length) return stop();
      state.day = i;
      slider.value = String(i);
      draw();
    }, still ? 1500 : 1000);
  });

  // Replay: the shown day's asks settle one at a time, in the order of their attempts, each a ring on
  // its node; then each exchange a finding holds travels along its pair's line.
  replay.addEventListener("click", function () {
    if (state.timer) return stop();
    var d = day();
    var order = [];
    d.nodes.forEach(function (n) {
      (n.asked || []).forEach(function (a) { order.push({ at: a.at || "~", id: n.node_id, ask: a }); });
    });
    order.sort(function (a, b) { return a.at < b.at ? -1 : a.at > b.at ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0; });
    d.edges.forEach(function (x, i) { order.push({ edge: i }); });
    var lit = { asks: {}, edges: {} }, k = 0;
    draw(lit);
    replay.textContent = "Stop";
    state.timer = setInterval(function () {
      if (k >= order.length) { stop(); draw(); return; }
      var ev = order[k++];
      if (ev.ask) {
        lit.asks[askKey(ev.id, ev.ask.question)] = true;
        draw(lit);
        ping(ev.id, ev.ask);
        status.textContent = askText(ev.id, ev.ask);
      } else {
        var x = d.edges[ev.edge];
        lit.edges[ev.edge] = true;
        draw(lit);
        travel(x);
        status.textContent = edgeText(x);
      }
    }, still ? 1200 : 800);
  });

  slider.addEventListener("input", function () { go(Number(slider.value)); });
  document.querySelectorAll("button[data-view]").forEach(function (b) {
    b.addEventListener("click", function () {
      setView(b.getAttribute("data-view"));
      if (history.replaceState) history.replaceState(null, "", "#" + b.getAttribute("data-view"));
    });
  });

  // --- Live refresh: the page's own data.json, every minute -----------------------------

  function stamp(text) { stampEl.textContent = text; }
  function apply(next) {
    var keep = view().days.length ? day().date : null;
    data = next;
    setView(state.view, keep);
  }
  function poll() {
    fetch("data.json", { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (next) {
        if (!next || next.format !== data.format || !next.views || !next.views[state.view]) throw new Error("format");
        state.updated = new Date();
        if (JSON.stringify(next) !== JSON.stringify(data)) {
          if (state.timer) state.pending = next; else apply(next);
          stamp("updated " + hhmmss(state.updated) + " UTC with new data; the text tables update on reload");
        } else {
          stamp("updated " + hhmmss(state.updated) + " UTC");
        }
      })
      .catch(function () { stamp("could not read data.json again; last updated " + hhmmss(state.updated) + " UTC"); });
  }
  setInterval(poll, 60000);

  document.getElementById("controls").hidden = false;
  stamp("last updated " + hhmmss(state.updated) + " UTC, when the page loaded");
  setView(location.hash === "#rehearsal" ? "rehearsal" : "live");
})();
