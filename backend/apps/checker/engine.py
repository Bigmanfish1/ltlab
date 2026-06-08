"""LTL model checking engine built on SPOT.

Three public functions cover the full pipeline:

  cytoscape_to_kripke  — Cytoscape.js JSON  → SPOT kripke_graph + BDD dict
  check_ltl            — kripke_graph + formula → satisfied / violated + lasso
  lasso_to_trace_steps — raw lasso → per-step dicts for the counterexample page

The lasso returned by SPOT has two parts:
  prefix — finite path from the initial state to the cycle entry
  cycle  — the repeating loop that witnesses the violation

Each trace step consumed by the counterexample page template has:
  state      : str            node ID (e.g. "s0")
  props      : list[str]      atomic propositions holding at this state
  ok         : bool           True = prefix (formula held), False = cycle (violation)
  highlight  : str            formula token to underline in the formula display
  reason     : str            plain-English educational explanation
  cycle_back : bool           True on the last cycle step (the loop-closing transition)
"""

from __future__ import annotations

try:
    import spot  # type: ignore[import]
    _SPOT_AVAILABLE = True

    # BDD utility functions (bdd_ithvar, bdd_nithvar) and the bddtrue constant
    # live in the `spot` namespace in older SPOT builds, but in `spot.buddy` (or
    # a standalone `buddy` package) in SPOT 2.13 / spottl.  Probe all locations
    # at import time so the rest of the module can call _bdd_ithvar() etc.
    _bdd_ithvar  = getattr(spot, "bdd_ithvar",  None)
    _bdd_nithvar = getattr(spot, "bdd_nithvar", None)
    _bddtrue     = getattr(spot, "bddtrue",     None)

    if _bdd_ithvar is None:
        for _mod_name in ("spot.buddy", "buddy"):
            try:
                _bm = __import__(_mod_name, fromlist=["bdd_ithvar"])
                _bdd_ithvar  = getattr(_bm, "bdd_ithvar",  None)
                _bdd_nithvar = getattr(_bm, "bdd_nithvar", None)
                _bddtrue     = getattr(_bm, "bddtrue",     _bddtrue)
                if _bdd_ithvar is not None:
                    break
            except ImportError:
                continue

except ImportError:
    _SPOT_AVAILABLE = False
    _bdd_ithvar  = None
    _bdd_nithvar = None
    _bddtrue     = None


# ── Guard ────────────────────────────────────────────────────────────────────

def _require_spot() -> None:
    if not _SPOT_AVAILABLE:
        raise RuntimeError(
            "SPOT is not installed. "
            "Add 'spottl==2.13.0.1' to requirements.txt and rebuild the Docker image."
        )
    if _bdd_ithvar is None:
        raise RuntimeError(
            "SPOT is installed but the BDD utility functions (bdd_ithvar/bdd_nithvar) "
            "could not be located.  They should be in spot.buddy or the buddy package."
        )


# ── Formula normalisation ────────────────────────────────────────────────────

def _normalize_formula(formula_str: str) -> str:
    """Translate Unicode LTL operators to SPOT-compatible ASCII."""
    return (
        formula_str
        .replace("¬", "!")
        .replace("∧", "&")
        .replace("∨", "|")
        .replace("→", "->")
        .replace("↔", "<->")
    )


# ── 1. Kripke graph conversion ───────────────────────────────────────────────

def cytoscape_to_kripke(graph: dict) -> tuple:
    """Convert a Cytoscape.js JSON graph dict to a SPOT kripke_graph.

    Phantom elements (used for the initial-state arrow UI) are stripped before
    conversion so they never reach SPOT.

    Returns
    -------
    (kripke_graph, bdd_dict, spot_id_to_node_id)
    where spot_id_to_node_id maps integer SPOT state IDs back to node-ID strings
    so that trace steps can be labelled with the user's own state names.

    Raises
    ------
    ValueError  for structural problems (no states, no/multiple initial states).
    """
    _require_spot()

    elements = graph.get("elements", {})
    raw_nodes = [
        n for n in elements.get("nodes", [])
        if not n.get("data", {}).get("phantom")
    ]
    raw_edges = [
        e for e in elements.get("edges", [])
        if not e.get("data", {}).get("phantom")
    ]

    if not raw_nodes:
        raise ValueError("Graph has no states.")

    initial_nodes = [n for n in raw_nodes if n["data"].get("initial")]
    if len(initial_nodes) == 0:
        raise ValueError("No initial state defined.")
    if len(initial_nodes) > 1:
        raise ValueError("Multiple initial states found — exactly one is required.")

    # Collect every atomic proposition used anywhere in the graph.
    all_aps: list[str] = []
    for n in raw_nodes:
        for p in n["data"].get("props", []):
            if p and p not in all_aps:
                all_aps.append(p)

    d = spot.make_bdd_dict()
    k = spot.make_kripke_graph(d)

    # Register APs with the graph and remember their BDD variable indices.
    ap_bdd_vars: dict[str, int] = {}
    for ap_name in all_aps:
        ap_bdd_vars[ap_name] = k.register_ap(spot.formula.ap(ap_name))

    # Create one SPOT state per graph node; label it with a full BDD
    # assignment over every registered AP (positive for held, negative for not).
    node_id_to_spot: dict[str, int] = {}
    spot_id_to_node: dict[int, str] = {}

    # Compute a BDD for TRUE once, used as the conjunction seed per state.
    # When APs exist we derive it as (p | !p) which is reliable across all
    # SPOT/buddy versions.  If there are no APs we fall back to the cached
    # _bddtrue constant resolved at import time.
    if ap_bdd_vars:
        _first_var = next(iter(ap_bdd_vars.values()))
        _bdd_true = _bdd_ithvar(_first_var) | _bdd_nithvar(_first_var)
    elif _bddtrue is not None:
        _bdd_true = _bddtrue
    else:
        raise RuntimeError(
            "Cannot access the BDD true constant from this SPOT installation."
        )

    for n in raw_nodes:
        node_id = n["data"]["id"]
        node_props = set(n["data"].get("props", []))
        # Build the BDD label — new_state() requires it as its argument.
        bdd_label = _bdd_true
        for ap_name, bdd_var in ap_bdd_vars.items():
            if ap_name in node_props:
                bdd_label &= _bdd_ithvar(bdd_var)
            else:
                bdd_label &= _bdd_nithvar(bdd_var)
        spot_sid = k.new_state(bdd_label)
        node_id_to_spot[node_id] = spot_sid
        spot_id_to_node[spot_sid] = node_id

    k.set_init_state(node_id_to_spot[initial_nodes[0]["data"]["id"]])

    for e in raw_edges:
        src = e["data"].get("source")
        tgt = e["data"].get("target")
        if src in node_id_to_spot and tgt in node_id_to_spot:
            k.new_edge(node_id_to_spot[src], node_id_to_spot[tgt])

    return k, d, spot_id_to_node


# ── 2. LTL check ─────────────────────────────────────────────────────────────

def check_ltl(kripke, bdd_dict, formula_str: str) -> dict:
    """Run the LTL model check against an explicit Kripke structure.

    We search for a run satisfying ¬φ, which is exactly a counterexample to φ.

    Returns
    -------
    {"result": "satisfied"}
    or
    {"result": "violated",
     "prefix": [{"spot_id": int}, ...],
     "cycle":  [{"spot_id": int}, ...]}

    Each step's spot_id is an integer SPOT state number; call lasso_to_trace_steps
    (which uses the spot_id_to_node map from cytoscape_to_kripke) to convert to
    human-readable trace dicts.

    Raises
    ------
    ValueError  if the formula cannot be parsed or translated.
    """
    _require_spot()

    normalised = _normalize_formula(formula_str)
    try:
        f = spot.formula(normalised)
    except (SyntaxError, RuntimeError) as exc:
        raise ValueError(f"Invalid LTL formula: {exc}") from exc

    try:
        neg_aut = spot.translate(spot.formula.Not(f), dict=bdd_dict)
    except Exception as exc:
        raise ValueError(f"Could not translate formula: {exc}") from exc

    run = kripke.intersecting_run(neg_aut)
    if run is None:
        return {"result": "satisfied"}

    run = run.reduce()

    # In spottl / SPOT 2.13, kripke.intersecting_run() returns a run whose
    # steps live in the PRODUCT automaton (kripke × negated TBA).  The step.s
    # field is a product-state pointer whose hash() combines both halves and
    # therefore never matches the plain kripke state-number keys we built in
    # cytoscape_to_kripke.
    #
    # However, step.label is set to the outgoing transition's condition which,
    # for a kripke structure, equals cond(current_kripke_state).  This is
    # because all outgoing transitions from kripke state s carry cond(s), and
    # the product transition is valid only when the TBA condition is implied by
    # cond(s), so cond(s) & tba_cond = cond(s).
    #
    # We identify the kripke state for each step by scanning state_condition(n)
    # for all n and comparing with BDD equality (== on buddy bdd objects tests
    # canonical root-node identity, i.e. same boolean function).
    try:
        n_states = kripke.num_states()
    except Exception:
        n_states = 0

    def _label_to_sid(label) -> int | None:
        """Return the kripke state number whose BDD condition matches label."""
        for sid in range(n_states):
            try:
                if kripke.state_condition(sid) == label:
                    return sid
            except Exception:
                pass
        return None

    def _step_sid(step) -> int:
        sid = _label_to_sid(step.label)
        if sid is not None:
            return sid
        # Fallback for older SPOT builds where step.s is already an integer
        # state number (or its hash() returns a usable key).
        s = step.s
        if isinstance(s, int):
            return s
        try:
            return s.hash()
        except AttributeError:
            return id(s)

    return {
        "result": "violated",
        "prefix": [{"spot_id": _step_sid(step)} for step in run.prefix],
        "cycle":  [{"spot_id": _step_sid(step)} for step in run.cycle],
    }


# ── 3. Lasso → educational trace steps ──────────────────────────────────────

def lasso_to_trace_steps(
    prefix: list[dict],
    cycle: list[dict],
    formula_str: str,
    graph: dict,
    spot_id_to_node: dict[int, str],
) -> list[dict]:
    """Convert a SPOT lasso into per-step dicts for the counterexample page.

    Prefix steps get ok=True  ("system was running fine up to here").
    Cycle steps get  ok=False ("this infinite loop witnesses the violation").
    The last cycle step also gets cycle_back=True (marks the loop-closing edge).
    """
    elements = graph.get("elements", {})
    raw_nodes = [
        n for n in elements.get("nodes", [])
        if not n.get("data", {}).get("phantom")
    ]
    props_by_node: dict[str, list[str]] = {
        n["data"]["id"]: list(n["data"].get("props", []))
        for n in raw_nodes
    }

    normalised = _normalize_formula(formula_str)
    try:
        f = spot.formula(normalised)
    except Exception:
        f = None
    top_op, inner_str = _top_op(f)

    steps: list[dict] = []

    for step in prefix:
        node_id = spot_id_to_node.get(step["spot_id"], str(step["spot_id"]))
        props = props_by_node.get(node_id, [])
        steps.append({
            "state":      node_id,
            "props":      props,
            "ok":         True,
            "highlight":  _highlight(True,  props, formula_str, top_op, inner_str),
            "reason":     _reason(True,  props, formula_str, top_op, inner_str, False),
            "cycle_back": False,
        })

    for i, step in enumerate(cycle):
        node_id = spot_id_to_node.get(step["spot_id"], str(step["spot_id"]))
        props = props_by_node.get(node_id, [])
        is_last = (i == len(cycle) - 1)
        steps.append({
            "state":      node_id,
            "props":      props,
            "ok":         False,
            "highlight":  _highlight(False, props, formula_str, top_op, inner_str),
            "reason":     _reason(False, props, formula_str, top_op, inner_str, is_last),
            "cycle_back": is_last,
        })

    return steps


# ── Internal helpers ──────────────────────────────────────────────────────────

def _top_op(f) -> tuple[str, str]:
    """Return (operator_name, inner_formula_string) for the top-level LTL operator.

    SPOT exposes operator constants at module level as spot.op_G, spot.op_F, etc.
    (not as spot.op.G — that namespace does not exist in the Python bindings).
    Use f._is(spot.op_X) as the idiomatic check since 'is' is a Python keyword.
    """
    if f is None:
        return ("unknown", "")
    try:
        if f._is(spot.op_G):
            return ("G", str(f[0]))
        if f._is(spot.op_F):
            return ("F", str(f[0]))
        if f._is(spot.op_X):
            return ("X", str(f[0]))
        if f._is(spot.op_U):
            return ("U", f"{f[0]} U {f[1]}")
        if f._is(spot.op_Not):
            return ("Not", str(f[0]))
        if f._is(spot.op_And):
            return ("And", "")
        if f._is(spot.op_Or):
            return ("Or", "")
        if f._is(spot.op_Implies):
            return ("Implies", f"{f[0]} -> {f[1]}")
    except Exception:
        pass
    return ("unknown", "")


def _highlight(ok: bool, props: list[str], formula_str: str, top_op: str, inner: str) -> str:
    """Return the formula token to underline at this step in the formula display."""

    def _first_prop_in_formula() -> str:
        for p in props:
            if p in formula_str:
                return p
        return ""

    if top_op == "G":
        if not ok:
            candidate = inner.replace("!", "¬").replace("&", "∧").replace("|", "∨").replace("->", "→")
            return candidate if candidate in formula_str else inner if inner in formula_str else ""
        return _first_prop_in_formula()

    if top_op == "F":
        if not ok:
            # Highlight "F <inner>" if that substring exists in the original formula
            candidate = "F " + inner.replace("!", "¬").replace("&", "∧").replace("|", "∨").replace("->", "→")
            if candidate in formula_str:
                return candidate
            return inner if inner in formula_str else ""
        return _first_prop_in_formula()

    if top_op == "U":
        if not ok:
            # Highlight the right-hand side (ψ) — the goal that was never reached
            parts = inner.split(" U ")
            rhs = parts[1].strip() if len(parts) == 2 else inner
            return rhs if rhs in formula_str else ""
        return _first_prop_in_formula()

    if top_op in ("Not", "X"):
        if not ok:
            return inner if inner in formula_str else ""
        return _first_prop_in_formula()

    return _first_prop_in_formula()


def _unicode_inner(inner: str) -> str:
    """Convert SPOT ASCII operators in inner formula back to Unicode for display."""
    return (
        inner
        .replace("!", "¬")
        .replace("&", "∧")
        .replace("|", "∨")
        .replace("->", "→")
    )


def _reason(
    ok: bool,
    props: list[str],
    formula_str: str,
    top_op: str,
    inner: str,
    is_cycle_back: bool,
) -> str:
    """Generate a plain-English educational explanation for this trace step."""
    prop_str = "{" + ", ".join(props) + "}" if props else "∅"
    inner_u = _unicode_inner(inner)

    if is_cycle_back:
        return (
            "This transition closes the infinite loop — the execution cycles back "
            "from here, repeating forever and making it impossible to satisfy the formula."
        )

    # ── G φ ──────────────────────────────────────────────────────────────────
    if top_op == "G":
        if ok:
            return (
                f"{prop_str} — the condition holds here as required by G (always). "
                "The formula is satisfied at this state."
            )
        return (
            f"G requires the condition to hold at EVERY state, but it fails here "
            f"(props: {prop_str}). Because this state is in an infinite loop, "
            "the formula is permanently violated."
        )

    # ── F φ ──────────────────────────────────────────────────────────────────
    if top_op == "F":
        if ok:
            return (
                f"{prop_str} — '{inner_u}' has not yet been reached. "
                "The system keeps running, still carrying the 'eventually' obligation."
            )
        return (
            f"F requires '{inner_u}' to eventually hold, but this cycle repeats "
            "forever without it ever becoming true. "
            "The liveness obligation can never be fulfilled."
        )

    # ── φ U ψ ─────────────────────────────────────────────────────────────────
    if top_op == "U":
        parts = inner.split(" U ")
        lhs_u = _unicode_inner(parts[0].strip()) if len(parts) == 2 else "φ"
        rhs_u = _unicode_inner(parts[1].strip()) if len(parts) == 2 else "ψ"
        if ok:
            return (
                f"{prop_str} — '{lhs_u}' holds here as required while waiting for '{rhs_u}'. "
                "The Until obligation is still active."
            )
        return (
            f"The Until operator requires '{rhs_u}' to eventually hold, "
            f"but this cycle shows '{rhs_u}' is never reached. "
            "The 'until' obligation is permanently violated."
        )

    # ── X φ ───────────────────────────────────────────────────────────────────
    if top_op == "X":
        if ok:
            return f"{prop_str} — checking the next-step condition (X {inner_u})."
        return (
            f"X requires '{inner_u}' to hold at the next state, "
            f"but this state (props: {prop_str}) is the next state and it fails."
        )

    # ── ¬ φ ───────────────────────────────────────────────────────────────────
    if top_op == "Not":
        if ok:
            return f"{prop_str} — the negated condition '{inner_u}' does not hold here (as expected)."
        return (
            f"The condition '{inner_u}' unexpectedly holds at this state (props: {prop_str}), "
            f"causing ¬({inner_u}) to be violated."
        )

    # ── Implies φ → ψ ─────────────────────────────────────────────────────────
    if top_op == "Implies":
        parts = inner.split(" -> ")
        ant_u = _unicode_inner(parts[0].strip()) if len(parts) == 2 else "φ"
        con_u = _unicode_inner(parts[1].strip()) if len(parts) == 2 else "ψ"
        if ok:
            return (
                f"{prop_str} — checking implication '{ant_u} → {con_u}'. "
                "The antecedent status is being evaluated."
            )
        return (
            f"The antecedent '{ant_u}' holds here but '{con_u}' does not (props: {prop_str}). "
            "This makes the implication false at this state."
        )

    # ── Fallback ──────────────────────────────────────────────────────────────
    if ok:
        return f"{prop_str} — formula conditions are satisfied at this state."
    return f"The formula condition fails at this state. Propositions holding: {prop_str}."
