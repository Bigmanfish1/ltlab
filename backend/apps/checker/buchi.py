"""Grade Büchi-automaton exercises (MCL5 p.13-19).

Two student tasks share this engine. buchi_construct grades a drawn automaton
against a target LTL formula by language equivalence (spot.are_equivalent vs
spot.translate(target, 'BA', 'sbacc')). buchi_word grades a typed lasso word
against a fixed automaton by membership (spot.contains). The automaton is
Cytoscape JSON: nodes carry `initial`/`accepting`, edges carry a boolean-
expression `label` over the declared alphabet.

Error contract mirrors traces.py: ValueError is a teacher/system fault (a
malformed label, an over-cap or ill-formed automaton, an unparseable target).
A wrong student automaton is a False return; a rejected word is a user-facing
message — neither is an exception.
"""

from .engine import _normalize_formula, _require_spot

try:
    import spot  # type: ignore[import]
except ImportError:  # pragma: no cover - mirrors engine's optional import
    spot = None

MAX_BUCHI_STATES = 60
MAX_BUCHI_EDGES = 120


def _real_elements(automaton_json):
    elements = automaton_json.get("elements", {})
    nodes = [
        n for n in elements.get("nodes", [])
        if not n.get("data", {}).get("phantom")
    ]
    edges = [
        e for e in elements.get("edges", [])
        if not e.get("data", {}).get("phantom")
    ]
    return nodes, edges


def _parse_label(label, declared_set):
    """A boolean edge-label string → validated spot.formula. ValueError on bad."""
    text = (label or "").strip()
    if not text:
        raise ValueError(
            "A transition has no label — label every edge with a boolean "
            "expression over the alphabet (or 'true')."
        )
    try:
        f = spot.formula(_normalize_formula(text))
    except (SyntaxError, RuntimeError) as exc:
        raise ValueError(f"Invalid transition label '{label}': {exc}") from exc
    if not f.is_boolean():
        raise ValueError(
            f"Transition label '{label}' is not a boolean expression — "
            "temporal operators are not allowed on edges."
        )
    undeclared = {ap.ap_name() for ap in spot.atomic_prop_collect(f)} - declared_set
    if undeclared:
        names = ", ".join(sorted(undeclared))
        raise ValueError(
            f"Transition label '{label}' uses proposition(s) not in the "
            f"alphabet: {names}."
        )
    return f


def build_buchi(automaton_json, declared_aps):
    """Compile the automaton JSON into a state-based Büchi twa_graph.

    Multiple initial states are allowed (Büchi I⊆Q): SPOT's set_init_state
    takes only one, so a fresh initial state is synthesized whose out-edges copy
    every declared-initial state's out-edges. It is visited once, so it does not
    change which states are seen infinitely often — the acceptance is preserved.
    """
    _require_spot()
    if not isinstance(automaton_json, dict):
        raise ValueError("The automaton could not be read.")

    nodes, edges = _real_elements(automaton_json)
    if not nodes:
        raise ValueError("Draw at least one state.")
    if len(nodes) > MAX_BUCHI_STATES:
        raise ValueError(
            f"Automaton has too many states ({len(nodes)}) — "
            f"at most {MAX_BUCHI_STATES} are supported."
        )
    if len(edges) > MAX_BUCHI_EDGES:
        raise ValueError(
            f"Automaton has too many transitions ({len(edges)}) — "
            f"at most {MAX_BUCHI_EDGES} are supported."
        )

    declared_set = set(declared_aps or [])
    g = spot.make_twa_graph()
    g.set_buchi()
    for ap in declared_aps or []:
        g.register_ap(ap)

    index = {n["data"]["id"]: g.new_state() for n in nodes}

    initial = [n for n in nodes if n["data"].get("initial")]
    if not initial:
        raise ValueError("Mark at least one initial state.")
    accepting_ids = {n["data"]["id"] for n in nodes if n["data"].get("accepting")}

    for e in edges:
        d = e["data"]
        src, tgt = d.get("source"), d.get("target")
        if src not in index or tgt not in index:
            raise ValueError("A transition refers to a missing state.")
        cond = spot.formula_to_bdd(_parse_label(d.get("label"), declared_set), g.get_dict(), g)
        # state-based acceptance: an accepting state's out-edges carry mark 0
        acc = [0] if src in accepting_ids else []
        g.new_edge(index[src], index[tgt], cond, acc)

    if len(initial) == 1:
        g.set_init_state(index[initial[0]["data"]["id"]])
    else:
        # snapshot before adding, so appending synth edges cannot invalidate iteration
        copies = [
            (e.dst, e.cond, e.acc)
            for init_node in initial
            for e in g.out(index[init_node["data"]["id"]])
        ]
        synth = g.new_state()
        for dst, cond, acc in copies:
            g.new_edge(synth, dst, cond, acc)
        g.set_init_state(synth)

    return g


def _translate_target(target):
    try:
        return spot.translate(_normalize_formula(target), "BA", "sbacc")
    except (SyntaxError, RuntimeError) as exc:
        raise ValueError(f"Invalid target formula: {exc}") from exc


def check_buchi_equivalence(automaton_json, target, declared_aps):
    """True iff the drawn automaton has the same language as the target LTL."""
    _require_spot()
    student = build_buchi(automaton_json, declared_aps)
    return spot.are_equivalent(student, _translate_target(target))


def is_deterministic(automaton_json, declared_aps):
    """True iff the drawn automaton is deterministic (spot.is_deterministic)."""
    _require_spot()
    return spot.is_deterministic(build_buchi(automaton_json, declared_aps))


def word_accepted(automaton_json, word_str, declared_aps):
    """Grade a typed lasso word against the automaton → (accepted, message).

    The automaton is teacher data (build_buchi raises on a bad one). The word is
    student data: a parse failure, missing cycle, or off-alphabet letter comes
    back as (False, message), never an exception.
    """
    _require_spot()
    aut = build_buchi(automaton_json, declared_aps)
    text = (word_str or "").strip()
    if not text:
        return False, "Enter a word — for example  a; cycle{a}."
    if "cycle{" not in text:
        return False, "A Büchi word needs a repeating part: write it as prefix; cycle{...}."
    try:
        word = spot.parse_word(_normalize_formula(text), aut.get_dict())
    except (SyntaxError, RuntimeError) as exc:
        return False, f"Could not read the word: {exc}"
    wa = word.as_automaton()
    undeclared = {ap.ap_name() for ap in wa.ap()} - set(declared_aps or [])
    if undeclared:
        names = ", ".join(sorted(undeclared))
        return False, f"The word uses proposition(s) not in the alphabet: {names}."
    return spot.contains(aut, wa), ""
