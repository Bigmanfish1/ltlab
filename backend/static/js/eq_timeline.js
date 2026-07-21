/*
 * Interactive LTL subformula timeline for the equivalence explorer.
 * Data comes from a json_script tag (see equivalence_result.html):
 *   { aps: [...], formulas: [{label,text,ast}, ...], presets: [{name,cols,loop,distinguishing}] }
 * The ast node shape mirrors engine._formula_ast: {op, text, name?, children?}.
 */
(function () {
  "use strict";

  var TRUE_FG = "#AEFC00";
  var REJECT = "#FF5C33";
  var MUTED = "#6B6B6B";
  var COL_W = 44;
  var GUTTER = 168;
  var MAX_STATES = 12;
  var LIVE_TIMERS = [];

  // ── LTL evaluation over a lasso word (port of engine._LassoWord) ───────────
  function Evaluator(cols, loop) {
    this.cols = cols;
    this.N = cols.length;
    this.P = loop;
    this.C = this.N - loop;
    this.horizon = this.N + Math.max(this.C, 1) + 1;
    this.memo = {};
  }
  Evaluator.prototype.canon = function (pos) {
    if (pos < this.N) return pos;
    if (this.C <= 0) return this.N - 1;
    return this.P + ((pos - this.P) % this.C);
  };
  Evaluator.prototype.holds = function (node, pos) {
    var cp = this.canon(pos);
    var key = node._id + ":" + cp;
    if (key in this.memo) return this.memo[key];
    var r = this.ev(node, cp);
    this.memo[key] = r;
    return r;
  };
  Evaluator.prototype.ev = function (node, cp) {
    var op = node.op, ch = node.children || [], k;
    switch (op) {
      case "tt": return true;
      case "ff": return false;
      case "ap": return this.cols[cp].indexOf(node.name) !== -1;
      case "Not": return !this.holds(ch[0], cp);
      case "And": return ch.every(function (c) { return this.holds(c, cp); }, this);
      case "Or": return ch.some(function (c) { return this.holds(c, cp); }, this);
      case "Implies": return !this.holds(ch[0], cp) || this.holds(ch[1], cp);
      case "Equiv": return this.holds(ch[0], cp) === this.holds(ch[1], cp);
      case "Xor": return this.holds(ch[0], cp) !== this.holds(ch[1], cp);
      case "X": return this.holds(ch[0], cp + 1);
      case "F":
        for (k = 0; k < this.horizon; k++) if (this.holds(ch[0], cp + k)) return true;
        return false;
      case "G":
        for (k = 0; k < this.horizon; k++) if (!this.holds(ch[0], cp + k)) return false;
        return true;
      case "U":
        for (k = 0; k < this.horizon; k++) {
          if (this.holds(ch[1], cp + k)) return true;
          if (!this.holds(ch[0], cp + k)) return false;
        }
        return false;
      case "W":
        for (k = 0; k < this.horizon; k++) {
          if (this.holds(ch[1], cp + k)) return true;
          if (!this.holds(ch[0], cp + k)) return false;
        }
        return true;
      case "R":
        for (k = 0; k < this.horizon; k++) {
          if (!this.holds(ch[1], cp + k)) return false;
          if (this.holds(ch[0], cp + k)) return true;
        }
        return true;
      case "M":
        for (k = 0; k < this.horizon; k++) {
          if (!this.holds(ch[1], cp + k)) return false;
          if (this.holds(ch[0], cp + k)) return true;
        }
        return false;
    }
    return false;
  };

  // ── Plain-English "why" for one subformula at one instant ──────────────────

  // First position at or after `pos` where `node` holds `want` (-1 if none).
  function firstIdx(ev, node, pos, want) {
    for (var k = 0; k < ev.horizon; k++) {
      if (ev.holds(node, pos + k) === want) return pos + k;
    }
    return -1;
  }
  // Name a raw (possibly unrolled) position by its canonical state.
  function at(ev, raw) {
    if (raw < 0) return "never";
    return "s" + ev.canon(raw) + (raw >= ev.N ? " on a later lap" : "");
  }
  function firstChild(ev, ch, pos, want) {
    for (var i = 0; i < ch.length; i++) {
      if (ev.holds(ch[i], pos) === want) return ch[i];
    }
    return null;
  }

  function explain(ev, node, pos) {
    var t = ev.holds(node, pos), ch = node.children || [], k, c;
    switch (node.op) {
      case "tt": return "true everywhere, by definition";
      case "ff": return "false everywhere, by definition";
      case "ap": return node.name + (t ? " is on here" : " is off here");
      case "Not":
        return ch[0].text + " is " + (t ? "false" : "true") + " here";
      case "And":
        if (t) return "every part holds here";
        c = firstChild(ev, ch, pos, false);
        return c.text + " is false here";
      case "Or":
        if (t) return firstChild(ev, ch, pos, true).text + " is true here";
        if (ch.length === 2) return "neither " + ch[0].text + " nor " + ch[1].text + " holds here";
        return "no part holds here";
      case "Implies":
        if (!t) return ch[0].text + " holds but " + ch[1].text + " does not";
        if (!ev.holds(ch[0], pos)) return ch[0].text + " is false here, so this holds vacuously";
        return ch[1].text + " is true here";
      case "Equiv":
        return ch[0].text + " and " + ch[1].text + (t ? " agree here" : " disagree here");
      case "Xor":
        return ch[0].text + " and " + ch[1].text + (t ? " differ here" : " match here");
      case "X":
        return ch[0].text + " is " + (t ? "true" : "false") + " at the next state, " + at(ev, pos + 1);
      case "F":
        if (t) return ch[0].text + " comes true at " + at(ev, firstIdx(ev, ch[0], pos, true));
        return ch[0].text + " never comes true from here on";
      case "G":
        if (t) return ch[0].text + " holds at every state from here on";
        return ch[0].text + " fails at " + at(ev, firstIdx(ev, ch[0], pos, false));
      case "U":
        if (t) {
          k = firstIdx(ev, ch[1], pos, true);
          return ch[1].text + " arrives at " + at(ev, k) +
                 (k === pos ? "" : ", and " + ch[0].text + " holds until then");
        }
        k = firstIdx(ev, ch[0], pos, false);
        if (k >= 0) return ch[0].text + " gives out at " + at(ev, k) + " before " + ch[1].text + " ever arrives";
        return ch[1].text + " never arrives";
      case "W":
        if (t) {
          k = firstIdx(ev, ch[1], pos, true);
          if (k < 0) return ch[0].text + " holds forever, so the wait never has to end";
          return ch[1].text + " arrives at " + at(ev, k) + ", and " + ch[0].text + " holds until then";
        }
        return ch[0].text + " gives out at " + at(ev, firstIdx(ev, ch[0], pos, false)) +
               " before " + ch[1].text + " arrives";
      case "R":
        if (t) {
          k = firstIdx(ev, ch[0], pos, true);
          if (k < 0) return ch[1].text + " holds forever, so nothing needs to release it";
          return ch[0].text + " releases at " + at(ev, k) + ", and " + ch[1].text + " held through it";
        }
        return ch[1].text + " fails at " + at(ev, firstIdx(ev, ch[1], pos, false)) + " with no release before it";
      case "M":
        if (t) {
          k = firstIdx(ev, ch[0], pos, true);
          return ch[0].text + " releases at " + at(ev, k) + ", and " + ch[1].text + " held through it";
        }
        k = firstIdx(ev, ch[1], pos, false);
        if (k >= 0) return ch[1].text + " fails at " + at(ev, k) + " before any release";
        return ch[0].text + " never releases it";
    }
    return t ? "true here" : "false here";
  }

  // ── AST helpers ────────────────────────────────────────────────────────────
  function assignIds(node, counter) {
    node._id = counter.n++;
    (node.children || []).forEach(function (c) { assignIds(c, counter); });
  }
  // Root-first, depth-tagged list of subformula rows.
  function flatten(node, depth, out) {
    out.push({ node: node, depth: depth });
    (node.children || []).forEach(function (c) { flatten(c, depth + 1, out); });
    return out;
  }

  // ── state-graph (lasso) drawing ────────────────────────────────────────────
  var SVG_NS = "http://www.w3.org/2000/svg";
  var ARROW_SEQ = 0;
  var NODE_R = 15, NODE_CY = 44, NODE_STEP = 64, SVG_H = 100;

  function svg(name, attrs) {
    var e = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function nodeX(i) { return 26 + i * NODE_STEP; }

  function arrowMarker(id, color) {
    var m = svg("marker", {
      id: id, viewBox: "0 0 8 8", refX: 7, refY: 4,
      markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse",
    });
    m.appendChild(svg("path", { d: "M0,0 L8,4 L0,8 z", fill: color }));
    return m;
  }

  // One graph per formula: node lit iff the whole formula holds there.
  function lassoSvg(ev, ast, cols, loop, cursor, justLooped, onPick) {
    var N = cols.length;
    var w = nodeX(N - 1) + 26 + (loop === N - 1 ? 14 : 0);
    var s = svg("svg", { width: w, height: SVG_H, viewBox: "0 0 " + w + " " + SVG_H });

    var seq = ARROW_SEQ++;
    var idNext = "eqar" + seq, idBack = "eqab" + seq;
    var defs = svg("defs", {});
    defs.appendChild(arrowMarker(idNext, "#3A3A3A"));
    defs.appendChild(arrowMarker(idBack, "#C6862E"));
    s.appendChild(defs);

    // forward edges
    for (var i = 0; i + 1 < N; i++) {
      s.appendChild(svg("line", {
        x1: nodeX(i) + NODE_R + 2, y1: NODE_CY,
        x2: nodeX(i + 1) - NODE_R - 4, y2: NODE_CY,
        stroke: "#3A3A3A", "stroke-width": 1.5, "marker-end": "url(#" + idNext + ")",
      }));
    }

    // the arrow back
    var xl = nodeX(N - 1), xt = nodeX(loop);
    var back = svg("path", {
      fill: "none", stroke: justLooped ? "#FFC46B" : "#C6862E",
      "stroke-width": justLooped ? 3 : 1.5,
      "marker-end": "url(#" + idBack + ")",
    });
    if (loop === N - 1) {
      back.setAttribute("d", "M" + (xl + 6) + "," + (NODE_CY + NODE_R - 2) +
        " C" + (xl + 34) + "," + (NODE_CY + 40) + " " + (xl - 34) + "," + (NODE_CY + 40) +
        " " + (xl - 6) + "," + (NODE_CY + NODE_R - 2));
    } else {
      back.setAttribute("d", "M" + xl + "," + (NODE_CY + NODE_R + 2) +
        " Q" + ((xl + xt) / 2) + "," + (NODE_CY + 52) + " " + xt + "," + (NODE_CY + NODE_R + 3));
    }
    s.appendChild(back);
    var loopTag = svg("text", {
      x: (xl + xt) / 2, y: NODE_CY + 46, "text-anchor": "middle",
      fill: justLooped ? "#FFC46B" : "#C6862E",
      "font-size": justLooped ? 14 : 11, "font-family": "ui-monospace,monospace",
    });
    loopTag.textContent = "↻";
    s.appendChild(loopTag);

    for (var j = 0; j < N; j++) {
      (function (j) {
        var on = ev.holds(ast, j), cur = j === cursor;
        var g = svg("g", { style: "cursor:pointer" });

        // propositions true here
        var lbl = svg("text", {
          x: nodeX(j), y: 16, "text-anchor": "middle",
          fill: cols[j].length ? "#9A9A9A" : "#3A3A3A",
          "font-size": 10, "font-family": "ui-monospace,monospace",
        });
        lbl.textContent = cols[j].length ? cols[j].join(",") : "∅";
        g.appendChild(lbl);

        g.appendChild(svg("circle", {
          cx: nodeX(j), cy: NODE_CY, r: NODE_R,
          fill: on ? "#1D3A0E" : "#0D0D0D",
          stroke: cur ? "#B8B8B8" : (on ? "#4E8A24" : "#1E1E1E"),
          "stroke-width": cur ? 2 : 1,
        }));
        var t = svg("text", {
          x: nodeX(j), y: NODE_CY + 4, "text-anchor": "middle",
          fill: on ? TRUE_FG : "#5A5A5A",
          "font-size": 10, "font-family": "ui-monospace,monospace",
        });
        t.textContent = "s" + j;
        g.appendChild(t);

        var why = svg("title", {});
        why.textContent = "s" + j + ": " + explain(ev, ast, j);
        g.appendChild(why);

        g.addEventListener("click", function () { onPick(j); });
        s.appendChild(g);
      })(j);
    }
    return s;
  }

  // ── tiny DOM helpers ───────────────────────────────────────────────────────
  function el(tag, css, text) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (text != null) e.textContent = text;
    return e;
  }
  function rowFlex() {
    return el("div", "display:flex;align-items:stretch;");
  }
  function gutter(text, css) {
    return el("div",
      "width:" + GUTTER + "px;flex:0 0 " + GUTTER + "px;display:flex;align-items:center;" +
      "font:11px ui-monospace,monospace;padding:0 8px;box-sizing:border-box;" + (css || ""),
      text);
  }

  function setup(root) {
    var dataTag = document.getElementById(root.getAttribute("data-timeline"));
    if (!dataTag) return;
    var data;
    try { data = JSON.parse(dataTag.textContent); } catch (e) { return; }
    if (!data.formulas || !data.formulas.length) return;

    var counter = { n: 0 };
    data.formulas.forEach(function (f) { assignIds(f.ast, counter); });
    var rowsByFormula = data.formulas.map(function (f) {
      return { f: f, rows: flatten(f.ast, 0, []) };
    });
    var notEquivalent = (data.presets || []).some(function (p) { return p.distinguishing; });

    // mutable trace state
    var preset0 = (data.presets && data.presets[0]) || { cols: [[], []], loop: 0 };
    var state = { cols: deepCols(preset0.cols), loop: preset0.loop, cursor: 0, looped: false, hist: [] };
    var timer = null;

    function deepCols(cols) { return cols.map(function (c) { return c.slice(); }); }

    // Past the last state, time falls back to the loop point. Back-stepping pops
    // history so a self-loop rewinds to the previous pass, not to s1.
    function stepCursor(d) {
      if (d > 0) {
        state.hist.push({ c: state.cursor, looped: state.looped });
        if (state.hist.length > 400) state.hist.shift();
        var n = state.cursor + 1;
        state.looped = n >= state.cols.length;
        if (state.looped) n = state.loop;
        state.cursor = n;
      } else {
        var prev = state.hist.pop();
        if (!prev) return;
        state.cursor = prev.c;
        state.looped = prev.looped;
      }
      render();
    }
    function goTo(i) {
      stopPlay();
      state.cursor = i;
      state.looped = false;
      state.hist = [];
      render();
    }
    function stopPlay() {
      if (!timer) return;
      clearInterval(timer);
      var at = LIVE_TIMERS.indexOf(timer);
      if (at !== -1) LIVE_TIMERS.splice(at, 1);
      timer = null;
    }

    // ── rendering ─────────────────────────────────────────────────────────────
    var traceHost = root.querySelector("[data-tl-trace]");
    var formulaHost = root.querySelector("[data-tl-formulas]");
    var verdictHost = root.querySelector("[data-tl-verdict]");
    var graphHost = root.querySelector("[data-tl-graphs]");

    function truthCell(isTrue, opts) {
      opts = opts || {};
      var e = el("div",
        "width:" + COL_W + "px;flex:0 0 " + COL_W + "px;height:" + (opts.h || 26) + "px;" +
        "margin:2px;border-radius:4px;display:flex;align-items:center;justify-content:center;" +
        "font:12px ui-monospace,monospace;box-sizing:border-box;" +
        (isTrue
          ? "background:" + (opts.strong ? "#1D3A0E" : "#132708") + ";border:1px solid " +
            (opts.strong ? "#4E8A24" : "#2C5216") + ";color:" + TRUE_FG + ";"
          : "background:#0D0D0D;border:1px solid #1E1E1E;color:#3A3A3A;"));
      // Loop accent and playhead ring are shadows, so neither shifts the grid.
      var shadow = [];
      if (opts.loopStart) shadow.push("inset 3px 0 0 #C6862E");
      if (opts.cursor) shadow.push("0 0 0 1px #B8B8B8");
      if (shadow.length) e.style.boxShadow = shadow.join(",");
      if (opts.clickable) e.style.cursor = "pointer";
      if (opts.glyph) e.textContent = isTrue ? opts.glyph : (opts.glyphOff || "");
      return e;
    }

    // Keep the toolbar's loop picker in step with the trace length.
    var loopSel = root.querySelector("[data-tl-loop]");
    function syncLoopSelect(N) {
      if (!loopSel) return;
      if (loopSel.options.length !== N) {
        loopSel.innerHTML = "";
        for (var i = 0; i < N; i++) {
          loopSel.appendChild(new Option("s" + i, String(i)));
        }
      }
      loopSel.value = String(state.loop);
    }
    if (loopSel) loopSel.onchange = function () {
      state.loop = parseInt(loopSel.value, 10) || 0;
      render();
    };

    function render() {
      var ev = new Evaluator(state.cols, state.loop);
      var N = state.cols.length;

      // --- trace editor ---
      traceHost.innerHTML = "";

      // column-index header (click to move the playhead)
      var hdr = rowFlex();
      hdr.appendChild(gutter("state", "color:" + MUTED + ";justify-content:flex-end;"));
      for (var i = 0; i < N; i++) {
        (function (i) {
          var isCur = i === state.cursor;
          var h = el("div",
            "width:" + COL_W + "px;flex:0 0 " + COL_W + "px;margin:2px;height:20px;cursor:pointer;" +
            "display:flex;align-items:center;justify-content:center;border-radius:4px;" +
            "font:10px ui-monospace,monospace;" +
            (isCur
              ? "border:1px solid #B8B8B8;background:#232323;color:#FAFAFA;"
              : "border:1px solid #1E1E1E;color:#9A9A9A;"),
            "s" + i);
          if (i === state.loop) h.style.boxShadow = "inset 3px 0 0 #C6862E";
          h.title = "move the playhead to s" + i +
                    (i === state.loop ? " (the run loops back here)" : "");
          h.onclick = function () { goTo(i); };
          hdr.appendChild(h);
        })(i);
      }
      traceHost.appendChild(hdr);

      // The loop point is set from the toolbar; here it is only shown.
      syncLoopSelect(N);

      // one editable row per atomic proposition
      (data.aps || []).forEach(function (ap) {
        var r = rowFlex();
        r.appendChild(gutter(ap, "color:#FAFAFA;justify-content:flex-end;"));
        for (var i = 0; i < N; i++) {
          (function (i) {
            var on = state.cols[i].indexOf(ap) !== -1;
            var c = truthCell(on, {
              clickable: true, glyph: "●", h: 26,
              loopStart: i === state.loop, cursor: i === state.cursor,
            });
            c.title = ap + " is " + (on ? "on" : "off") + " at s" + i + ". Click to flip it.";
            c.onclick = function () {
              var idx = state.cols[i].indexOf(ap);
              if (idx === -1) state.cols[i].push(ap); else state.cols[i].splice(idx, 1);
              render();
            };
            r.appendChild(c);
          })(i);
        }
        traceHost.appendChild(r);
      });

      if (!(data.aps || []).length) {
        traceHost.appendChild(el("div",
          "font:10px ui-monospace,monospace;color:" + MUTED + ";padding:6px 8px;",
          "These formulas use no propositions."));
      }

      // --- formula decompositions ---
      formulaHost.innerHTML = "";
      var verdicts = [];
      rowsByFormula.forEach(function (fb) {
        var accepts = ev.holds(fb.f.ast, 0);
        verdicts.push(accepts);

        var block = el("div", "margin-top:14px;");
        var head = rowFlex();
        head.style.alignItems = "center";
        head.style.marginBottom = "2px";
        var badge = el("span",
          "font:9px ui-monospace,monospace;padding:2px 6px;border-radius:4px;margin-left:8px;" +
          (accepts ? "color:" + TRUE_FG + ";background:#132708;" : "color:" + REJECT + ";background:#200B06;"),
          accepts ? "ACCEPTS" : "REJECTS");
        var title = el("div", "font:12px ui-monospace,monospace;color:#FAFAFA;display:flex;align-items:center;",
          fb.f.label + " = " + fb.f.text);
        title.appendChild(badge);
        head.appendChild(title);
        block.appendChild(head);

        fb.rows.forEach(function (row, ri) {
          var isRoot = ri === 0;
          var r = rowFlex();
          var lbl = gutter(row.node.text, "justify-content:flex-start;" +
            "padding-left:" + (8 + row.depth * 13) + "px;" +
            (isRoot ? "color:#FAFAFA;font-weight:600;" : "color:#9A9A9A;"));
          r.appendChild(lbl);
          for (var i = 0; i < N; i++) {
            var t = ev.holds(row.node, i);
            var cell = truthCell(t, {
              strong: isRoot,
              glyph: isRoot ? "✓" : "",
              loopStart: i === state.loop,
              cursor: i === state.cursor,
            });
            // why this cell is lit, without putting prose on the page
            cell.title = row.node.text + " is " + (t ? "true" : "false") +
                         " at s" + i + ": " + explain(ev, row.node, i);
            r.appendChild(cell);
          }
          block.appendChild(r);
        });
        formulaHost.appendChild(block);
      });

      // --- state-graph view of the same run ---
      renderGraphs(ev);

      // --- readout + verdict ---
      renderVerdict(ev, verdicts);
    }

    // A and B over the identical run: only the lit states differ.
    function renderGraphs(ev) {
      if (!graphHost) return;
      graphHost.innerHTML = "";
      rowsByFormula.forEach(function (fb) {
        var accepts = ev.holds(fb.f.ast, 0);
        var card = el("div", "border:1px solid #1F1F1F;border-radius:4px;background:#0C0C0C;");

        var head = el("div",
          "display:flex;align-items:center;gap:8px;padding:6px 10px;" +
          "border-bottom:1px solid #1F1F1F;font:11px ui-monospace,monospace;");
        head.appendChild(el("span", "color:#9A9A9A;", fb.f.label));
        head.appendChild(el("span", "color:#FAFAFA;", fb.f.text));
        head.appendChild(el("span",
          "font-size:9px;padding:2px 6px;border-radius:4px;margin-left:auto;" +
          (accepts ? "color:" + TRUE_FG + ";background:#132708;"
                   : "color:" + REJECT + ";background:#200B06;"),
          accepts ? "ACCEPTS" : "REJECTS"));
        card.appendChild(head);

        var body = el("div", "overflow-x:auto;padding:4px 6px;");
        body.appendChild(lassoSvg(ev, fb.f.ast, state.cols, state.loop, state.cursor,
          state.looped, function (j) { goTo(j); }));
        card.appendChild(body);
        graphHost.appendChild(card);
      });
    }

    // Line 1 follows the playhead; line 2 is the verdict for the whole run.
    function renderVerdict(ev, verdicts) {
      verdictHost.innerHTML = "";
      var c = state.cursor;

      var now = el("div", "font:11px ui-monospace,monospace;line-height:1.6;");
      now.appendChild(el("span", "color:#B8B8B8;", "at s" + c));
      rowsByFormula.forEach(function (fb) {
        var t = ev.holds(fb.f.ast, c);
        now.appendChild(el("span", "color:#3A3A3A;padding:0 8px;", "│"));
        now.appendChild(el("span", "color:#9A9A9A;", fb.f.label + " "));
        now.appendChild(el("span", "color:#FAFAFA;", fb.f.text + " "));
        now.appendChild(el("span", "color:" + (t ? TRUE_FG : REJECT) + ";", t ? "✓ " : "✗ "));
        now.appendChild(el("span", "color:" + MUTED + ";", explain(ev, fb.f.ast, c)));
      });
      verdictHost.appendChild(now);

      if (verdicts.length < 2) return;
      var a = verdicts[0], b = verdicts[1];
      var line = el("div", "font:11px ui-monospace,monospace;line-height:1.6;margin-top:4px;");
      if (a !== b) {
        line.appendChild(el("span", "color:" + REJECT + ";",
          "This run tells them apart, so A and B are not equivalent."));
      } else if (notEquivalent) {
        line.appendChild(el("span", "color:#E0A030;",
          "They agree on this run, but another one tells them apart."));
      } else {
        line.appendChild(el("span", "color:" + TRUE_FG + ";",
          "They agree, and always will: A and B are equivalent."));
      }
      verdictHost.appendChild(line);
    }

    // ── controls: add / remove states, preset traces ────────────────────────────
    var addBtn = root.querySelector("[data-tl-add]");
    var delBtn = root.querySelector("[data-tl-del]");
    if (addBtn) addBtn.onclick = function () {
      if (state.cols.length >= MAX_STATES) return;
      var last = state.cols[state.cols.length - 1] || [];
      state.cols.push(last.slice());
      render();
    };
    if (delBtn) delBtn.onclick = function () {
      if (state.cols.length <= 1) return;
      state.cols.pop();
      if (state.loop >= state.cols.length) state.loop = state.cols.length - 1;
      if (state.cursor >= state.cols.length) state.cursor = state.cols.length - 1;
      state.looped = false;
      state.hist = [];
      render();
    };

    // The transport is duplicated next to the graphs, which sit well below the
    // grid; every copy drives the one playhead.
    var playBtns = root.querySelectorAll("[data-tl-play]");
    function syncPlayBtns() {
      playBtns.forEach(function (b) { b.textContent = timer ? "❚❚ pause" : "▶ play"; });
    }
    root.querySelectorAll("[data-tl-prev]").forEach(function (b) {
      b.onclick = function () { stopPlay(); syncPlayBtns(); stepCursor(-1); };
    });
    root.querySelectorAll("[data-tl-next]").forEach(function (b) {
      b.onclick = function () { stopPlay(); syncPlayBtns(); stepCursor(1); };
    });
    playBtns.forEach(function (b) {
      b.onclick = function () {
        if (timer) { stopPlay(); } else {
          timer = setInterval(function () { stepCursor(1); }, 900);
          LIVE_TIMERS.push(timer);
        }
        syncPlayBtns();
      };
    });

    var presetHost = root.querySelector("[data-tl-presets]");
    if (presetHost && data.presets) {
      data.presets.forEach(function (p) {
        var btn = el("button", null, p.name);
        btn.type = "button";
        btn.className = "eq-preset-btn";
        btn.style.cssText =
          "font:10px ui-monospace,monospace;padding:4px 8px;border-radius:4px;cursor:pointer;" +
          "border:1px solid " + (p.distinguishing ? "#5A2013" : "#2C5216") + ";" +
          "color:" + (p.distinguishing ? REJECT : TRUE_FG) + ";background:#0C0C0C;";
        btn.onclick = function () {
          stopPlay();
          syncPlayBtns();
          state.cols = deepCols(p.cols);
          state.loop = p.loop;
          state.cursor = 0;
          state.looped = false;
          state.hist = [];
          render();
        };
        presetHost.appendChild(btn);
      });
    }

    render();
  }

  function initAll() {
    // A fragment swapped out mid-playback leaves its interval ticking.
    LIVE_TIMERS.forEach(clearInterval);
    LIVE_TIMERS.length = 0;
    document.querySelectorAll("[data-timeline]").forEach(setup);
  }

  window.EquivalenceTimeline = { initAll: initAll };
})();
