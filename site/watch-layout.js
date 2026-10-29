// The watch page's layout: where each node sits, day by day, with d3's force layout.
// Handwritten. A node starts at the seed watch.py gives it from its node id, so the layout is the
// same on every load. The days are laid out in order: when a node first appears, the nodes already
// placed are pinned where they are while the new one settles, so they never jump. A node keeps its
// place after that, through the days it is absent or has left. The simulation runs to rest at once
// (no timers), so the result depends only on the data. watch.js uses it in the page; the tests run
// it under node.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else root.watchLayout = factory;
})(this, function (d3, days, seeds, size) {
  "use strict";
  var cx = size[0] / 2, cy = size[1] / 2, pad = 48;
  var placed = {};
  var out = [];
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  days.forEach(function (d) {
    var fresh = d.nodes.map(function (n) { return n.node_id; })
      .filter(function (id) { return !(id in placed); }).sort();
    if (fresh.length) {
      var nodes = Object.keys(placed).sort().map(function (id) {
        var p = placed[id];
        return { id: id, x: p[0], y: p[1], fx: p[0], fy: p[1] };
      }).concat(fresh.map(function (id) {
        var s = seeds[id] || [cx, cy];
        return { id: id, x: s[0], y: s[1] };
      }));
      d3.forceSimulation(nodes).stop()
        .force("charge", d3.forceManyBody().strength(-900))
        .force("x", d3.forceX(cx).strength(0.05))
        .force("y", d3.forceY(cy).strength(0.07))
        .force("collide", d3.forceCollide(64))
        .tick(300);
      // Every node's place is read back from the simulation: the pins are what hold the old ones.
      nodes.forEach(function (n) {
        placed[n.id] = [Math.round(clamp(n.x, pad, size[0] - pad) * 10) / 10,
          Math.round(clamp(n.y, pad, size[1] - pad) * 10) / 10];
      });
    }
    var at = {};
    Object.keys(placed).forEach(function (id) { at[id] = placed[id].slice(); });
    out.push(at);
  });
  return out;
});
