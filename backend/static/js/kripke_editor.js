/**
 * kripke_editor.js — reusable Kripke-structure graph editor.
 *
 * A single factory, `initKripkeEditor(config)`, that turns a container into an
 * interactive Cytoscape-backed editor: add/remove states and transitions, edit
 * names & propositions, mark the (single) initial state, self-loops, undo/redo,
 * proposition chips, hover tooltips, and a right-click context menu.
 *
 * Every instance is fully closure-scoped — no globals leak — so the same editor
 * powers the sandbox page and, independently, one or more exercise questions on
 * the same page.
 *
 * Requires Cytoscape (window.cytoscape) loaded before init runs.
 *
 * The hidden <input> is kept in sync with the phantom-stripped graph JSON
 * (`cy.json()` shape: { elements: { nodes, edges } }) — the exact shape the
 * checker's verify/counterexample views consume — so wrapping the component in
 * a <form> submits the student's structure with no extra wiring.
 *
 * Config (all optional):
 *   id         Instance id / element-id prefix.              Default "kripke"
 *   mount      Canvas element/selector.                      Default #<id>-cy
 *   input      Hidden input element/selector.                Default #<id>-graph-data
 *   toolbar    Toolbar container ([data-tool]/[data-action]).Default #<id>-toolbar
 *   elements   Initial Cytoscape elements.                   Default demo graph
 *   editable   false → read-only viewer.                     Default true
 *   keyboard   Bind shortcuts while hovering the canvas.     Default true (editable)
 *   fitOnLoad  Fit the viewport to the graph on boot/load.   Default true
 *
 * Events (dispatched on the mount element, bubbling):
 *   kripke:warn    detail.html  — a user-facing warning (duplicate edge, etc.)
 *   kripke:change              — the graph changed (hidden input already synced)
 *
 * Returns an API and registers it at window.KripkeEditors[id].
 */
(function () {
  "use strict";

  // ── Proposition-name validation (mirrors server-side rules) ─────────────────
  const PROP_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
  const RESERVED_PROP_NAMES = new Set(["X", "F", "G", "U", "R", "W", "M", "true", "false", "tt", "ff"]);

  function validatePropNames(props) {
    for (const p of props) {
      if (!PROP_NAME_RE.test(p)) {
        return `"${p}" is invalid — names must start with a letter or underscore and contain only letters, digits, and underscores.`;
      }
      if (RESERVED_PROP_NAMES.has(p)) {
        return `"${p}" is a reserved LTL operator — choose a different name.`;
      }
    }
    return null;
  }

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
      // Initial state: normal white border — the entry arrow is the marker.
      // Orange is reserved for selection so the initial state is never
      // mistaken for a selected one.
      selector: "node[?initial]",
      style: { "border-color": "#FAFAFA", "border-width": 2 },
    },
    {
      selector: "node:selected",
      style: { "border-color": "#FF4D00", "border-width": 3, "background-color": "#1A0A00" },
    },
    {
      // Edge source highlight: lime so "drawing from here" is distinct from
      // selection (orange) and from the initial state (white).
      selector: ".edge-source",
      style: { "border-color": "#AEFC00", "border-width": 3, "background-color": "#0D1A00" },
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
      },
    },
    {
      selector: "edge:selected",
      style: { "line-color": "#FF4D00", "target-arrow-color": "#FF4D00" },
    },
    {
      // Self-loops rendered as a small loop at top-right of the node
      selector: "edge[source = target]",
      style: {
        "curve-style": "loop",
        "loop-direction": "-45deg",
        "loop-sweep": "-45deg",
        "control-point-step-size": 40,
      },
    },
    {
      // Phantom node: invisible point anchoring the initial-state entry arrow
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
      // Entry arrow from phantom node into the initial state
      selector: "edge[?phantom]",
      style: {
        width: 2,
        "line-color": "#FF4D00",
        "target-arrow-color": "#FF4D00",
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        events: "no",
      },
    },
  ];

  // Default graph used when no `elements` are supplied.
  const DEFAULT_ELEMENTS = [
    { data: { id: "s0", name: "s0", label: "s0", props: ["p"], initial: true }, position: { x: 220, y: 220 } },
    { data: { id: "s1", name: "s1", label: "s1", props: ["q"], initial: false }, position: { x: 460, y: 220 } },
    { data: { id: "s2", name: "s2", label: "s2", props: ["p", "q"], initial: false }, position: { x: 340, y: 380 } },
    { data: { id: "e0", source: "s0", target: "s1" } },
    { data: { id: "e1", source: "s1", target: "s2" } },
    { data: { id: "e2", source: "s2", target: "s0" } },
  ];

  function resolve(sel) {
    if (!sel) return null;
    return typeof sel === "string" ? document.querySelector(sel) : sel;
  }

  function initKripkeEditor(config) {
    config = config || {};
    const id = config.id || "kripke";
    const byId = (suffix) => document.getElementById(id + "-" + suffix);

    const mount = resolve(config.mount) || byId("cy");
    if (!mount) {
      console.error("initKripkeEditor: mount not found for id '" + id + "'");
      return null;
    }
    const input = resolve(config.input) || byId("graph-data");
    const toolbar = resolve(config.toolbar) || byId("toolbar");
    const indicatorEl = byId("indicator");
    const noInitialHintEl = byId("no-initial-hint");
    const emptyCanvasHintEl = byId("empty-canvas-hint");
    const chipsLayerEl = byId("prop-chips-layer");
    const tooltipEl = byId("node-tooltip");
    const tooltipNameEl = byId("node-tooltip-name");
    const tooltipPropsEl = byId("node-tooltip-props");

    const editable = config.editable !== false;
    const useKeyboard = config.keyboard !== false && editable;
    const fitOnLoad = config.fitOnLoad !== false;
    const elements = config.elements && config.elements.length ? config.elements : DEFAULT_ELEMENTS;

    // ── Instance state ──────────────────────────────────────────────────────
    let currentTool = "node";
    let edgeSource = null;
    const undoStack = [];
    const redoStack = [];
    let hovering = false;
    let contextMenu = null;

    // Node id counter — seeded past any existing sN id so we never collide.
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
      mount.dispatchEvent(new CustomEvent("kripke:warn", { bubbles: true, detail: { html } }));
    }
    function emitChange() {
      mount.dispatchEvent(new CustomEvent("kripke:change", { bubbles: true }));
    }

    // ── Cytoscape init ──────────────────────────────────────────────────────
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

    // ── Clean graph JSON (phantom elements stripped) ────────────────────────
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
      if (input) input.value = JSON.stringify(getCleanGraphJson());
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
      rebuildAllChips();
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
    // internal tool ids stay node/edge; the UI speaks Kripke-structure terms
    const TOOL_LABELS = { node: "STATE", edge: "TRANSITION", label: "LABEL", delete: "DELETE" };

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
      setIndicator("MODE: " + (TOOL_LABELS[tool] || tool.toUpperCase()));
      cy.userPanningEnabled(tool !== "edge");
    }
    function setIndicator(text) {
      if (indicatorEl) indicatorEl.textContent = text;
    }

    // ── Single-initial-state enforcement ────────────────────────────────────
    function setInitial(node) {
      const wasInitial = !!node.data("initial");
      cy.nodes().forEach((n) => n.data("initial", false));
      if (!wasInitial) node.data("initial", true);
      refreshAllInitialArrows();
      updateInitialHint();
    }
    function toggleInitial() {
      if (!editable) return;
      const selected = cy.$("node:selected").filter((n) => !n.data("phantom"));
      if (!selected.length) return;
      saveSnapshot();
      setInitial(selected.first());
      syncGraphData();
    }

    // ── Initial-state entry arrow (phantom node approach) ────────────────────
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

    // ── Clear canvas ────────────────────────────────────────────────────────
    function clearCanvas() {
      if (!editable) return;
      if (cy.nodes().filter((n) => !n.data("phantom")).length === 0) return;
      saveSnapshot();
      cy.elements().remove();
      updateInitialHint();
      updateEmptyCanvasHint();
      rebuildAllChips();
      syncGraphData();
    }

    // ── Load a full graph (example / exercise preset) ────────────────────────
    function loadElements(els) {
      saveSnapshot();
      cy.elements().remove();
      cy.add(JSON.parse(JSON.stringify(els)));
      cy.layout({ name: "preset" }).run();
      refreshAllInitialArrows();
      updateInitialHint();
      updateEmptyCanvasHint();
      rebuildAllChips();
      syncGraphData();
      if (fitOnLoad) cy.fit(cy.elements("[!phantom]"), 80);
    }

    // ── Proposition chip overlay ────────────────────────────────────────────
    // Props shown as small lime chip pills below each node in a separate HTML
    // layer so node circles stay clean (only the state name).
    const chipWraps = new Map(); // nodeId → chip-wrapper DOM element

    function chipWrapHtml(node) {
      const wrap = document.createElement("div");
      wrap.style.cssText =
        "position:absolute;display:flex;flex-wrap:wrap;justify-content:center;" +
        "gap:2px;transform:translateX(-50%);max-width:140px;transition:opacity 120ms;";
      const props = node.data("props") || [];
      if (props.length === 0) {
        const c = document.createElement("span");
        c.style.cssText =
          "font-family:monospace;font-size:12px;color:#2A2A2A;background:#0F0F0F;" +
          "border:1px solid #1A1A1A;padding:3px 9px;border-radius:4px;" +
          "white-space:nowrap;line-height:1.5;letter-spacing:0.03em;";
        c.textContent = "∅";
        wrap.appendChild(c);
      } else {
        props.forEach((p) => {
          const c = document.createElement("span");
          c.style.cssText =
            "font-family:monospace;font-size:12px;color:#AEFC00;background:#060E06;" +
            "border:1px solid #1A3A14;padding:3px 9px;border-radius:4px;" +
            "white-space:nowrap;line-height:1.5;letter-spacing:0.03em;";
          c.textContent = p;
          wrap.appendChild(c);
        });
      }
      return wrap;
    }
    function positionChipWrap(node, wrap) {
      const pos = node.renderedPosition();
      const r = node.renderedWidth() / 2;
      wrap.style.left = pos.x + "px";
      wrap.style.top = pos.y + r + 4 + "px";
    }
    function addChip(node) {
      if (!chipsLayerEl || node.data("phantom")) return;
      const wrap = chipWrapHtml(node);
      chipsLayerEl.appendChild(wrap);
      chipWraps.set(node.id(), wrap);
      positionChipWrap(node, wrap);
    }
    function removeChip(node) {
      const wrap = chipWraps.get(node.id());
      if (wrap) {
        wrap.remove();
        chipWraps.delete(node.id());
      }
    }
    function refreshChip(node) {
      if (!chipsLayerEl || node.data("phantom")) return;
      const existing = chipWraps.get(node.id());
      if (existing) existing.remove();
      const wrap = chipWrapHtml(node);
      chipsLayerEl.appendChild(wrap);
      chipWraps.set(node.id(), wrap);
      positionChipWrap(node, wrap);
    }
    function repositionAllChips() {
      cy.nodes().filter((n) => !n.data("phantom")).forEach((n) => {
        const wrap = chipWraps.get(n.id());
        if (wrap) positionChipWrap(n, wrap);
      });
    }
    function rebuildAllChips() {
      if (!chipsLayerEl) return;
      chipsLayerEl.innerHTML = "";
      chipWraps.clear();
      cy.nodes().filter((n) => !n.data("phantom")).forEach(addChip);
    }

    // ── Inline label editor ─────────────────────────────────────────────────
    function openLabelEditor(node) {
      const pos = node.renderedPosition();
      const rect = mount.getBoundingClientRect();

      const existing = document.getElementById(id + "-label-editor");
      if (existing) existing.remove();

      const inputStyle = `
        background: #0A0A0A; border: 1px solid #2A2A2A; color: #FAFAFA;
        font-family: monospace; font-size: 12px; padding: 3px 7px;
        border-radius: 3px; outline: none; width: 100%; box-sizing: border-box;
      `;

      const panel = document.createElement("div");
      panel.id = id + "-label-editor";
      panel.style.cssText = `
        position: absolute;
        left: ${pos.x + rect.left - 75}px;
        top:  ${pos.y + rect.top + 28}px;
        width: 155px; background: #111111; border: 1px solid #FF4D00;
        border-radius: 6px; padding: 8px; z-index: 100;
        display: flex; flex-direction: column; gap: 5px;
        box-shadow: 0 4px 16px rgba(0,0,0,0.6);
      `;

      const nameLabel = document.createElement("div");
      nameLabel.textContent = "NAME";
      nameLabel.style.cssText = "font-family:monospace;font-size:10px;color:#6B6B6B;letter-spacing:0.1em;";

      const nameInput = document.createElement("input");
      nameInput.type = "text";
      nameInput.value = node.data("name") || node.id();
      nameInput.placeholder = "state name";
      nameInput.style.cssText = inputStyle;

      const propsLabel = document.createElement("div");
      propsLabel.textContent = "PROPS";
      propsLabel.style.cssText = "font-family:monospace;font-size:10px;color:#6B6B6B;letter-spacing:0.1em;margin-top:2px;";

      const propsInput = document.createElement("input");
      propsInput.type = "text";
      propsInput.value = (node.data("props") || []).join(", ");
      propsInput.placeholder = "p, q, …";
      propsInput.style.cssText = inputStyle;

      // Initial-state toggle row — wrapping label makes the whole row clickable
      const initRow = document.createElement("label");
      initRow.style.cssText = "display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;padding:2px 0;";
      const initCheck = document.createElement("input");
      initCheck.type = "checkbox";
      initCheck.checked = !!node.data("initial");
      initCheck.style.cssText = "accent-color:#FF4D00;width:13px;height:13px;cursor:pointer;flex-shrink:0;";
      const initSpan = document.createElement("span");
      initSpan.textContent = "INITIAL STATE";
      initSpan.style.cssText = "font-family:monospace;font-size:10px;color:#6B6B6B;letter-spacing:0.1em;user-select:none;";
      initRow.appendChild(initCheck);
      initRow.appendChild(initSpan);

      panel.appendChild(nameLabel);
      panel.appendChild(nameInput);
      panel.appendChild(propsLabel);
      panel.appendChild(propsInput);
      panel.appendChild(initRow);
      document.body.appendChild(panel);
      nameInput.focus();
      nameInput.select();

      const errorEl = document.createElement("div");
      errorEl.style.cssText = "font-family:monospace;font-size:10px;color:#FF4D00;margin-top:2px;min-height:14px;";
      panel.appendChild(errorEl);

      let committed = false;
      function commit() {
        if (committed) return;
        const name = nameInput.value.trim() || node.id();
        const props = propsInput.value.split(",").map((s) => s.trim()).filter(Boolean);
        const propErr = validatePropNames(props);
        if (propErr) {
          errorEl.textContent = propErr;
          return;
        }
        errorEl.textContent = "";
        committed = true;
        saveSnapshot();
        const wasInitial = !!node.data("initial");
        const nowInitial = !!initCheck.checked;
        node.data("name", name);
        node.data("label", name);
        node.data("props", props);
        if (wasInitial !== nowInitial) {
          cy.nodes().forEach((n) => n.data("initial", false));
          if (nowInitial) node.data("initial", true);
          refreshAllInitialArrows();
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
        if (e.key === "Enter") { e.preventDefault(); propsInput.focus(); propsInput.select(); }
        if (e.key === "Escape") { committed = true; panel.remove(); document.removeEventListener("mousedown", outsideHandler); }
      });
      propsInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") { committed = true; panel.remove(); document.removeEventListener("mousedown", outsideHandler); }
      });
    }

    // ── Right-click context menu ────────────────────────────────────────────
    function showContextMenu(node, renderedPos) {
      if (contextMenu) { contextMenu.remove(); contextMenu = null; }

      const mountRect = mount.getBoundingClientRect();
      const menuLeft = mountRect.left + renderedPos.x;
      const menuTop = mountRect.top + renderedPos.y;

      contextMenu = document.createElement("div");
      contextMenu.style.cssText = `
        position: fixed; left: ${menuLeft}px; top: ${menuTop}px;
        background: #111111; border: 1px solid #2A2A2A; border-radius: 6px;
        padding: 4px 0; z-index: 200; min-width: 180px;
        box-shadow: 0 4px 16px rgba(0,0,0,0.6);
      `;

      const items = [
        { label: "Edit labels", action: () => openLabelEditor(node) },
        {
          label: node.data("initial") ? "Clear initial state  (I)" : "Set as initial state  (I)",
          action: () => { saveSnapshot(); setInitial(node); syncGraphData(); },
        },
        {
          label: "Add self-loop",
          action: () => {
            if (cy.edges(`[source = "${node.id()}"][target = "${node.id()}"]`).length > 0) {
              warn("A self-loop already exists on state <b>" + (node.data("name") || node.id()) + "</b>.");
              return;
            }
            saveSnapshot();
            cy.add({ group: "edges", data: { id: "e" + node.id() + "_self_" + Date.now(), source: node.id(), target: node.id() } });
            syncGraphData();
          },
        },
        {
          label: "Delete  (D)",
          action: () => { saveSnapshot(); node.remove(); updateInitialHint(); syncGraphData(); },
        },
      ];

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
          openLabelEditor(node);
          return;
        }
        if (currentTool === "edge") {
          if (!edgeSource) {
            edgeSource = node;
            node.addClass("edge-source");
            setIndicator("MODE: TRANSITION — click target (or same state for self-loop)");
          } else if (edgeSource.id() === node.id()) {
            if (cy.edges(`[source = "${node.id()}"][target = "${node.id()}"]`).length > 0) {
              warn("A self-loop already exists on state <b>" + (node.data("name") || node.id()) + "</b>.");
            } else {
              saveSnapshot();
              cy.add({ group: "edges", data: { id: "e" + node.id() + "_self_" + Date.now(), source: node.id(), target: node.id() } });
              syncGraphData();
            }
            edgeSource.removeClass("edge-source");
            edgeSource = null;
            setIndicator("MODE: TRANSITION");
          } else {
            const srcId = edgeSource.id();
            const tgtId = node.id();
            if (cy.edges(`[source = "${srcId}"][target = "${tgtId}"]`).filter((e) => !e.data("phantom")).length > 0) {
              warn("A transition from <b>" + (edgeSource.data("name") || srcId) + "</b> to <b>" + (node.data("name") || tgtId) + "</b> already exists.");
            } else {
              saveSnapshot();
              cy.add({ group: "edges", data: { id: "e" + srcId + "_" + tgtId + "_" + Date.now(), source: srcId, target: tgtId } });
              syncGraphData();
            }
            edgeSource.removeClass("edge-source");
            edgeSource = null;
            setIndicator("MODE: TRANSITION");
          }
          return;
        }
      });

      cy.on("tap", "edge", function (evt) {
        if (evt.target.data("phantom")) return;
        if (currentTool === "delete") {
          saveSnapshot();
          evt.target.remove();
          syncGraphData();
        }
      });

      // Background single-tap: cancel edge source / deselect (never adds a node)
      cy.on("tap", function (evt) {
        if (evt.target !== cy) return;
        if (currentTool === "edge" && edgeSource) {
          edgeSource.removeClass("edge-source");
          edgeSource = null;
          setIndicator("MODE: TRANSITION");
        }
      });

      // Double-click empty canvas: add node (works in every tool mode)
      cy.on("dblclick", function (evt) {
        if (evt.target !== cy) return;
        saveSnapshot();
        const nid = nextNodeId();
        const isFirstInitial = cy.nodes().filter((n) => !n.data("phantom") && n.data("initial")).length === 0;
        cy.add({ group: "nodes", data: { id: nid, name: nid, label: nid, props: [], initial: isFirstInitial }, position: evt.position });
        if (isFirstInitial) refreshInitialArrow(cy.getElementById(nid));
        updateInitialHint();
        updateEmptyCanvasHint();
        syncGraphData();
      });

      // Double-click node: open label editor (not in edge/delete tool)
      cy.on("dblclick", "node", function (evt) {
        const node = evt.target;
        if (node.data("phantom")) return;
        if (currentTool === "edge") return;
        if (!node.inside()) return;
        openLabelEditor(node);
      });

      // Right-click node: context menu
      cy.on("cxttap", "node", function (evt) {
        if (evt.target.data("phantom")) return;
        showContextMenu(evt.target, evt.renderedPosition);
      });
      mount.addEventListener("contextmenu", (e) => e.preventDefault());

      // Reposition phantom arrow when initial state is dragged
      cy.on("dragfree", "node", function (evt) {
        const node = evt.target;
        if (!node.data("phantom") && node.data("initial")) refreshInitialArrow(node);
        syncGraphData();
      });

      // Clean up phantom node when its host node is removed
      cy.on("remove", "node", function (evt) {
        const node = evt.target;
        if (!node.data("phantom")) {
          const phantom = cy.$(`#phantom_${node.id()}`);
          if (phantom.length) phantom.remove();
          updateInitialHint();
          updateEmptyCanvasHint();
        }
      });

      // Toolbar wiring (data attributes — no inline onclick globals)
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
            else if (action === "clear") clearCanvas();
          });
        });
      }

      // Keyboard shortcuts — act only while hovering this instance so multiple
      // editors on one page don't fight over key presses.
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
        const name = node.data("name") || node.id();
        const props = node.data("props") || [];
        const initial = node.data("initial") ? " · initial" : "";
        tooltipNameEl.textContent = name + initial;
        tooltipPropsEl.textContent = props.length ? `props: ${props.join(", ")}` : "no propositions";
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

    // ── Chip overlay hooks ──────────────────────────────────────────────────
    cy.on("add", "node", (evt) => addChip(evt.target));
    cy.on("remove", "node", (evt) => removeChip(evt.target));
    cy.on("data", "node", (evt) => refreshChip(evt.target));
    cy.on("pan zoom resize layoutstop", repositionAllChips);
    cy.on("position", "node", (evt) => {
      const wrap = chipWraps.get(evt.target.id());
      if (wrap) positionChipWrap(evt.target, wrap);
    });

    // ── Structure validation (shared by callers before submit/verify) ────────
    // Returns a user-facing error string, or null when the structure is a valid
    // total Kripke structure (≥1 state, exactly one initial, no deadlocks).
    function validateStructure() {
      const realNodes = cy.nodes().filter((n) => !n.data("phantom"));
      if (realNodes.length === 0) return "Graph is empty — add at least one state before verifying.";
      const initials = realNodes.filter((n) => n.data("initial"));
      if (initials.length === 0) return "No initial state — select a state and press I (or right-click) to mark it as initial.";
      if (initials.length > 1) return "Multiple initial states found — exactly one is required. Select the correct state and press I.";
      const deadlocks = realNodes
        .filter((n) => n.outgoers("edge").filter((ed) => !ed.data("phantom")).length === 0)
        .map((n) => n.data("name") || n.id());
      if (deadlocks.length > 0) {
        return (
          "State(s) with no outgoing transition: " + deadlocks.join(", ") +
          ". Every state needs at least one successor (add a transition or a self-loop). " +
          "Otherwise the graph has no infinite paths and every formula holds vacuously."
        );
      }
      return null;
    }

    // ── Boot ────────────────────────────────────────────────────────────────
    if (editable) setTool("node");
    refreshAllInitialArrows();
    updateInitialHint();
    updateEmptyCanvasHint();
    rebuildAllChips();
    syncGraphData();
    if (fitOnLoad) cy.ready(() => cy.fit(cy.elements("[!phantom]"), 80));

    const api = {
      id,
      cy,
      setTool,
      undo: undoAction,
      redo: redoAction,
      toggleInitial,
      clear: clearCanvas,
      loadElements,
      getData: getCleanGraphJson,
      sync: syncGraphData,
      validateStructure,
      warn,
      fit: () => cy.fit(cy.elements("[!phantom]"), 80),
    };

    window.KripkeEditors = window.KripkeEditors || {};
    window.KripkeEditors[id] = api;
    return api;
  }

  window.initKripkeEditor = initKripkeEditor;
})();
