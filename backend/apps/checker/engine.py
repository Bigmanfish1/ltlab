"""LTL model checking engine built on SPOT.

Four public functions cover the full pipeline:

  validate_request     — pre-flight structural complexity + AP-subset guard
  cytoscape_to_kripke  — Cytoscape.js JSON  → SPOT kripke_graph + BDD dict
  check_ltl            — kripke_graph + formula → satisfied / violated + lasso
  analyze_lasso        — raw lasso → per-step analysis for the counterexample page

The lasso returned by SPOT has two parts:
  prefix — finite path from the initial state to the cycle entry
  cycle  — the repeating loop that witnesses the violation

Together they describe an *ultimately periodic* infinite word
``w = prefix · cycle^ω``.  Because that word is fully determined, we can
evaluate any LTL subformula at any position of it.  ``analyze_lasso`` does
exactly this so each state can be classified individually instead of bluntly
marking the whole cycle as wrong.

analyze_lasso returns a dict:
  steps                : list[dict]   per-state analysis (see below)
  violation_kind       : str          "safety" (a concrete bad state exists)
                                       or "liveness" (the loop never fulfils
                                       an obligation — no single bad state)
  violating_subformula : str          subformula to highlight in the overview

Each trace step consumed by the counterexample page template has:
  state      : str   node ID (e.g. "s0")
  name       : str   human-readable state name (may differ from id after rename)
  props      : list[str]   atomic propositions holding at this state
  status     : str   one of
                 "satisfied"  the local obligation actively holds here
                 "vacuous"    the obligation does not apply here (e.g. the
                              antecedent of an implication is false)
                 "pending"    an obligation is still open / waiting here
                 "violating"  this is a state where the formula concretely breaks
  highlight  : str   subformula token to underline in the formula display
  reason     : str   plain-English educational explanation
  in_cycle   : bool  True when this position belongs to the repeating cycle
  cycle_back : bool  True on the last cycle step (the loop-closing transition)
"""

from __future__ import annotations

from functools import lru_cache

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


# ── Structural complexity limits (SPOT-grounded) ─────────────────────────────
# Translation blowup is ~2^(subformulas) × 2^|AP| in the worst case.
# These caps keep the negated-formula automaton in the low-thousands of states,
# which × ≤100 Kripke states stays sub-second and well within memory.
MAX_FORMULA_APS   = 8   # distinct atomic propositions in the formula
MAX_TEMPORAL_OPS  = 10  # temporal-operator nodes (X F G U R W M)
MAX_FORMULA_NODES = 40  # total AST nodes (spot.length equivalent)


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


# ── Formula AST helpers ──────────────────────────────────────────────────────

def _formula_node_count(f) -> int:
    """Count total AST nodes (equivalent to spot.length for our purposes)."""
    try:
        children = f.size()
    except Exception:
        return 1
    if children == 0:
        return 1
    return 1 + sum(_formula_node_count(f[i]) for i in range(children))


def _collect_formula_aps_set(f, ops: dict) -> set[str]:
    """Return the set of all atomic-proposition names in the formula tree."""
    aps: set[str] = set()
    stack = [f]
    while stack:
        node = stack.pop()
        tag = _op_tag(node, ops)
        if tag == "ap":
            try:
                aps.add(node.ap_name())
            except Exception:
                pass
        else:
            try:
                for i in range(node.size()):
                    stack.append(node[i])
            except Exception:
                pass
    return aps


def _count_temporal_ops(f, ops: dict) -> int:
    """Count temporal-operator nodes (X F G U R W M) in the formula tree."""
    temporal_tags = {"X", "F", "G", "U", "R", "W", "M"}
    count = 0
    stack = [f]
    while stack:
        node = stack.pop()
        tag = _op_tag(node, ops)
        if tag in temporal_tags:
            count += 1
        try:
            for i in range(node.size()):
                stack.append(node[i])
        except Exception:
            pass
    return count


# ── Pre-flight validation (PRIMARY DoS guard — call before translate()) ───────

def validate_formula(formula_str: str):
    """Parse + cap a single LTL formula; return the parsed ``spot.formula``.

    This is the formula-only half of the pre-flight guard, shared by the model
    checker (``validate_request``) and the equivalence explorer
    (``formula_relationship``).  Raises ValueError with a user-facing message.

    Checks (in order):
      1. Formula parses successfully.
      2. It is a pure LTL formula (rejects SERE/rational operators, which can
         crash SPOT natively when rebuilt — see misconceptions.py).
      3. AST-level structural caps (prevent 2^n blowup in translate()).
    """
    _require_spot()

    normalised = _normalize_formula(formula_str)
    try:
        f = spot.formula(normalised)
    except (SyntaxError, RuntimeError) as exc:
        raise ValueError(f"Invalid LTL formula: {exc}") from exc

    try:
        is_ltl = f.is_ltl_formula()
    except Exception:
        is_ltl = True
    if not is_ltl:
        raise ValueError(
            "Only LTL formulas are supported here (no SERE / rational operators)."
        )

    ops = _op_constants()

    # ── Structural caps ───────────────────────────────────────────────────────
    node_count = _formula_node_count(f)
    if node_count > MAX_FORMULA_NODES:
        raise ValueError(
            f"Formula is too complex ({node_count} subformulas) — "
            f"the sandbox supports at most {MAX_FORMULA_NODES}. "
            "Try splitting the formula into smaller pieces."
        )

    temporal = _count_temporal_ops(f, ops)
    if temporal > MAX_TEMPORAL_OPS:
        raise ValueError(
            f"Formula contains {temporal} temporal operators (X/F/G/U/R/W/M) — "
            f"the sandbox supports at most {MAX_TEMPORAL_OPS}."
        )

    formula_aps = _collect_formula_aps_set(f, ops)
    if len(formula_aps) > MAX_FORMULA_APS:
        raise ValueError(
            f"Formula references {len(formula_aps)} distinct propositions — "
            f"the sandbox supports at most {MAX_FORMULA_APS}."
        )

    return f


def validate_request(graph: dict, formula_str: str) -> None:
    """Validate the formula and graph before the expensive SPOT translation.

    Must be called at the top of run_ltl_check, before spot.translate().
    Raises ValueError with a user-friendly message on any violation so the
    check fails cleanly instead of blowing up memory/CPU.

    Checks (in order):
      1-3. Everything ``validate_formula`` covers (parse, LTL-only, structural caps).
      4.   Formula APs are a subset of propositions declared on graph states.
    """
    f = validate_formula(formula_str)
    ops = _op_constants()
    formula_aps = _collect_formula_aps_set(f, ops)

    # ── AP subset: formula must not reference undeclared propositions ─────────
    elements = graph.get("elements", {})
    declared_props: set[str] = set()
    for n in elements.get("nodes", []):
        if not n.get("data", {}).get("phantom"):
            for p in n["data"].get("props", []):
                declared_props.add(p)

    undeclared = formula_aps - declared_props
    if undeclared:
        names = ", ".join(sorted(undeclared))
        raise ValueError(
            f"Formula references proposition(s) not declared on any state: {names}. "
            "Add these propositions to the relevant states, or remove them from the formula."
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
    ValueError  for structural problems (no states, no/multiple initial states,
                or deadlock states with no outgoing transition).
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

    nodes_with_successor: set[str] = set()
    for e in raw_edges:
        src = e["data"].get("source")
        tgt = e["data"].get("target")
        if src in node_id_to_spot and tgt in node_id_to_spot:
            k.new_edge(node_id_to_spot[src], node_id_to_spot[tgt])
            nodes_with_successor.add(src)

    # A Kripke structure must have a TOTAL transition relation: every state
    # needs at least one outgoing transition (a self-loop counts).  A state
    # with no successor is a deadlock — no infinite path can pass through it,
    # so LTL formulas become vacuously true and the model checker would report
    # misleading "holds" results.  Reject such graphs with a clear message.
    deadlocks = [
        n["data"]["id"]
        for n in raw_nodes
        if n["data"]["id"] not in nodes_with_successor
    ]
    if deadlocks:
        names = ", ".join(sorted(deadlocks))
        raise ValueError(
            f"State(s) with no outgoing transition: {names}. "
            "A Kripke structure must be total — every state needs at least one "
            "successor. Add a transition or a self-loop. (Otherwise the graph has "
            "no infinite paths and every formula holds vacuously.)"
        )

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

    Each step's spot_id is an integer SPOT state number; call analyze_lasso
    (which uses the spot_id_to_node map from cytoscape_to_kripke) to convert to
    human-readable, per-state trace dicts.

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

    # kripke.intersecting_run() returns a run OF THE KRIPKE (its states are
    # kripke state pointers, not product states).  Digraph-backed automata in
    # SPOT expose state_number(state*) which maps a state pointer back to the
    # integer state number we used as the key in cytoscape_to_kripke.  That is
    # the canonical, version-stable way to recover the original node.
    def _step_sid(step) -> int:
        s = step.s
        if isinstance(s, int):
            return s
        try:
            return kripke.state_number(s)
        except Exception:
            pass
        # Last-resort fallback: match the step's outgoing condition against
        # each state's BDD condition (state_condition compares by canonical
        # BDD root, so equal boolean functions compare equal).
        try:
            for sid in range(kripke.num_states()):
                if kripke.state_condition(sid) == step.label:
                    return sid
        except Exception:
            pass
        return id(s)

    return {
        "result": "violated",
        "prefix": [{"spot_id": _step_sid(step)} for step in run.prefix],
        "cycle":  [{"spot_id": _step_sid(step)} for step in run.cycle],
    }


# ── 3. Lasso → per-state analysis ───────────────────────────────────────────

# Status values used to classify each state of the counterexample.
STATUS_SATISFIED = "satisfied"  # local obligation actively holds here
STATUS_VACUOUS = "vacuous"      # obligation does not apply here
STATUS_PENDING = "pending"      # an obligation is still open / waiting
STATUS_VIOLATING = "violating"  # this state concretely breaks the formula

KIND_SAFETY = "safety"          # a concrete bad state exists
KIND_LIVENESS = "liveness"      # the loop never fulfils an obligation


def _unicode_inner(inner: str) -> str:
    """Convert SPOT ASCII operators back to the Unicode used in the UI."""
    return (
        inner
        .replace("<->", "↔")
        .replace("->", "→")
        .replace("!", "¬")
        .replace("&", "∧")
        .replace("|", "∨")
    )


def _op_constants() -> dict:
    """Resolve SPOT op_* constants once (some are absent in older builds)."""
    names = (
        "tt", "ff", "ap", "Not", "And", "Or", "Implies", "Equiv", "Xor",
        "X", "F", "G", "U", "R", "W", "M",
    )
    if not _SPOT_AVAILABLE:
        return {}
    return {name: getattr(spot, "op_" + name, None) for name in names}


def _op_tag(node, ops: dict) -> str:
    """Return the canonical operator tag (e.g. "G", "Until", "ap") for a node."""
    if node is None:
        return "unknown"
    for tag, const in ops.items():
        if const is None:
            continue
        try:
            if node._is(const):
                return tag
        except Exception:
            continue
    return "unknown"


class _LassoWord:
    """Evaluate LTL subformulas over an ultimately periodic word (a lasso).

    The word is ``prefix · cycle^ω``.  Positions ``0 .. prefix_len-1`` are the
    prefix; ``prefix_len .. N-1`` are one iteration of the cycle, which then
    repeats forever.  Because the suffix from any position depends only on its
    *canonical* position within ``[0, N)``, results are memoised on that.
    """

    def __init__(self, props_by_pos: list, prefix_len: int, ops: dict):
        self.props = props_by_pos
        self.N = len(props_by_pos)
        self.P = prefix_len
        self.C = self.N - prefix_len            # cycle length (>= 1 for a lasso)
        self.ops = ops
        # One full pass over the prefix tail plus one full cycle is always
        # enough to decide F / G / U / R / W / M on an ultimately periodic word.
        self.horizon = self.N + max(self.C, 1) + 1
        self._memo: dict = {}

    def canon(self, pos: int) -> int:
        """Map any (possibly out-of-range) position to its canonical index."""
        if pos < self.N:
            return pos
        if self.C <= 0:
            return self.N - 1
        return self.P + ((pos - self.P) % self.C)

    def holds(self, node, pos: int) -> bool:
        cpos = self.canon(pos)
        key = (str(node), cpos)
        if key in self._memo:
            return self._memo[key]
        result = self._eval(node, cpos)
        self._memo[key] = result
        return result

    def _eval(self, node, cpos: int) -> bool:
        tag = _op_tag(node, self.ops)

        if tag == "tt":
            return True
        if tag == "ff":
            return False
        if tag == "ap":
            return node.ap_name() in self.props[cpos]
        if tag == "Not":
            return not self.holds(node[0], cpos)
        if tag == "And":
            return all(self.holds(node[i], cpos) for i in range(node.size()))
        if tag == "Or":
            return any(self.holds(node[i], cpos) for i in range(node.size()))
        if tag == "Implies":
            return (not self.holds(node[0], cpos)) or self.holds(node[1], cpos)
        if tag == "Equiv":
            return self.holds(node[0], cpos) == self.holds(node[1], cpos)
        if tag == "Xor":
            return self.holds(node[0], cpos) != self.holds(node[1], cpos)
        if tag == "X":
            return self.holds(node[0], cpos + 1)
        if tag == "F":
            return any(self.holds(node[0], cpos + k) for k in range(self.horizon))
        if tag == "G":
            return all(self.holds(node[0], cpos + k) for k in range(self.horizon))
        if tag == "U":
            for k in range(self.horizon):
                if self.holds(node[1], cpos + k):
                    return True
                if not self.holds(node[0], cpos + k):
                    return False
            return False
        if tag == "W":  # weak until: (a U b) | G a
            for k in range(self.horizon):
                if self.holds(node[1], cpos + k):
                    return True
                if not self.holds(node[0], cpos + k):
                    return False
            return True   # left held throughout the loop → G left
        if tag == "R":  # release: b holds up to & including when a does; or G b
            for k in range(self.horizon):
                if not self.holds(node[1], cpos + k):
                    return False
                if self.holds(node[0], cpos + k):
                    return True
            return True   # right held throughout the loop → G right
        if tag == "M":  # strong release: b U (a & b)
            for k in range(self.horizon):
                if not self.holds(node[1], cpos + k):
                    return False
                if self.holds(node[0], cpos + k):
                    return True
            return False

        return False  # unknown operator → conservative


def _focus(node, formula_str: str) -> str:
    """Return a substring of the original (Unicode) formula matching ``node``.

    Used to underline the relevant subformula.  Returns "" when no clean match
    exists, in which case the UI simply underlines nothing.
    """
    if node is None:
        return ""
    cand = _unicode_inner(str(node))
    if cand and cand in formula_str:
        return cand
    stripped = cand.strip("()")
    if stripped and stripped in formula_str:
        return stripped
    return ""


def _props_str(props) -> str:
    return "{" + ", ".join(props) + "}" if props else "∅"


def _responsible_child(node, word: "_LassoWord", ops: dict):
    """For a Boolean top operator, return the child that causes the failure."""
    tag = _op_tag(node, ops)
    try:
        if tag == "And":
            for i in range(node.size()):
                if not word.holds(node[i], 0):
                    return node[i]
        elif tag == "Or":
            # Every child is false on a counterexample; prefer a temporal one.
            for i in range(node.size()):
                if _op_tag(node[i], ops) in ("G", "F", "X", "U", "R", "W", "M"):
                    return node[i]
            return node[0]
        elif tag == "Implies":
            if word.holds(node[0], 0) and not word.holds(node[1], 0):
                return node[1]
            if not word.holds(node[0], 0):
                return node[0]
    except Exception:
        return None
    return None


def _classify(f, word: "_LassoWord", formula_str: str, ops: dict) -> tuple:
    """Classify every position of the lasso for formula ``f``.

    Returns (statuses, highlights, reasons, kind, violating_subformula).
    """
    N = word.N
    tag = _op_tag(f, ops)
    statuses = [STATUS_SATISFIED] * N
    highlights = [""] * N
    reasons = [""] * N

    def u(node) -> str:
        return _unicode_inner(str(node))

    def ps(i: int) -> str:
        return _props_str(word.props[i])

    def setp(i, status, highlight, reason):
        statuses[i] = status
        highlights[i] = highlight
        reasons[i] = reason

    # ── G ψ ───────────────────────────────────────────────────────────────────
    if tag == "G":
        body = f[0]
        btag = _op_tag(body, ops)
        is_impl = btag == "Implies"
        live_body = btag in ("F", "U", "W", "M")
        body_focus = _focus(body, formula_str)
        for i in range(N):
            if word.holds(body, i):
                if is_impl and not word.holds(body[0], i):
                    setp(i, STATUS_VACUOUS,
                         _focus(body[0], formula_str) or body_focus,
                         f"{ps(i)} — the antecedent of '{u(body)}' is false here, "
                         "so the implication is vacuously true. G places no "
                         "constraint on this state.")
                else:
                    setp(i, STATUS_SATISFIED, body_focus,
                         f"{ps(i)} — '{u(body)}' holds here, as G (always) requires.")
            else:
                if live_body:
                    setp(i, STATUS_PENDING, body_focus,
                         f"{ps(i)} — the eventuality '{u(body)}' is still unmet; "
                         "the loop repeats forever without ever fulfilling it.")
                else:
                    hl = _focus(body[1], formula_str) if is_impl else body_focus
                    setp(i, STATUS_VIOLATING, hl or body_focus,
                         f"{ps(i)} — G requires '{u(body)}' at every state, but it "
                         "fails here. This is the state that breaks the formula.")
        kind = KIND_LIVENESS if STATUS_VIOLATING not in statuses else KIND_SAFETY
        return statuses, highlights, reasons, kind, body_focus

    # ── F ψ ───────────────────────────────────────────────────────────────────
    if tag == "F":
        body = f[0]
        bf = _focus(body, formula_str)
        ever = any(word.holds(body, k) for k in range(N))
        for i in range(N):
            if word.holds(body, i):
                setp(i, STATUS_SATISFIED, bf,
                     f"{ps(i)} — '{u(body)}' holds here, fulfilling the eventually.")
            elif ever:
                setp(i, STATUS_PENDING, bf,
                     f"{ps(i)} — still waiting for '{u(body)}' here; the eventually "
                     "is fulfilled elsewhere in the run.")
            else:
                setp(i, STATUS_PENDING, bf,
                     f"{ps(i)} — still waiting for '{u(body)}'; it never occurs "
                     "anywhere in this run, so F can never be satisfied.")
        return statuses, highlights, reasons, KIND_LIVENESS, bf

    # ── ψ1 U ψ2 / ψ1 W ψ2 ─────────────────────────────────────────────────────
    if tag in ("U", "W"):
        a, b = f[0], f[1]
        af, bf = _focus(a, formula_str), _focus(b, formula_str)
        for i in range(N):
            if word.holds(b, i):
                setp(i, STATUS_SATISFIED, bf,
                     f"{ps(i)} — the target '{u(b)}' is reached here.")
            elif word.holds(a, i):
                setp(i, STATUS_PENDING, af,
                     f"{ps(i)} — '{u(a)}' holds while waiting for '{u(b)}'.")
            else:
                setp(i, STATUS_VIOLATING, bf,
                     f"{ps(i)} — neither the target '{u(b)}' nor the guard '{u(a)}' "
                     "holds here, so the until obligation breaks.")
        kind = KIND_SAFETY if STATUS_VIOLATING in statuses else KIND_LIVENESS
        return statuses, highlights, reasons, kind, bf

    # ── ψ1 R ψ2 / ψ1 M ψ2 ─────────────────────────────────────────────────────
    if tag in ("R", "M"):
        a, b = f[0], f[1]
        af, bf = _focus(a, formula_str), _focus(b, formula_str)
        for i in range(N):
            if not word.holds(b, i):
                setp(i, STATUS_VIOLATING, bf,
                     f"{ps(i)} — '{u(b)}' must stay true until release, but it "
                     "fails here.")
            elif word.holds(a, i):
                setp(i, STATUS_SATISFIED, af,
                     f"{ps(i)} — the release condition '{u(a)}' holds here.")
            else:
                setp(i, STATUS_PENDING, bf,
                     f"{ps(i)} — '{u(b)}' holds while awaiting release by '{u(a)}'.")
        kind = KIND_SAFETY if STATUS_VIOLATING in statuses else KIND_LIVENESS
        return statuses, highlights, reasons, kind, bf

    # ── X ψ ───────────────────────────────────────────────────────────────────
    if tag == "X":
        body = f[0]
        bf = _focus(body, formula_str)
        for i in range(N):
            if i == 0:
                setp(i, STATUS_PENDING, bf,
                     f"{ps(i)} — X checks the NEXT state for '{u(body)}'.")
            elif i == 1:
                if word.holds(body, 1):
                    setp(i, STATUS_SATISFIED, bf,
                         f"{ps(i)} — '{u(body)}' holds at the next state, as X requires.")
                else:
                    setp(i, STATUS_VIOLATING, bf,
                         f"{ps(i)} — X requires '{u(body)}' at this (the next) state, "
                         "but it fails here.")
            else:
                setp(i, STATUS_VACUOUS, "",
                     f"{ps(i)} — X only constrains the state right after the start; "
                     "this state is unconstrained.")
        return statuses, highlights, reasons, KIND_SAFETY, bf

    # ── ¬ ψ ───────────────────────────────────────────────────────────────────
    if tag == "Not":
        body = f[0]
        bf = _focus(body, formula_str)
        for i in range(N):
            if i == 0:
                if word.holds(body, 0):
                    setp(i, STATUS_VIOLATING, bf,
                         f"{ps(i)} — '{u(body)}' holds here, but ¬ requires it to be false.")
                else:
                    setp(i, STATUS_SATISFIED, bf,
                         f"{ps(i)} — '{u(body)}' is false here, as ¬ requires.")
            else:
                setp(i, STATUS_VACUOUS, "",
                     f"{ps(i)} — only the initial state matters for this formula.")
        return statuses, highlights, reasons, KIND_SAFETY, bf

    # ── Boolean top operator: blame the responsible (often temporal) child ─────
    if tag in ("And", "Or", "Implies", "Equiv", "Xor"):
        child = _responsible_child(f, word, ops)
        if child is not None and _op_tag(child, ops) != "ap" and _op_tag(child, ops) != "unknown":
            return _classify(child, word, formula_str, ops)

    # ── Fallback: a Boolean / atomic formula only constrains the initial state ─
    # It may be either satisfied or violated by the word (the equivalence
    # explorer classifies both formulas, not just the failing one), so decide
    # from its actual truth value rather than assuming a counterexample.
    ff = _focus(f, formula_str)
    holds0 = word.holds(f, 0)
    for i in range(N):
        if i == 0:
            if holds0:
                setp(i, STATUS_SATISFIED, ff,
                     f"{ps(i)} — '{u(f)}' holds at the initial state, as required.")
            else:
                setp(i, STATUS_VIOLATING, ff,
                     f"{ps(i)} — '{u(f)}' is false at the initial state.")
        else:
            setp(i, STATUS_VACUOUS, "",
                 f"{ps(i)} — only the initial state matters for this formula.")
    return statuses, highlights, reasons, KIND_SAFETY, ff


def analyze_lasso(
    prefix: list[dict],
    cycle: list[dict],
    formula_str: str,
    graph: dict,
    spot_id_to_node: dict[int, str],
) -> dict:
    """Convert a SPOT lasso into a per-state analysis for the counterexample page.

    Returns a dict with ``steps`` (one dict per state), ``violation_kind`` and
    ``violating_subformula``.  Each state is classified individually by
    evaluating the formula over the ultimately periodic counterexample word, so
    only the states that genuinely break the formula are flagged as violating.
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
    # name may differ from id when the user renames a state in the editor.
    name_by_node: dict[str, str] = {
        n["data"]["id"]: n["data"].get("name", n["data"]["id"])
        for n in raw_nodes
    }

    order = [
        spot_id_to_node.get(step["spot_id"], str(step["spot_id"]))
        for step in prefix
    ]
    cycle_start = len(order)
    order += [
        spot_id_to_node.get(step["spot_id"], str(step["spot_id"]))
        for step in cycle
    ]
    N = len(order)
    props_by_pos = [list(props_by_node.get(nid, [])) for nid in order]

    ops = _op_constants()
    try:
        f = spot.formula(_normalize_formula(formula_str)) if _SPOT_AVAILABLE else None
    except Exception:
        f = None

    if f is None or N == 0:
        # Degraded mode (no SPOT / unparseable): fall back to the old structural
        # split so the page still renders something sensible.
        steps = []
        for i, nid in enumerate(order):
            in_cycle = i >= cycle_start
            steps.append({
                "state":       nid,
                "name":        name_by_node.get(nid, nid),
                "props":       props_by_pos[i],
                "status":      STATUS_VIOLATING if in_cycle else STATUS_SATISFIED,
                "highlight":   "",
                "reason":      "",
                "in_cycle":    in_cycle,
                "cycle_start": in_cycle and i == cycle_start,
                "cycle_back":  False,
            })
        if steps:
            steps[-1]["cycle_back"] = True
        return {
            "steps": steps,
            "violation_kind": KIND_SAFETY,
            "violating_subformula": "",
        }

    word = _LassoWord(props_by_pos, cycle_start, ops)
    statuses, highlights, reasons, kind, viol_sub = _classify(
        f, word, formula_str, ops
    )

    steps = []
    for i, nid in enumerate(order):
        in_cyc = i >= cycle_start
        steps.append({
            "state":       nid,
            "name":        name_by_node.get(nid, nid),
            "props":       props_by_pos[i],
            "status":      statuses[i],
            "highlight":   highlights[i],
            "reason":      reasons[i],
            "in_cycle":    in_cyc,
            "cycle_start": in_cyc and i == cycle_start,
            "cycle_back":  False,
        })
    if steps:
        steps[-1]["cycle_back"] = True

    return {
        "steps": steps,
        "violation_kind": kind,
        "violating_subformula": viol_sub,
    }


# ── 4. Formula equivalence explorer ──────────────────────────────────────────
#
# Compare two LTL formulas A and B and, when they differ, produce a concrete
# *distinguishing model*: an ultimately-periodic word (a lasso) that satisfies
# one formula but not the other.  A word satisfying ``A ∧ ¬B`` is a witness that
# A is not implied by B; symmetrically for ``B ∧ ¬A``.  The witness is rendered
# as a read-only Kripke lasso (same Cytoscape shape the editor consumes) so the
# UI can animate it exactly like the counterexample page.
#
# Relationship keys (nuanced, via language containment):
#   equivalent   A ≡ B                     (glyph ≡)
#   a_stronger   L(A) ⊊ L(B)  (A ⊨ B)      (glyph ⊋)  → witness: B ∧ ¬A
#   b_stronger   L(B) ⊊ L(A)  (B ⊨ A)      (glyph ⊊)  → witness: A ∧ ¬B
#   incomparable neither implies the other (glyph ≢)  → both witnesses


def _label_props(label, ap_vars: list) -> list[str]:
    """Decode one BDD transition label into a concrete set of true APs.

    We pick a single satisfying minterm by greedily restricting the label:
    an AP is taken as true only when that is still consistent with the choices
    made so far, otherwise false.  This always yields a valid assignment even
    when the label is a disjunction (e.g. ``a | b``).
    """
    props: list[str] = []
    cur = label
    false_bdd = None
    for name, var in ap_vars:
        ith = _bdd_ithvar(var)
        if false_bdd is None:
            false_bdd = ith & _bdd_nithvar(var)
        if (cur & ith) != false_bdd:
            cur = cur & ith
            props.append(name)
        else:
            cur = cur & _bdd_nithvar(var)
    return props


def _no_em(text: str) -> str:
    """Remove em / en dashes from user-facing prose (project style: none allowed).

    The per-state reasons produced by ``_classify`` are reused verbatim in the
    equivalence explorer; that helper writes them with " — " separators, so we
    rewrite those to a colon here rather than duplicating the reason strings.
    """
    if not text:
        return text
    return (
        text
        .replace(" — ", ": ")
        .replace("—", ": ")
        .replace(" – ", ": ")
        .replace("–", "-")
    )


# ── Tier 3: language-level facts about a single formula ───────────────────────
#
# These feed the "formula insight" strip so students see *why* two formulas
# relate the way they do (e.g. both are tautologies, or one is a contradiction),
# and learn the temporal-hierarchy class of what they typed.

_MP_CLASS_LABEL = {
    "safety":      "Safety",
    "guarantee":   "Guarantee",
    "obligation":  "Obligation",
    "recurrence":  "Recurrence",
    "persistence": "Persistence",
    "reactivity":  "Reactivity",
}

# One-line, em-dash-free descriptions of each Manna-Pnueli class.
_MP_CLASS_BLURB = {
    "Safety":      "nothing bad ever happens; a violation would show up at a finite point.",
    "Guarantee":   "something good happens at least once.",
    "Obligation":  "a Boolean combination of safety and guarantee requirements.",
    "Recurrence":  "something good keeps happening infinitely often.",
    "Persistence": "from some point on, a condition holds forever after.",
    "Reactivity":  "a general response pattern; the most expressive class.",
}


def _formula_facts(f) -> dict:
    """Language-level facts about one formula for the insight strip.

    Returns satisfiable / tautology / contradiction flags plus the formula's
    Manna-Pnueli temporal class (safety, recurrence, ...). Best-effort: any SPOT
    hiccup degrades to conservative defaults rather than raising.
    """
    satisfiable = True
    tautology = False
    try:
        satisfiable = not spot.translate(f).is_empty()
        tautology = spot.translate(spot.formula.Not(f)).is_empty()
    except Exception:
        pass

    try:
        primary = spot.mp_class(f, "v").split()[0]
    except Exception:
        primary = ""
    class_label = _MP_CLASS_LABEL.get(primary, "")

    return {
        "satisfiable":   satisfiable,
        "contradiction": not satisfiable,
        "tautology":     tautology,
        "class_label":   class_label,
        "class_blurb":   _MP_CLASS_BLURB.get(class_label, ""),
    }


def _formula_ast(f, ops: dict) -> dict:
    """Serialise an LTL formula as a JSON-friendly syntax tree.

    The interactive timeline renders one row per subformula and re-evaluates
    every node over the (editable) trace in the browser, so it needs the whole
    tree: an operator tag, the node's Unicode text (used as the row label) and,
    for atoms, the proposition name.
    """
    tag = _op_tag(f, ops)
    node = {"op": tag, "text": _unicode_inner(str(f))}
    if tag == "ap":
        node["name"] = f.ap_name()
    elif tag not in ("tt", "ff"):
        node["children"] = [_formula_ast(f[i], ops) for i in range(f.size())]
    return node


def _collect_aps(node: dict, out: list) -> None:
    """Depth-first collect of atomic-proposition names from a ``_formula_ast`` tree."""
    if node.get("op") == "ap" and node["name"] not in out:
        out.append(node["name"])
    for ch in node.get("children", []):
        _collect_aps(ch, out)


def _lasso_to_witness(run, aut) -> dict | None:
    """Turn a SPOT accepting run into a Kripke-lasso witness dict.

    Returns ``{"elements": [...cytoscape...], "trace": [...]}`` or None if the
    run has no cycle (should not happen for a genuine accepting run).
    """
    try:
        run = run.reduce()
    except Exception:
        pass

    prefix_steps = list(run.prefix)
    cycle_steps = list(run.cycle)
    if not cycle_steps:
        return None

    ap_vars = [(ap.ap_name(), aut.register_ap(ap)) for ap in aut.ap()]

    order = prefix_steps + cycle_steps
    cycle_start = len(prefix_steps)
    n = len(order)

    props_by_pos = [_label_props(step.label, ap_vars) for step in order]

    elements: list[dict] = []
    for i in range(n):
        elements.append({
            "data": {
                "id": f"w{i}",
                "name": f"s{i}",
                "label": f"s{i}",
                "props": props_by_pos[i],
                "initial": i == 0,
            },
            "position": {"x": 90 + i * 130, "y": 200},
        })
    # chain edges + the loop-closing back-edge (makes the graph a total lasso)
    for i in range(n - 1):
        elements.append({"data": {"id": f"we{i}", "source": f"w{i}", "target": f"w{i + 1}"}})
    elements.append({
        "data": {"id": "we_loop", "source": f"w{n - 1}", "target": f"w{cycle_start}"}
    })

    trace = []
    for i in range(n):
        trace.append({
            "state": f"w{i}",
            "name": f"s{i}",
            "props": props_by_pos[i],
            "in_cycle": i >= cycle_start,
            "cycle_start": i == cycle_start,
            "cycle_back": i == n - 1,
        })

    return {"elements": elements, "trace": trace}


def _truth_trace(witness: dict, fa, fb, ops: dict) -> "_LassoWord":
    """Attach the per-position truth of A and B along the witness word.

    Equivalence is defined by the *language* of a formula (the set of behaviours
    it accepts), so the honest way to show two formulas differ is to take one
    concrete behaviour and evaluate BOTH on it, position by position.  We reuse
    ``_LassoWord`` (standard LTL suffix satisfaction) so ``a_holds`` at position i
    means "the run from state i onward satisfies A".

    Each trace step gains ``a_holds`` / ``b_holds`` (bool), ``agree`` and
    ``diverges`` (= they disagree here).  Returns the ``_LassoWord`` for reuse.
    """
    props_by_pos = [list(s["props"]) for s in witness["trace"]]
    cycle_start = next(
        (i for i, s in enumerate(witness["trace"]) if s.get("cycle_start")), 0
    )
    word = _LassoWord(props_by_pos, cycle_start, ops)
    for i, step in enumerate(witness["trace"]):
        a = word.holds(fa, i)
        b = word.holds(fb, i)
        step["a_holds"] = a
        step["b_holds"] = b
        step["agree"] = (a == b)
        step["diverges"] = (a != b)
    return word


def _eq_reasons(witness: dict) -> None:
    """Per-step, equivalence-framed explanation (distinct from the model-checker).

    Reads the ``a_holds`` / ``b_holds`` already attached by ``_truth_trace`` and
    phrases each step in terms of A vs B agreeing or disagreeing, with position 0
    (the whole run) called out as the decisive comparison.  No em dashes.
    """
    for i, step in enumerate(witness["trace"]):
        name = step["name"]
        av = "true" if step["a_holds"] else "false"
        bv = "true" if step["b_holds"] else "false"
        if step["agree"]:
            both = "true" if step["a_holds"] else "false"
            step["reason"] = (
                f"From {name} onward, A and B agree: both are {both}."
            )
        elif i == 0:
            step["reason"] = (
                f"Reading the whole run from {name}, A is {av} but B is {bv}. "
                f"This behaviour is inside one formula's language and outside the "
                f"other's, so on its own it proves A and B are not equivalent."
            )
        else:
            step["reason"] = (
                f"From {name} onward, A is {av} but B is {bv}, so the two formulas "
                f"make different claims about this part of the run."
            )


def _dual_classify(witness: dict, fa, fb, word: "_LassoWord", ops: dict) -> None:
    """Attach per-step, per-formula mechanism status and reason for BOTH formulas.

    This is the heart of the "two parallel runs" view.  Showing the same word
    twice with a bare true/false checkmark per formula is opaque: the two lanes
    look identical and never explain *why* one formula is satisfied and the other
    is not.  Instead we reuse the model checker's ``_classify`` for each formula,
    so every state carries the same trusted, plain-English judgement the Kripke
    counterexample page uses: the requirement holds here (satisfied), is still
    open (pending), or concretely breaks here (violating).  The satisfied
    formula's lane then resolves to green, while the violated one either waits
    forever (a liveness difference) or shows a red break state (a safety one) so
    the contrast between the lanes is real and self-explaining.

    Each trace step gains ``a_status`` / ``b_status`` (satisfied / pending /
    violating / vacuous) and ``a_reason`` / ``b_reason`` (em-dash-free prose).
    """
    def lane(f):
        stat, _h, reasons, _k, _vs = _classify(f, word, _unicode_inner(str(f)), ops)
        # ``_classify`` explains a formula as a counterexample, so its VIOLATING
        # marks assume the word breaks the formula.  When the word *accepts* this
        # formula (it holds at position 0) there is no genuine break: those marks
        # are states past the point where the obligation was already discharged,
        # so present them as unconstrained rather than as a false "breaks here".
        if word.holds(f, 0):
            for i, s in enumerate(stat):
                if s == STATUS_VIOLATING:
                    stat[i] = STATUS_VACUOUS
                    reasons[i] = (
                        f"{_props_str(word.props[i])} — the obligation was already "
                        "met earlier, so this state is unconstrained."
                    )
        return stat, reasons

    a_stat, a_reasons = lane(fa)
    b_stat, b_reasons = lane(fb)
    for i, step in enumerate(witness["trace"]):
        step["a_status"] = a_stat[i]
        step["a_reason"] = _no_em(a_reasons[i])
        step["b_status"] = b_stat[i]
        step["b_reason"] = _no_em(b_reasons[i])


def _eq_divergence_caption(hold_label: str, viol_label: str, viol_unicode: str,
                           kind: str, break_names: list[str]) -> str:
    """One-line "why they differ" caption for a witness run."""
    lead = f"The run below satisfies {hold_label} but not {viol_label}"
    if kind == KIND_SAFETY and break_names:
        where = (
            break_names[0] if len(break_names) == 1
            else ", ".join(break_names)
        )
        return lead + f", which breaks at {where}."
    return lead + (
        f". Nothing breaks {viol_label} at any one state; it fails only because "
        f"the loop repeats forever without ever getting there."
    )


def _annotate_witness(witness: dict, fa, fb, viol_label: str) -> None:
    """Attach truth trace, equivalence reasons and the headline explainer."""
    ops = _op_constants()
    word = _truth_trace(witness, fa, fb, ops)
    _eq_reasons(witness)
    _dual_classify(witness, fa, fb, word, ops)

    # Classify the violated formula only to learn the *character* of the
    # difference (safety vs liveness) and, for safety, the concrete break state.
    f_violate = fa if viol_label == "A" else fb
    viol_unicode = _unicode_inner(str(f_violate))
    statuses, _h, _r, kind, _vs = _classify(f_violate, word, viol_unicode, ops)
    break_names = [
        witness["trace"][i]["name"]
        for i, s in enumerate(statuses) if s == STATUS_VIOLATING
    ]
    hold_label = "B" if viol_label == "A" else "A"
    witness["violation_kind"] = kind
    witness["divergence"] = _eq_divergence_caption(
        hold_label, viol_label, viol_unicode, kind, break_names
    )


def _witness(fa, fb, viol_label: str) -> dict | None:
    """Build a witness word that satisfies one formula but violates the other.

    ``viol_label`` ("A" or "B") says which formula the witness must violate; the
    other is the one it satisfies.  The returned witness carries the per-position
    A/B truth trace and the equivalence explainer.
    """
    f_violate = fa if viol_label == "A" else fb
    f_hold = fb if viol_label == "A" else fa
    conj = spot.formula.And([f_hold, spot.formula.Not(f_violate)])
    try:
        aut = spot.translate(conj)
    except Exception:
        return None
    run = aut.accepting_run()
    if run is None:
        return None
    w = _lasso_to_witness(run, aut)
    if w is None:
        return None
    _annotate_witness(w, fa, fb, viol_label)
    return w


def _shared_example(fa, fb) -> dict | None:
    """One behaviour that satisfies A (used to illustrate the equivalent case).

    Returns None when A is unsatisfiable (a contradiction has no models).  The
    trace still carries the A/B truth rows; because the formulas are equivalent
    they agree at every position, which is the visual point: the two rows match.
    """
    try:
        aut = spot.translate(fa)
    except Exception:
        return None
    run = aut.accepting_run()
    if run is None:
        return None
    w = _lasso_to_witness(run, aut)
    if w is None:
        return None
    ops = _op_constants()
    word = _truth_trace(w, fa, fb, ops)
    _eq_reasons(w)
    _dual_classify(w, fa, fb, word, ops)
    return w


@lru_cache(maxsize=1024)
def _relationship_cached(a_str: str, b_str: str) -> dict:
    """Classify A vs B and build witnesses.  Keyed on canonical formula strings.

    Pure and deterministic → memoised.  A classroom converges on a handful of
    pairs, so repeat calls are O(1) with zero SPOT work.  Callers must treat the
    returned dict as read-only (it is shared across cache hits).
    """
    fa = spot.formula(a_str)
    fb = spot.formula(b_str)

    facts_a = _formula_facts(fa)
    facts_b = _formula_facts(fb)

    # Syntax trees + the union of propositions feed the interactive timeline,
    # which evaluates each subformula of A and B over a trace the student edits.
    ast_a = _formula_ast(fa, _op_constants())
    ast_b = _formula_ast(fb, _op_constants())
    aps: list[str] = []
    _collect_aps(ast_a, aps)
    _collect_aps(ast_b, aps)
    ast_bundle = {"ast_a": ast_a, "ast_b": ast_b, "aps": aps}

    if spot.are_equivalent(fa, fb):
        return {
            "relationship": "equivalent",
            "glyph": "≡",
            "facts_a": facts_a,
            "facts_b": facts_b,
            "witnesses": [],
            "shared_example": _shared_example(fa, fb),
            "note": _equivalent_note(facts_a, facts_b),
            **ast_bundle,
        }

    # spot.contains(X, Y) means L(Y) ⊆ L(X)  (see tests/checker/test_adversarial.py)
    a_in_b = spot.contains(fb, fa)   # L(A) ⊆ L(B)  → A implies B (A stronger)
    b_in_a = spot.contains(fa, fb)   # L(B) ⊆ L(A)  → B implies A (B stronger)

    witnesses: list[dict] = []
    if a_in_b and not b_in_a:
        relationship, glyph = "a_stronger", "⊋"
        w = _witness(fa, fb, "A")    # violates A (so satisfies B)
        if w:
            witnesses.append({"satisfies": "B", "violates": "A", **w})
    elif b_in_a and not a_in_b:
        relationship, glyph = "b_stronger", "⊊"
        w = _witness(fa, fb, "B")    # violates B (so satisfies A)
        if w:
            witnesses.append({"satisfies": "A", "violates": "B", **w})
    else:
        relationship, glyph = "incomparable", "≢"
        wa = _witness(fa, fb, "B")   # violates B (so satisfies A)
        if wa:
            witnesses.append({"satisfies": "A", "violates": "B", **wa})
        wb = _witness(fa, fb, "A")   # violates A (so satisfies B)
        if wb:
            witnesses.append({"satisfies": "B", "violates": "A", **wb})

    return {
        "relationship": relationship,
        "glyph": glyph,
        "facts_a": facts_a,
        "facts_b": facts_b,
        "witnesses": witnesses,
        "shared_example": None,
        "note": "",
        **ast_bundle,
    }


def _equivalent_note(facts_a: dict, facts_b: dict) -> str:
    """Explain *why* two equivalent formulas coincide, when it is instructive."""
    if facts_a["tautology"] and facts_b["tautology"]:
        return ("Both formulas are tautologies: they hold on every possible "
                "behaviour, so they are trivially equivalent.")
    if facts_a["contradiction"] and facts_b["contradiction"]:
        return ("Both formulas are contradictions: no behaviour satisfies either "
                "one, so they are trivially equivalent.")
    return ("SPOT verified that both formulas accept exactly the same set of "
            "behaviours. Here is one behaviour they both accept:")


def formula_relationship(formula_a: str, formula_b: str) -> dict:
    """Compare two LTL formulas; return their relationship and distinguishing models.

    Returns a dict:
      relationship   : "equivalent" | "a_stronger" | "b_stronger" | "incomparable"
      glyph          : ≡ / ⊋ / ⊊ / ≢
      facts_a/facts_b: language-level facts per formula (satisfiable, tautology,
                       contradiction, temporal class) for the insight strip
      witnesses      : list of {satisfies, violates, elements, trace,
                       violation_kind, divergence}.  Each trace step carries
                       status / reason / diverges marking the divergence point.
                       Empty when equivalent.
      shared_example : {elements, trace} both formulas accept (equivalent case
                       only; None when they are contradictions or not equivalent)
      note           : plain-English note for the equivalent case ("" otherwise)

    Raises ValueError (user-facing) when either formula fails to parse, is not
    pure LTL, exceeds the structural caps, or the two together reference more
    than MAX_FORMULA_APS distinct propositions.
    """
    _require_spot()

    fa = validate_formula(formula_a)
    fb = validate_formula(formula_b)

    # Combined-AP cap: the witness translations below build A ∧ ¬B, whose AP set
    # is the union — keep it bounded so translate() stays fast.
    ops = _op_constants()
    combined_aps = _collect_formula_aps_set(fa, ops) | _collect_formula_aps_set(fb, ops)
    if len(combined_aps) > MAX_FORMULA_APS:
        raise ValueError(
            f"Together the two formulas reference {len(combined_aps)} distinct "
            f"propositions — the explorer supports at most {MAX_FORMULA_APS}."
        )

    # Canonical strings dedupe syntactic variants (spacing, parens) in the cache.
    return _relationship_cached(str(fa), str(fb))
