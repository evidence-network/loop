// The watch page: the network as a graph, one day (or rehearsal slot) at a time.
// Handwritten, standard DOM and SVG only; it reads the JSON the page carries inline
// (the same bytes as data.json beside it) and fetches nothing. Text from the data is
// set with textContent, never as markup.
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var data = JSON.parse(document.getElementById("watch-data").textContent);
  var W = 720, CX = 360, CY = 225, R = 160;
  var STEP = { deal: "05", settle: "06", ask07: "07" };
  var SAYS = { answered: "answered", refused: "signed refusal", absent: "nothing came back" };
  var svg = document.getElementById("graph");
  var card = document.getElementById("card");
  var slider = document.getElementById("day");
  var dayLabel = document.getElementById("day-label");
  var status = document.getElementById("status");
  var summary = document.getElementById("summary");
  var dayName = document.getElementById("day-name");
  var notes = document.getElementById("measured");
  var playDays = document.getElementById("play-days");
  var replay = document.getElementById("replay");
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var state = { view: "live", day: 0, timer: null, pos: {} };

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
  // A build or plugin string quotes commit and binary digests in full; the card shows their first 12 digits.
  function abbr(s) { return String(s).replace(/\b([\da-f]{12})[\da-f]{28}(?:[\da-f]{24})?\b/g, "$1..."); }
  function short(id) { return id === "reporter" ? "reporter" : id.slice(0, 8); }
  function view() { return data.views[state.view]; }
  function day() { return view().days[state.day]; }

  function stop() {
    if (state.timer) clearInterval(state.timer);
    state.timer = null;
    playDays.textContent = "Play the " + view().unit + "s";
    replay.textContent = "Replay this " + view().unit;
    dayName.textContent = view().unit.charAt(0).toUpperCase() + view().unit.slice(1);
  }

  // Every node the view ever shows keeps one place on the circle, so a joining node appears in its own spot.
  function place() {
    var ids = {};
    view().days.forEach(function (d) { d.nodes.forEach(function (n) { ids[n.node_id] = true; }); });
    var list = Object.keys(ids).sort();
    state.pos = { reporter: [CX, CY] };
    list.forEach(function (id, i) {
      var a = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(list.length, 1);
      state.pos[id] = [Math.round(CX + R * Math.cos(a)), Math.round(CY + R * Math.sin(a))];
    });
  }

  function curve(a, b, bend) {
    var p = state.pos[a], q = state.pos[b];
    var mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2;
    var dx = q[0] - p[0], dy = q[1] - p[1], len = Math.sqrt(dx * dx + dy * dy) || 1;
    var cx = mx - (dy / len) * bend, cy = my + (dx / len) * bend;
    // Stop short of the target's circle so the end marker sits outside it.
    var ex = q[0] - (q[0] - cx) * 26 / Math.max(Math.sqrt((q[0] - cx) * (q[0] - cx) + (q[1] - cy) * (q[1] - cy)), 1);
    var ey = q[1] - (q[1] - cy) * 26 / Math.max(Math.sqrt((q[0] - cx) * (q[0] - cx) + (q[1] - cy) * (q[1] - cy)), 1);
    return { d: "M" + p[0] + " " + p[1] + " Q" + Math.round(cx) + " " + Math.round(cy) + " " + Math.round(ex) + " " +
      Math.round(ey), mx: Math.round((mx + cx) / 2), my: Math.round((my + cy) / 2) };
  }

  function edgeText(x) {
    var what = x.kind === "ask" ? "the reporter asks " + short(x.to) + " " + x.question
      : "step " + STEP[x.kind] + " " + x.kind.replace("ask07", "ask") + ": " + short(x.from) + " to " + short(x.to);
    return (x.at ? x.at.slice(11, 19) + " UTC, " : "") + what + ": " + SAYS[x.outcome];
  }

  function draw(lit) {
    var d = day();
    while (svg.lastChild && svg.lastChild.nodeName !== "defs") svg.removeChild(svg.lastChild);
    svg.appendChild(el("title", {}, view().label + ", " + d.date + ": the nodes and the edges of this " + view().unit));
    var edges = el("g", { "class": "edges" });
    var pairs = {};
    d.edges.forEach(function (x, i) {
      var key = x.from + ">" + x.to + ">" + x.kind;
      var k = pairs[key] = (pairs[key] || 0) + 1;
      // The reporter's two asks to a node bow apart; a step's edges between two nodes bow well clear of the
      // reporter in the middle, deals and asks to one side, settlements to the other, each further out.
      var bend = x.kind === "ask" ? (k % 2 ? 16 : -16) * Math.ceil(k / 2)
        : (x.kind === "settle" ? -1 : 1) * (96 + 48 * k + (x.kind === "ask07" ? 96 : 0));
      var c = curve(x.from, x.to, bend);
      var cls = "edge o-" + x.outcome + " k-" + x.kind + (lit && lit.indexOf(i) < 0 ? " dim" : "");
      var path = el("path", { "class": cls, d: c.d, "marker-end": "url(#m-" + x.outcome + ")",
        "data-kind": x.kind, "data-outcome": x.outcome });
      path.appendChild(el("title", {}, edgeText(x)));
      edges.appendChild(path);
      if (x.kind !== "ask") edges.appendChild(el("text", { "class": "steplabel", x: c.mx, y: c.my }, STEP[x.kind]));
    });
    svg.appendChild(edges);
    var hub = el("g", { "class": "hub", tabindex: "0", role: "button", "data-node": "reporter",
      "aria-label": "the reporter: it asks every listed node the same two questions" });
    hub.appendChild(el("rect", { x: CX - 34, y: CY - 16, width: 68, height: 32, rx: 6 }));
    hub.appendChild(el("text", { x: CX, y: CY + 5, "class": "hublabel" }, "reporter"));
    svg.appendChild(hub);
    d.nodes.forEach(function (n) {
      var p = state.pos[n.node_id];
      var cls = "node" + (n.left ? " left" : " st-" + n.state) + (n.first_day ? " joined" : "");
      var g = el("g", { "class": cls, tabindex: "0", role: "button", "data-node": n.node_id,
        "aria-label": "node " + short(n.node_id) + (n.left ? ", left on " + n.left_on : ", " + (n.state || "")) });
      if (n.first_day) g.appendChild(el("circle", { "class": "ring", cx: p[0], cy: p[1], r: 24 }));
      g.appendChild(el("circle", { "class": "dot", cx: p[0], cy: p[1], r: 17 }));
      var below = p[1] >= CY;
      g.appendChild(el("text", { x: p[0], y: below ? p[1] + 38 : p[1] - 28, "class": "nodelabel code" },
        short(n.node_id)));
      var tag = n.left ? "left " + n.left_on : n.first_day ? "first " + view().unit : "";
      if (tag) g.appendChild(el("text", { x: p[0], y: below ? p[1] + 54 : p[1] - 44, "class": "tag" }, tag));
      svg.appendChild(g);
    });
    svg.querySelectorAll("g[data-node]").forEach(function (g) {
      g.addEventListener("mouseenter", function () { show(g); });
      g.addEventListener("focus", function () { show(g); });
      g.addEventListener("mouseleave", hide);
      g.addEventListener("blur", hide);
      g.addEventListener("keydown", function (ev) { if (ev.key === "Escape") hide(); });
    });
    dayLabel.textContent = (view().test ? "TEST " : "") + view().unit + " " + d.date + " (UTC), " +
      (state.day + 1) + " of " + view().days.length + " " + view().unit + "s, run " + d.run.replace("_", " ");
    var asks = d.edges.filter(function (x) { return x.kind === "ask"; });
    var shown = d.nodes.filter(function (n) { return !n.left; }).length;
    var count = function (o) { return asks.filter(function (x) { return x.outcome === o; }).length; };
    summary.textContent = d.run === "settled"
      ? shown + " of " + d.listed + " listed nodes in the " + view().unit + " file; asks: " + count("answered") +
        " answered, " + count("refused") + " refused, " + count("absent") + " absent, of " + asks.length + " asks"
      : d.run === "missed" ? "The reporter missed this " + view().unit + ": no asks to draw."
      : "No " + view().unit + " file: nothing to draw.";
    status.textContent = "";
    notes.textContent = "";
    ["ask", "deal", "settle", "ask07"].forEach(function (k) {
      var m = d.measured[k];
      var name = k === "ask" ? "the reporter's asks" : "step " + STEP[k] + " " + k.replace("ask07", "ask") + " edges";
      var li = html("li", m.measured ? "" : "unmeasured");
      if (m.measured && k === "ask") {
        li.textContent = name + ": " + d.edges.filter(function (x) { return x.kind === "ask"; }).length + " edges, from " + m.source;
      } else if (m.measured) {
        li.textContent = name + ": " + m.drawn + " drawn, " + m.not_drawn + " not drawn, from " + m.finding +
          " (" + m.result + ")" + (m.not_drawn ? "; not drawn: " + m.why_not_drawn : "");
      } else {
        li.textContent = name + ": " + m.says;
      }
      notes.appendChild(li);
    });
  }

  function row(dl, k, v) { dl.appendChild(html("dt", "", k)); dl.appendChild(html("dd", "", v)); }

  function show(g) {
    var id = g.getAttribute("data-node");
    var d = day();
    card.textContent = "";
    if (id === "reporter") {
      card.appendChild(html("h3", "", "The reporter"));
      var dlr = html("dl");
      var r = data.reporters.filter(function (x) { return x.key_id === d.reporter_key_id; })[0];
      row(dlr, "key id", d.reporter_key_id ? short(d.reporter_key_id) + "..." : "none this " + view().unit);
      row(dlr, "runs on", r && r.node_id ? "node " + short(r.node_id) + " (it asks that node through a second console)" : "a node not in nodes/");
      row(dlr, "asks", d.edges.filter(function (x) { return x.kind === "ask"; }).length + " this " + view().unit + ", two per node");
      card.appendChild(dlr);
    } else {
      var n = d.nodes.filter(function (x) { return x.node_id === id; })[0];
      var meta = data.nodes[id] || {};
      card.appendChild(html("h3", "code", "node " + short(id)));
      var dl = html("dl");
      row(dl, "operator", meta.listed ? meta.operator : "not in nodes/ today");
      row(dl, "build", meta.listed ? abbr(meta.host_build) : "-");
      row(dl, "plugin", meta.listed ? abbr(meta.plugin_version) : "-");
      var files = view().days.slice(0, state.day + 1).filter(function (x) { return x.run !== "no_file"; }).length;
      row(dl, view().unit + "s listed", n.days_listed + " of " + files + " " + view().unit + " files to this " + view().unit);
      if (n.left) {
        row(dl, "this " + view().unit, "left on " + n.left_on + "; not asked");
      } else {
        row(dl, "this " + view().unit, n.state + " (checkpoints " + n.questions.checkpoints + ", history_card/1 " +
          n.questions["history_card/1"] + ")");
        row(dl, "asks", n.asks.answered + " answered, " + n.asks.refused + " refused, " + n.asks.absent +
          " absent, of 2 questions");
        row(dl, "records", n.records_at_checkpoint === null ? "not known: no checkpoint size in the answer"
          : n.records_at_checkpoint + " at the checkpoint");
      }
      card.appendChild(dl);
    }
    // The card sits in the stage, beside the node, so the graph's own scroll box never clips it.
    var box = svg.getBoundingClientRect(), stage = card.parentNode.getBoundingClientRect();
    var p = state.pos[id];
    var left = box.left - stage.left + p[0] + 28;
    if (left + 360 > stage.width) left = Math.max(box.left - stage.left + p[0] - 388, 0);
    card.style.left = Math.round(left) + "px";
    card.style.top = Math.round(box.top - stage.top + Math.max(p[1] - 40, 0)) + "px";
    card.hidden = false;
  }
  function hide() { card.hidden = true; }

  function go(i) {
    stop();
    state.day = i;
    slider.value = String(i);
    draw();
  }

  function setView(name) {
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
    place();
    hide();
    if (!view().days.length) {
      dayLabel.textContent = "no " + view().unit + " files yet";
      notes.textContent = "";
      while (svg.lastChild && svg.lastChild.nodeName !== "defs") svg.removeChild(svg.lastChild);
      return;
    }
    go(view().days.length - 1);
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
    }, still ? 1500 : 900);
  });

  // Replay: the shown day's edges light up one at a time, the reporter's asks in the order of their attempts.
  replay.addEventListener("click", function () {
    if (state.timer) return stop();
    var order = day().edges.map(function (x, i) { return [x.at || "~", i]; })
      .sort(function (a, b) { return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] - b[1]; })
      .map(function (p) { return p[1]; });
    var lit = [];
    draw(lit);
    replay.textContent = "Stop";
    state.timer = setInterval(function () {
      if (lit.length >= order.length) { stop(); draw(); return; }
      lit.push(order[lit.length]);
      draw(lit);
      status.textContent = edgeText(day().edges[lit[lit.length - 1]]);
    }, still ? 1200 : 450);
  });

  slider.addEventListener("input", function () { go(Number(slider.value)); });
  document.querySelectorAll("button[data-view]").forEach(function (b) {
    b.addEventListener("click", function () {
      setView(b.getAttribute("data-view"));
      if (history.replaceState) history.replaceState(null, "", "#" + b.getAttribute("data-view"));
    });
  });
  document.getElementById("controls").hidden = false;
  setView(location.hash === "#rehearsal" ? "rehearsal" : "live");
})();
