/*
 * Trace picker — lasso selection on top of a read-only Kripke editor.
 *
 * Layers on an existing kripke_editor instance (window.KripkeEditors[id].cy):
 * the student clicks states to build prefix·cycle^ω. Clicking a state already
 * in the path closes the lasso at its earlier occurrence — everything before
 * it becomes the prefix, from it to the end the cycle. Only successors of the
 * last state are clickable; the server re-validates everything.
 *
 *   initTracePicker({
 *     editorId:     kripke editor id the picker attaches to
 *     prefixInput:  selector of the hidden input receiving JSON prefix ids
 *     cycleInput:   selector of the hidden input receiving JSON cycle ids
 *     readout:      selector of the element showing s0 → (s1 → s2)ω
 *     textInput:    optional selector of a text input for typed lasso entry,
 *                   kept in sync with the clicked path (module notation
 *                   prefix·(cycle)ω, MCL8 p.24)
 *     onChange:     optional callback(state) on every change
 *   })
 *
 * Registered at window.TracePickers[editorId]; API: reset(), undo(),
 * getState() → {path, closedAt} , isClosed(), setState(prefix, cycle),
 * applyText(text) → bool.
 */
(function () {
  "use strict";

  const PICKER_STYLES = [
    { selector: "node.tp-dim", style: { opacity: 0.25 } },
    { selector: "edge.tp-dim", style: { opacity: 0.15 } },
    {
      selector: "node.tp-candidate",
      style: { "border-color": "#FAFAFA", "border-width": 3 },
    },
    {
      selector: "node.tp-prefix",
      style: { "border-color": "#FF4D00", "border-width": 3, "background-color": "#1A0A00" },
    },
    {
      selector: "node.tp-cycle",
      style: { "border-color": "#AEFC00", "border-width": 3, "background-color": "#0D1A00" },
    },
    {
      selector: "edge.tp-prefix",
      style: { "line-color": "#FF4D00", "target-arrow-color": "#FF4D00", opacity: 1 },
    },
    {
      selector: "edge.tp-cycle",
      style: { "line-color": "#AEFC00", "target-arrow-color": "#AEFC00", opacity: 1 },
    },
    {
      selector: "edge.tp-loopback",
      style: { "line-style": "dashed" },
    },
  ];

  function initTracePicker(config) {
    const editor = window.KripkeEditors && window.KripkeEditors[config.editorId];
    if (!editor) return null;
    const cy = editor.cy;
    const prefixInput = document.querySelector(config.prefixInput);
    const cycleInput = document.querySelector(config.cycleInput);
    const readoutEl = config.readout ? document.querySelector(config.readout) : null;
    const textInput = config.textInput ? document.querySelector(config.textInput) : null;

    const styles = cy.style().json().concat(PICKER_STYLES);
    cy.style().fromJson(styles).update();

    let path = [];        // clicked node ids, in order
    let closedAt = -1;    // index in path where the cycle starts; -1 = open

    function realNodes() {
      return cy.nodes().filter((n) => !n.data("phantom"));
    }

    function initialId() {
      const initial = realNodes().filter((n) => n.data("initial"));
      return initial.length ? initial[0].id() : null;
    }

    function successorsOf(id) {
      const out = new Set();
      cy.edges().forEach((e) => {
        if (!e.data("phantom") && e.data("source") === id) out.add(e.data("target"));
      });
      return out;
    }

    function edgeBetween(src, tgt) {
      return cy.edges().filter(
        (e) => !e.data("phantom") && e.data("source") === src && e.data("target") === tgt
      );
    }

    function split() {
      if (closedAt < 0) return { prefix: path.slice(), cycle: [] };
      return { prefix: path.slice(0, closedAt), cycle: path.slice(closedAt) };
    }

    function sync() {
      const parts = split();
      if (prefixInput) prefixInput.value = JSON.stringify(parts.prefix);
      if (cycleInput) cycleInput.value = JSON.stringify(closedAt < 0 ? [] : parts.cycle);
      if (readoutEl) readoutEl.textContent = renderReadout(parts);
      if (textInput && document.activeElement !== textInput) {
        textInput.value = canonicalText();
      }
      if (config.onChange) config.onChange(getState());
    }

    function renderReadout(parts) {
      if (!path.length) return "click the initial state to start";
      const name = (id) => {
        const node = cy.getElementById(id);
        return (node && node.data("name")) || id;
      };
      if (closedAt < 0) {
        return path.map(name).join(" → ") + " → …";
      }
      const prefixStr = parts.prefix.map(name).join(" → ");
      const cycleStr = "(" + parts.cycle.map(name).join(" → ") + ")ω";
      return prefixStr ? prefixStr + " → " + cycleStr : cycleStr;
    }

    function paint() {
      cy.nodes().removeClass("tp-dim tp-candidate tp-prefix tp-cycle");
      cy.edges().removeClass("tp-dim tp-prefix tp-cycle tp-loopback");

      const parts = split();
      cy.edges().forEach((e) => {
        if (!e.data("phantom")) e.addClass("tp-dim");
      });

      realNodes().forEach((n) => n.addClass("tp-dim"));
      parts.prefix.forEach((id) => {
        cy.getElementById(id).removeClass("tp-dim").addClass("tp-prefix");
      });
      parts.cycle.forEach((id) => {
        cy.getElementById(id).removeClass("tp-dim").addClass("tp-cycle");
      });

      for (let i = 0; i < path.length - 1; i++) {
        edgeBetween(path[i], path[i + 1]).removeClass("tp-dim").addClass(
          closedAt >= 0 && i >= closedAt ? "tp-cycle" : "tp-prefix"
        );
      }
      if (closedAt >= 0) {
        edgeBetween(path[path.length - 1], path[closedAt])
          .removeClass("tp-dim")
          .addClass("tp-cycle tp-loopback");
      }

      if (closedAt < 0) {
        const candidates = path.length
          ? successorsOf(path[path.length - 1])
          : new Set([initialId()].filter(Boolean));
        candidates.forEach((id) => {
          cy.getElementById(id).removeClass("tp-dim").addClass("tp-candidate");
        });
      }
    }

    function handleTap(evt) {
      if (closedAt >= 0) return;
      const node = evt.target;
      if (node.data("phantom")) return;
      const id = node.id();

      if (!path.length) {
        if (id !== initialId()) {
          editor.warn && editor.warn("Start at the initial state.");
          return;
        }
        path.push(id);
      } else {
        if (!successorsOf(path[path.length - 1]).has(id)) {
          editor.warn && editor.warn("No transition from " + path[path.length - 1] + " to " + id + ".");
          return;
        }
        const seen = path.indexOf(id);
        if (seen >= 0) {
          closedAt = seen;
        } else {
          path.push(id);
        }
      }
      paint();
      sync();
    }

    function undo() {
      if (closedAt >= 0) {
        closedAt = -1;
      } else if (path.length) {
        path.pop();
      }
      paint();
      sync();
    }

    function reset() {
      path = [];
      closedAt = -1;
      paint();
      sync();
    }

    function getState() {
      return { path: path.slice(), closedAt: closedAt };
    }

    function setState(prefixIds, cycleIds) {
      path = prefixIds.concat(cycleIds);
      closedAt = cycleIds.length ? prefixIds.length : -1;
      paint();
      sync();
    }

    function warn(message) {
      if (editor.warn) editor.warn(message);
    }

    function resolveToken(token) {
      const nodes = realNodes();
      const byId = nodes.filter((n) => n.id() === token);
      if (byId.length) return byId[0].id();
      const byName = nodes.filter((n) => (n.data("name") || "") === token);
      if (byName.length > 1) return { ambiguous: token };
      if (byName.length) return byName[0].id();
      return null;
    }

    function tokenize(part) {
      return part
        .split(/→|->|,|\s+/)
        .map((t) => t.trim())
        .filter(Boolean);
    }

    // Typed lasso prefix·(cycle)ω (MCL8 p.24). silent suppresses warnings so
    // live-while-typing does not fire on every half-finished keystroke.
    function applyText(text, silent) {
      const fail = (msg) => {
        if (!silent) warn(msg);
        return false;
      };
      const raw = String(text || "").trim();
      if (!raw) return fail("Type a path like s0 → (s1 → s2)ω.");
      const match = raw.match(/^([^()]*)\(([^()]*)\)\s*(ω|\^ω|\^w|w)?\s*$/);
      if (!match) return fail("Wrap the repeating cycle in parentheses: prefix (cycle)ω.");
      const prefixTokens = tokenize(match[1]);
      const cycleTokens = tokenize(match[2]);
      if (!cycleTokens.length) return fail("The cycle needs at least one state inside ( )ω.");
      const ids = [];
      const tokens = prefixTokens.concat(cycleTokens);
      for (let i = 0; i < tokens.length; i++) {
        const resolved = resolveToken(tokens[i]);
        if (resolved === null) return fail("Unknown state: " + tokens[i] + ".");
        if (typeof resolved === "object") return fail("Ambiguous state name: " + resolved.ambiguous + ".");
        ids.push(resolved);
      }
      if (ids[0] !== initialId()) return fail("The path must start at the initial state.");
      for (let i = 0; i < ids.length - 1; i++) {
        if (!successorsOf(ids[i]).has(ids[i + 1])) {
          return fail("No transition from " + tokens[i] + " to " + tokens[i + 1] + ".");
        }
      }
      const closeSrc = ids[ids.length - 1];
      const closeTgt = ids[prefixTokens.length];
      if (!successorsOf(closeSrc).has(closeTgt)) {
        return fail("The cycle does not close — no transition from " +
          tokens[tokens.length - 1] + " back to " + tokens[prefixTokens.length] + ".");
      }
      setState(ids.slice(0, prefixTokens.length), ids.slice(prefixTokens.length));
      return true;
    }

    // ids, not names, so a typed re-entry round-trips exactly even when names
    // duplicate or contain spaces.
    function canonicalText() {
      const parts = split();
      if (!path.length) return "";
      if (closedAt < 0) return path.join(" → ") + " → …";
      const pre = parts.prefix.join(" → ");
      const cyc = "(" + parts.cycle.join(" → ") + ")ω";
      return pre ? pre + " → " + cyc : cyc;
    }

    cy.on("tap", "node", handleTap);
    paint();
    sync();

    const api = {
      reset: reset,
      undo: undo,
      getState: getState,
      isClosed: function () { return closedAt >= 0; },
      setState: setState,
      applyText: applyText,
      canonicalText: canonicalText,
    };
    window.TracePickers = window.TracePickers || {};
    window.TracePickers[config.editorId] = api;
    return api;
  }

  window.initTracePicker = initTracePicker;
})();
