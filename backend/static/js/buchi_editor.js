/**
 * buchi_editor.js — reusable Büchi-automaton graph editor.
 *
 * Sibling of kripke_editor.js, adapted for Büchi automata (MCL5 p.13-19):
 *   - states carry no propositions; instead each state can be marked ACCEPTING
 *     (rendered as a double ring), and MORE THAN ONE state may be INITIAL (I⊆Q);
 *   - every transition carries an editable boolean-expression LABEL over the
 *     alphabet (e.g. `a & !b`, or `1` for true).
 *
 * Otherwise the interaction model matches the Kripke editor: add/remove states
 * and transitions, undo/redo, right-click context menu, keyboard shortcuts, and
 * a hidden <input> kept in sync with the phantom-stripped graph JSON so wrapping
 * the component in a <form> submits the drawn automaton with no extra wiring.
 * The JSON shape (nodes: {id, name, initial, accepting}; edges: {source, target,
 * label}) is exactly what apps/checker/buchi.py:build_buchi consumes.
 *
 * Returns an API and registers it at window.BuchiEditors[id].
 */
(function () {
  "use strict";

  // ── Cytoscape style ─────────────────────────────────────────────────────────
  const CY_STYLE = [
    {
      selector: "node",
      style: {
        "background-color": "#0A0A0A",
        "border-color": "#FAFAFA",
        "border-width": 2,
        label: "data(name)",
        color: "#FAFAFA",
        "font-family": "monospace",
        "font-size": 11,
        "text-valign": "center",
        "text-halign": "center",
        width: 44,
        height: 44,
      },
    },
    {
      // Accepting state: double ring (needs a wider border for the gap to show).
      selector: "node[?accepting]",
      style: { "border-style": "double", "border-width": 6 },
    },
    {
      // Selection recolours only (never touches border-width/style) so an
      // accepting state keeps its double ring while selected.
      selector: "node:selected",
      style: { "border-color": "#FF4D00", "background-color": "#1A0A00" },
    },
    {
      selector: ".edge-source",
      style: { "border-color": "#AEFC00", "background-color": "#0D1A00" },
    },
    {
      selector: "edge",
      style: {
        width: 2,
        "line-color": "#6B6B6B",
        "target-arrow-color": "#6B6B6B",
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        "arrow-scale": 1.2,
        label: "data(label)",
        "font-family": "monospace",
        "font-size": 11,
        color: "#AEFC00",
        "text-background-color": "#0A0A0A",
        "text-background-opacity": 1,
        "text-background-padding": 3,
        "text-rotation": "none",
      },
    },
    {
      selector: "edge:selected",
      style: { "line-color": "#FF4D00", "target-arrow-color": "#FF4D00" },
    },
    {
      selector: "edge[source = target]",
      style: {
        "curve-style": "loop",
        "loop-direction": "-45deg",
        "loop-sweep": "-45deg",
        "control-point-step-size": 40,
      },
    },
    {
      selector: "node[?phantom]",
      style: {
        width: 1,
        height: 1,
        "background-color": "transparent",
        "border-width": 0,
        label: "",
        events: "no",
      },
    },
    {
      selector: "edge[?phantom]",
      style: {
        width: 2,
        "line-color": "#FF4D00",
        "target-arrow-color": "#FF4D00",
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        label: "",
        events: "no",
      },
    },
  ];

  // Default automaton: "infinitely often a" (G F a) — a familiar Büchi example.
  const DEFAULT_ELEMENTS = [
    { data: { id: "s0", name: "s0", initial: true, accepting: false }, position: { x: 240, y: 240 } },
    { data: { id: "s1", name: "s1", initial: false, accepting: true }, position: { x: 470, y: 240 } },
    { data: { id: "e0", source: "s0", target: "s0", label: "1" } },
    { data: { id: "e1", source: "s0", target: "s1", label: "a" } },
    { data: { id: "e2", source: "s1", target: "s0", label: "1" } },
  ];

  function resolve(sel) {
    if (!sel) return null;
    return typeof sel === "string" ? document.querySelector(sel) : sel;
  }

  function initBuchiEditor(config) {
    config = config || {};
    const id = config.id || "buchi";
    const byId = (suffix) => document.getElementById(id + "-" + suffix);

    const mount = resolve(config.mount) || byId("cy");
    if (!mount) {
      console.error("initBuchiEditor: mount not found for id '" + id + "'");
      return null;
    }
    const input = resolve(config.input) || byId("graph-data");
    const toolbar = resolve(config.toolbar) || byId("toolbar");
    const indicatorEl = byId("indicator");
    const noInitialHintEl = byId("no-initial-hint");
    const emptyCanvasHintEl = byId("empty-canvas-hint");
    const tooltipEl = byId("node-tooltip");
    const tooltipNameEl = byId("node-tooltip-name");
    const tooltipPropsEl = byId("node-tooltip-props");

    const editable = config.editable !== false;
    const useKeyboard = config.keyboard !== false && editable;
    const fitOnLoad = config.fitOnLoad !== false;
    // Autosave: when a storageKey is given, the working canvas is mirrored to
    // localStorage on every change so a reload keeps in-progress edits even
    // when nothing was submitted. A non-empty autosave wins over the server-
    // restored elements; an absent/empty one falls through to them.
    const storageKey = config.storageKey || null;
    function loadAutosave() {
      if (!storageKey) return null;
      try {
        const parsed = JSON.parse(localStorage.getItem(storageKey) || "null");
        const els = parsed && parsed.elements;
        const arr = els ? (els.nodes || []).concat(els.edges || []) : null;
        return arr && arr.length ? arr : null;
      } catch (e) {
        return null;
      }
    }
    // An explicit array (even empty) is honoured, so a caller can start blank;
    // only a wholly absent `elements` falls back to the demo automaton.
    const elements =
      loadAutosave() ||
      (Array.isArray(config.elements) ? config.elements : DEFAULT_ELEMENTS);

    let currentTool = "node";
    let edgeSource = null;
    const undoStack = [];
    const redoStack = [];
    let hovering = false;
    let contextMenu = null;

    let nodeCounter = 0;
    elements.forEach((el) => {
      const eid = el.data && el.data.id;
      const m = typeof eid === "string" && /^s(\d+)$/.exec(eid);
      if (m) nodeCounter = Math.max(nodeCounter, parseInt(m[1], 10) + 1);
    });
    function nextNodeId() {
      let candidate;
      do {
        candidate = "s" + nodeCounter++;
      } while (cy.getElementById(candidate).length);
      return candidate;
    }

    function warn(html) {
      mount.dispatchEvent(new CustomEvent("buchi:warn", { bubbles: true, detail: { html } }));
    }
    function emitChange() {
      mount.dispatchEvent(new CustomEvent("buchi:change", { bubbles: true }));
    }

    const cy = cytoscape({
      container: mount,
      elements: JSON.parse(JSON.stringify(elements)),
      style: CY_STYLE,
      layout: { name: "preset" },
      minZoom: 0.2,
      maxZoom: 4,
      boxSelectionEnabled: editable,
      autoungrabify: !editable,
    });

    function getCleanGraphJson() {
      const raw = cy.json();
      const els = raw.elements || {};
      return {
        ...raw,
        elements: {
          nodes: (els.nodes || []).filter((n) => !n.data.phantom),
          edges: (els.edges || []).filter((e) => !e.data.phantom),
        },
      };
    }

    function syncGraphData() {
      const clean = getCleanGraphJson();
      if (input) input.value = JSON.stringify(clean);
      if (storageKey && editable) {
        try {
          localStorage.setItem(storageKey, JSON.stringify(clean));
        } catch (e) {
          /* storage full / disabled — autosave is best-effort */
        }
      }
      emitChange();
    }

    // ── Undo / redo ─────────────────────────────────────────────────────────
    function saveSnapshot() {
      undoStack.push(getCleanGraphJson());
      redoStack.length = 0;
    }
    function restore(json) {
      cy.json(json);
      refreshAllInitialArrows();
      updateInitialHint();
      updateEmptyCanvasHint();
      syncGraphData();
    }
    function undoAction() {
      if (!undoStack.length) return;
      redoStack.push(getCleanGraphJson());
      restore(undoStack.pop());
    }
    function redoAction() {
      if (!redoStack.length) return;
      undoStack.push(getCleanGraphJson());
      restore(redoStack.pop());
    }

    // ── Tool selection ──────────────────────────────────────────────────────
    function setTool(tool) {
      if (!editable) return;
      if (edgeSource) {
        edgeSource.removeClass("edge-source");
        edgeSource = null;
      }
      currentTool = tool;
      if (toolbar) {
        toolbar.querySelectorAll(".tool-btn").forEach((btn) => {
          const bar = btn.querySelector(".tool-active-bar");
          if (bar) bar.remove();
          btn.classList.remove("text-[#FAFAFA]");
        });
        const activeBtn = toolbar.querySelector(`.tool-btn[data-tool="${tool}"]`);
        if (activeBtn) {
          const bar = document.createElement("span");
          bar.className = "tool-active-bar absolute left-0 top-1 bottom-1 w-[2px] bg-[#FF4D00]";
          activeBtn.appendChild(bar);
          activeBtn.classList.add("text-[#FAFAFA]");
        }
      }
      setIndicator("MODE: " + tool.toUpperCase());
      cy.userPanningEnabled(tool !== "edge");
    }
    function setIndicator(text) {
      if (indicatorEl) indicatorEl.textContent = text;
    }

    // ── Initial / accepting toggles (both allow MANY states) ─────────────────
    function toggleInitialOn(node) {
      node.data("initial", !node.data("initial"));
      refreshInitialArrow(node);
      updateInitialHint();
    }
    function toggleAcceptingOn(node) {
      node.data("accepting", !node.data("accepting"));
    }
    function toggleInitial() {
      if (!editable) return;
      const selected = cy.$("node:selected").filter((n) => !n.data("phantom"));
      if (!selected.length) return;
      saveSnapshot();
      selected.forEach(toggleInitialOn);
      syncGraphData();
    }
    function toggleAccepting() {
      if (!editable) return;
      const selected = cy.$("node:selected").filter((n) => !n.data("phantom"));
      if (!selected.length) return;
      saveSnapshot();
      selected.forEach(toggleAcceptingOn);
      syncGraphData();
    }

    // ── Initial-state entry arrow (phantom node approach, one per initial) ────
    function refreshInitialArrow(node) {
      cy.remove(`#phantom_${node.id()}`);
      cy.remove(`#phantom_edge_${node.id()}`);
      if (node.data("initial")) {
        const pos = node.position();
        cy.add([
          { group: "nodes", data: { id: `phantom_${node.id()}`, phantom: true }, position: { x: pos.x - 70, y: pos.y } },
          { group: "edges", data: { id: `phantom_edge_${node.id()}`, source: `phantom_${node.id()}`, target: node.id(), phantom: true } },
        ]);
      }
    }
    function refreshAllInitialArrows() {
      cy.remove("[?phantom]");
      cy.nodes().filter((n) => n.data("initial") && !n.data("phantom")).forEach(refreshInitialArrow);
    }

    // ── Canvas hints ────────────────────────────────────────────────────────
    function updateInitialHint() {
      if (!noInitialHintEl) return;
      const hasInitial = cy.nodes("[?initial]").filter((n) => !n.data("phantom")).length > 0;
      noInitialHintEl.classList.toggle("hidden", hasInitial);
    }
    function updateEmptyCanvasHint() {
      if (!emptyCanvasHintEl) return;
      const hasNodes = cy.nodes().filter((n) => !n.data("phantom")).length > 0;
      emptyCanvasHintEl.classList.toggle("hidden", hasNodes);
    }

    function clearCanvas() {
      if (!editable) return;
      if (cy.nodes().filter((n) => !n.data("phantom")).length === 0) return;
      saveSnapshot();
      cy.elements().remove();
      updateInitialHint();
      updateEmptyCanvasHint();
      syncGraphData();
    }

    function loadElements(els) {
      saveSnapshot();
      cy.elements().remove();
      cy.add(JSON.parse(JSON.stringify(els)));
      cy.layout({ name: "preset" }).run();
      refreshAllInitialArrows();
      updateInitialHint();
      updateEmptyCanvasHint();
      syncGraphData();
      if (fitOnLoad) cy.fit(cy.elements("[!phantom]"), 80);
    }

    // ── Inline editors ──────────────────────────────────────────────────────
    function panelBase(leftPx, topPx) {
      const existing = document.getElementById(id + "-inline-editor");
      if (existing) existing.remove();
      const panel = document.createElement("div");
      panel.id = id + "-inline-editor";
      panel.style.cssText = `
        position: absolute; left: ${leftPx}px; top: ${topPx}px;
        width: 175px; background: #111111; border: 1px solid #FF4D00;
        border-radius: 6px; padding: 8px; z-index: 100;
        display: flex; flex-direction: column; gap: 5px;
        box-shadow: 0 4px 16px rgba(0,0,0,0.6);
      `;
      return panel;
    }
    const FIELD_LABEL = "font-family:monospace;font-size:10px;color:#6B6B6B;letter-spacing:0.1em;";
    const INPUT_STYLE = `
      background:#0A0A0A;border:1px solid #2A2A2A;color:#FAFAFA;font-family:monospace;
      font-size:12px;padding:3px 7px;border-radius:3px;outline:none;width:100%;box-sizing:border-box;
    `;
    function checkRow(text, checked) {
      const row = document.createElement("label");
      row.style.cssText = "display:flex;align-items:center;gap:6px;margin-top:2px;cursor:pointer;padding:2px 0;";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = checked;
      box.style.cssText = "accent-color:#FF4D00;width:13px;height:13px;cursor:pointer;flex-shrink:0;";
      const span = document.createElement("span");
      span.textContent = text;
      span.style.cssText = FIELD_LABEL + "user-select:none;";
      row.appendChild(box);
      row.appendChild(span);
      return { row, box };
    }

    function openNodeEditor(node) {
      const pos = node.renderedPosition();
      const rect = mount.getBoundingClientRect();
      const panel = panelBase(pos.x + rect.left - 85, pos.y + rect.top + 28);

      const nameLabel = document.createElement("div");
      nameLabel.textContent = "NAME";
      nameLabel.style.cssText = FIELD_LABEL;
      const nameInput = document.createElement("input");
      nameInput.type = "text";
      nameInput.value = node.data("name") || node.id();
      nameInput.style.cssText = INPUT_STYLE;

      const initial = checkRow("INITIAL STATE", !!node.data("initial"));
      const accepting = checkRow("ACCEPTING STATE", !!node.data("accepting"));

      panel.appendChild(nameLabel);
      panel.appendChild(nameInput);
      panel.appendChild(initial.row);
      panel.appendChild(accepting.row);
      document.body.appendChild(panel);
      nameInput.focus();
      nameInput.select();

      let committed = false;
      function commit() {
        if (committed) return;
        committed = true;
        saveSnapshot();
        node.data("name", nameInput.value.trim() || node.id());
        const wasInitial = !!node.data("initial");
        node.data("initial", initial.box.checked);
        node.data("accepting", accepting.box.checked);
        if (wasInitial !== initial.box.checked) {
          refreshInitialArrow(node);
          updateInitialHint();
        }
        panel.remove();
        document.removeEventListener("mousedown", outsideHandler);
        syncGraphData();
      }
      function outsideHandler(e) {
        if (!panel.contains(e.target)) commit();
      }
      setTimeout(() => document.addEventListener("mousedown", outsideHandler), 0);
      nameInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") { committed = true; panel.remove(); document.removeEventListener("mousedown", outsideHandler); }
      });
    }

    function openEdgeLabelEditor(edge, renderedPos) {
      const rect = mount.getBoundingClientRect();
      const panel = panelBase(renderedPos.x + rect.left - 85, renderedPos.y + rect.top + 14);

      const lbl = document.createElement("div");
      lbl.textContent = "TRANSITION LABEL";
      lbl.style.cssText = FIELD_LABEL;
      const hint = document.createElement("div");
      hint.textContent = "boolean over the alphabet, e.g.  a & !b   or   1";
      hint.style.cssText = "font-family:monospace;font-size:9px;color:#3A3A3A;line-height:1.4;";
      const labelInput = document.createElement("input");
      labelInput.type = "text";
      labelInput.value = edge.data("label") || "";
      labelInput.placeholder = "a & !b";
      labelInput.style.cssText = INPUT_STYLE;

      panel.appendChild(lbl);
      panel.appendChild(labelInput);
      panel.appendChild(hint);
      document.body.appendChild(panel);
      labelInput.focus();
      labelInput.select();

      let committed = false;
      function commit() {
        if (committed) return;
        committed = true;
        saveSnapshot();
        edge.data("label", labelInput.value.trim() || "1");
        panel.remove();
        document.removeEventListener("mousedown", outsideHandler);
        syncGraphData();
      }
      function outsideHandler(e) {
        if (!panel.contains(e.target)) commit();
      }
      setTimeout(() => document.addEventListener("mousedown", outsideHandler), 0);
      labelInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") { committed = true; panel.remove(); document.removeEventListener("mousedown", outsideHandler); }
      });
    }

    // ── Context menus ───────────────────────────────────────────────────────
    function renderMenu(items, left, top) {
      if (contextMenu) { contextMenu.remove(); contextMenu = null; }
      contextMenu = document.createElement("div");
      contextMenu.style.cssText = `
        position: fixed; left: ${left}px; top: ${top}px;
        background: #111111; border: 1px solid #2A2A2A; border-radius: 6px;
        padding: 4px 0; z-index: 200; min-width: 190px;
        box-shadow: 0 4px 16px rgba(0,0,0,0.6);
      `;
      items.forEach((item) => {
        const btn = document.createElement("button");
        btn.textContent = item.label;
        btn.style.cssText = `
          display: block; width: 100%; text-align: left; padding: 7px 14px;
          font-family: monospace; font-size: 12px; color: #FAFAFA;
          background: transparent; border: none; cursor: pointer; white-space: nowrap;
        `;
        btn.onmouseenter = () => { btn.style.background = "#1A1A1A"; btn.style.color = "#FF4D00"; };
        btn.onmouseleave = () => { btn.style.background = "transparent"; btn.style.color = "#FAFAFA"; };
        btn.onclick = () => { item.action(); if (contextMenu) { contextMenu.remove(); contextMenu = null; } };
        contextMenu.appendChild(btn);
      });
      document.body.appendChild(contextMenu);
      setTimeout(() => {
        document.addEventListener("mousedown", function closeMenu(e) {
          if (contextMenu && !contextMenu.contains(e.target)) {
            contextMenu.remove();
            contextMenu = null;
            document.removeEventListener("mousedown", closeMenu);
          }
        });
      }, 0);
    }

    function showNodeMenu(node, renderedPos) {
      const rect = mount.getBoundingClientRect();
      renderMenu([
        { label: "Edit state", action: () => openNodeEditor(node) },
        {
          label: node.data("initial") ? "Clear initial  (I)" : "Set initial  (I)",
          action: () => { saveSnapshot(); toggleInitialOn(node); syncGraphData(); },
        },
        {
          label: node.data("accepting") ? "Clear accepting  (A)" : "Set accepting  (A)",
          action: () => { saveSnapshot(); toggleAcceptingOn(node); syncGraphData(); },
        },
        {
          label: "Add self-loop",
          action: () => {
            if (cy.edges(`[source = "${node.id()}"][target = "${node.id()}"]`).length > 0) {
              warn("A self-loop already exists on state <b>" + (node.data("name") || node.id()) + "</b>.");
              return;
            }
            saveSnapshot();
            cy.add({ group: "edges", data: { id: "e" + node.id() + "_self_" + Date.now(), source: node.id(), target: node.id(), label: "1" } });
            syncGraphData();
          },
        },
        {
          label: "Delete  (D)",
          action: () => { saveSnapshot(); node.remove(); updateInitialHint(); syncGraphData(); },
        },
      ], rect.left + renderedPos.x, rect.top + renderedPos.y);
    }

    function showEdgeMenu(edge, renderedPos) {
      const rect = mount.getBoundingClientRect();
      renderMenu([
        { label: "Edit label  (L)", action: () => openEdgeLabelEditor(edge, renderedPos) },
        { label: "Delete  (D)", action: () => { saveSnapshot(); edge.remove(); syncGraphData(); } },
      ], rect.left + renderedPos.x, rect.top + renderedPos.y);
    }

    // ── Interaction handlers (editable only) ────────────────────────────────
    if (editable) {
      cy.on("tap", "node", function (evt) {
        const node = evt.target;
        if (node.data("phantom")) return;
        if (currentTool === "delete") {
          saveSnapshot();
          node.remove();
          updateInitialHint();
          syncGraphData();
          return;
        }
        if (currentTool === "label") {
          openNodeEditor(node);
          return;
        }
        if (currentTool === "edge") {
          if (!edgeSource) {
            edgeSource = node;
            node.addClass("edge-source");
            setIndicator("MODE: EDGE — click target (or same node for self-loop)");
          } else if (edgeSource.id() === node.id()) {
            if (cy.edges(`[source = "${node.id()}"][target = "${node.id()}"]`).length > 0) {
              warn("A self-loop already exists on state <b>" + (node.data("name") || node.id()) + "</b>.");
            } else {
              saveSnapshot();
              cy.add({ group: "edges", data: { id: "e" + node.id() + "_self_" + Date.now(), source: node.id(), target: node.id(), label: "1" } });
              syncGraphData();
            }
            edgeSource.removeClass("edge-source");
            edgeSource = null;
            setIndicator("MODE: EDGE");
          } else {
            const srcId = edgeSource.id();
            const tgtId = node.id();
            if (cy.edges(`[source = "${srcId}"][target = "${tgtId}"]`).filter((e) => !e.data("phantom")).length > 0) {
              warn("A transition from <b>" + (edgeSource.data("name") || srcId) + "</b> to <b>" + (node.data("name") || tgtId) + "</b> already exists.");
            } else {
              saveSnapshot();
              cy.add({ group: "edges", data: { id: "e" + srcId + "_" + tgtId + "_" + Date.now(), source: srcId, target: tgtId, label: "1" } });
              syncGraphData();
            }
            edgeSource.removeClass("edge-source");
            edgeSource = null;
            setIndicator("MODE: EDGE");
          }
          return;
        }
      });

      cy.on("tap", "edge", function (evt) {
        const edge = evt.target;
        if (edge.data("phantom")) return;
        if (currentTool === "delete") {
          saveSnapshot();
          edge.remove();
          syncGraphData();
        } else if (currentTool === "label") {
          openEdgeLabelEditor(edge, evt.renderedPosition);
        }
      });

      cy.on("tap", function (evt) {
        if (evt.target !== cy) return;
        if (currentTool === "edge" && edgeSource) {
          edgeSource.removeClass("edge-source");
          edgeSource = null;
          setIndicator("MODE: EDGE");
        }
      });

      cy.on("dblclick", function (evt) {
        if (evt.target !== cy) return;
        saveSnapshot();
        const nid = nextNodeId();
        const isFirst = cy.nodes().filter((n) => !n.data("phantom")).length === 0;
        cy.add({ group: "nodes", data: { id: nid, name: nid, initial: isFirst, accepting: false }, position: evt.position });
        if (isFirst) refreshInitialArrow(cy.getElementById(nid));
        updateInitialHint();
        updateEmptyCanvasHint();
        syncGraphData();
      });

      cy.on("dblclick", "node", function (evt) {
        const node = evt.target;
        if (node.data("phantom")) return;
        if (currentTool === "edge") return;
        if (!node.inside()) return;
        openNodeEditor(node);
      });

      cy.on("dblclick", "edge", function (evt) {
        const edge = evt.target;
        if (edge.data("phantom")) return;
        if (currentTool === "edge") return;
        openEdgeLabelEditor(edge, evt.renderedPosition);
      });

      cy.on("cxttap", "node", function (evt) {
        if (evt.target.data("phantom")) return;
        showNodeMenu(evt.target, evt.renderedPosition);
      });
      cy.on("cxttap", "edge", function (evt) {
        if (evt.target.data("phantom")) return;
        showEdgeMenu(evt.target, evt.renderedPosition);
      });
      mount.addEventListener("contextmenu", (e) => e.preventDefault());

      cy.on("dragfree", "node", function (evt) {
        const node = evt.target;
        if (!node.data("phantom") && node.data("initial")) refreshInitialArrow(node);
        syncGraphData();
      });

      cy.on("remove", "node", function (evt) {
        const node = evt.target;
        if (!node.data("phantom")) {
          const phantom = cy.$(`#phantom_${node.id()}`);
          if (phantom.length) phantom.remove();
          updateInitialHint();
          updateEmptyCanvasHint();
        }
      });

      if (toolbar) {
        toolbar.querySelectorAll("[data-tool]").forEach((btn) => {
          btn.addEventListener("click", () => setTool(btn.getAttribute("data-tool")));
        });
        toolbar.querySelectorAll("[data-action]").forEach((btn) => {
          const action = btn.getAttribute("data-action");
          btn.addEventListener("click", () => {
            if (action === "undo") undoAction();
            else if (action === "redo") redoAction();
            else if (action === "toggle-initial") toggleInitial();
            else if (action === "toggle-accepting") toggleAccepting();
            else if (action === "clear") clearCanvas();
          });
        });
      }

      if (useKeyboard) {
        mount.addEventListener("mouseenter", () => { hovering = true; });
        mount.addEventListener("mouseleave", () => { hovering = false; });
        document.addEventListener("keydown", function (e) {
          if (!hovering) return;
          if (["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
          const key = e.key.toLowerCase();
          if (key === "n") setTool("node");
          if (key === "e") setTool("edge");
          if (key === "l") setTool("label");
          if (key === "d") setTool("delete");
          if (key === "i") toggleInitial();
          if (key === "a") toggleAccepting();
          if (key === "z" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); undoAction(); }
          if (key === "y" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); redoAction(); }
          if (key === "delete" || key === "backspace") {
            const sel = cy.$(":selected").filter((el) => !el.data("phantom"));
            if (sel.length) { saveSnapshot(); sel.remove(); updateInitialHint(); syncGraphData(); }
          }
        });
      }
    }

    // ── Node hover tooltip ──────────────────────────────────────────────────
    if (tooltipEl && tooltipNameEl && tooltipPropsEl) {
      cy.on("mouseover", "node", function (evt) {
        const node = evt.target;
        if (node.data("phantom")) return;
        const flags = [];
        if (node.data("initial")) flags.push("initial");
        if (node.data("accepting")) flags.push("accepting");
        tooltipNameEl.textContent = node.data("name") || node.id();
        tooltipPropsEl.textContent = flags.length ? flags.join(" · ") : "ordinary state";
        tooltipEl.classList.remove("hidden");

        const rp = node.renderedPosition();
        const nodeR = node.renderedWidth() / 2 + 4;
        const tipW = tooltipEl.offsetWidth;
        const tipH = tooltipEl.offsetHeight;
        const canvasW = mount.offsetWidth;
        let left = rp.x - tipW / 2;
        let top = rp.y - nodeR - tipH;
        left = Math.max(8, Math.min(left, canvasW - tipW - 8));
        if (top < 8) top = rp.y + nodeR + 4;
        tooltipEl.style.left = left + "px";
        tooltipEl.style.top = top + "px";
      });
      cy.on("mouseout", "node", () => tooltipEl.classList.add("hidden"));
      cy.on("tap", "node", () => tooltipEl.classList.add("hidden"));
    }

    // ── Structure validation (shared by callers before submit) ───────────────
    // A valid drawing has ≥1 state, ≥1 initial and ≥1 accepting state, and every
    // transition labelled. Dead states (no successor) are allowed — a run that
    // cannot continue is simply not accepting, not an authoring error.
    function validateStructure() {
      const realNodes = cy.nodes().filter((n) => !n.data("phantom"));
      if (realNodes.length === 0) return "Automaton is empty — add at least one state.";
      if (realNodes.filter((n) => n.data("initial")).length === 0) {
        return "No initial state — select a state and press I (or right-click) to mark it initial.";
      }
      if (realNodes.filter((n) => n.data("accepting")).length === 0) {
        return "No accepting state — mark at least one state accepting (press A) so some run can be accepted.";
      }
      const unlabelled = cy.edges()
        .filter((e) => !e.data("phantom"))
        .filter((e) => !(e.data("label") || "").trim());
      if (unlabelled.length > 0) {
        return "Every transition needs a boolean label (e.g. a, a & !b, or 1). Some are empty.";
      }
      return null;
    }

    // ── Boot ────────────────────────────────────────────────────────────────
    if (editable) setTool("node");
    refreshAllInitialArrows();
    updateInitialHint();
    updateEmptyCanvasHint();
    syncGraphData();
    if (fitOnLoad) cy.ready(() => cy.fit(cy.elements("[!phantom]"), 80));

    const api = {
      id,
      cy,
      setTool,
      undo: undoAction,
      redo: redoAction,
      toggleInitial,
      toggleAccepting,
      clear: clearCanvas,
      loadElements,
      getData: getCleanGraphJson,
      sync: syncGraphData,
      validateStructure,
      warn,
      fit: () => cy.fit(cy.elements("[!phantom]"), 80),
    };

    window.BuchiEditors = window.BuchiEditors || {};
    window.BuchiEditors[id] = api;
    return api;
  }

  window.initBuchiEditor = initBuchiEditor;
})();
