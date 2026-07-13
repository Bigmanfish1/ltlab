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
 *     onChange:     optional callback(state) on every change
 *   })
 *
 * Registered at window.TracePickers[editorId]; API: reset(), undo(),
 * getState() → {path, closedAt} , isClosed().
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

    cy.on("tap", "node", handleTap);
    paint();
    sync();

    const api = {
      reset: reset,
      undo: undo,
      getState: getState,
      isClosed: function () { return closedAt >= 0; },
    };
    window.TracePickers = window.TracePickers || {};
    window.TracePickers[config.editorId] = api;
    return api;
  }

  window.initTracePicker = initTracePicker;
})();
