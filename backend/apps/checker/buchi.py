"""Grade Büchi-automaton exercises (MCL5 p.13-19).

The alphabet Σ is a set of atomic, mutually-exclusive SYMBOLS (MCL5 p.17-19):
exactly one symbol occurs at each word position. A transition is labelled with a
symbol (`a`) or a comma-set of symbols (`a,b` = "on a or on b"), never a boolean
proposition guard. Each symbol is compiled to a one-hot conjunction over Σ — for
Σ={a,b,c}, `a` becomes `a & !b & !c` — so SPOT's propositional automata operate on
exactly one symbol per step and language equivalence over 2^AP coincides with the
symbol-alphabet language (both automata reject any non-one-hot letter).

Two student tasks share this engine. buchi_construct grades a drawn automaton
against a target LTL formula by equivalence to `translate(target) ∩ one-hot`.
buchi_word grades a typed lasso word (one symbol per step) by membership.

Error contract: ValueError signals a structural problem with an automaton — a
malformed label, an over-cap or ill-formed automaton, an unparseable target.
It never means a silently-wrong grade; the caller surfaces its message (for a
student drawing the client validation missed, submit_buchi shows it rather than
500-ing). A well-formed but wrong student automaton is a False return, and a
rejected word is a user-facing message.
"""

import re

from .engine import _normalize_formula, _require_spot

try:
    import spot  # type: ignore[import]
    import buddy  # type: ignore[import]
except ImportError:  # pragma: no cover - mirrors engine's optional import
    spot = None
    buddy = None

MAX_BUCHI_STATES = 60
MAX_BUCHI_EDGES = 120

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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


def _onehot_terms(symbols):
    """One-hot boolean term per symbol: `a` → `(a & !b & !c)` over Σ={a,b,c}."""
    terms = {}
    for s in symbols:
        conj = [s] + [f"!{o}" for o in symbols if o != s]
        terms[s] = "(" + " & ".join(conj) + ")"
    return terms


def _symbol_guard(label, symbols, g):
    """A symbol / comma-set edge label → its one-hot BDD guard. ValueError on bad."""
    tokens = [t.strip() for t in (label or "").split(",") if t.strip()]
    if not tokens:
        raise ValueError(
            "A transition has no label — label it with a symbol (e.g. a) "
            "or a set (e.g. a,b)."
        )
    guard = buddy.bddfalse
    for t in tokens:
        if t not in symbols:
            raise ValueError(
                f"Transition label '{label}' uses '{t}', which is not in the "
                f"alphabet {{{', '.join(symbols)}}}."
            )
        term = buddy.bddtrue
        for s in symbols:
            v = buddy.bdd_ithvar(g.register_ap(s))
            term = term & (v if s == t else -v)
        guard = guard | term
    return guard


def build_buchi(automaton_json, symbols):
    """Compile the automaton JSON into a state-based Büchi twa_graph.

    Edge labels are symbol / comma-set strings over Σ (`symbols`), compiled to
    one-hot guards. Multiple initial states are allowed (Büchi I⊆Q): SPOT's
    set_init_state takes only one, so a fresh initial state is synthesized whose
    out-edges copy every declared-initial state's out-edges. It is visited once,
    so it does not change which states are seen infinitely often.
    """
    _require_spot()
    if not isinstance(automaton_json, dict):
        raise ValueError("The automaton could not be read.")
    symbols = list(symbols or [])

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

    g = spot.make_twa_graph()
    g.set_buchi()
    for s in symbols:
        g.register_ap(s)

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
        guard = _symbol_guard(d.get("label"), symbols, g)
        # state-based acceptance: an accepting state's out-edges carry mark 0
        acc = [0] if src in accepting_ids else []
        g.new_edge(index[src], index[tgt], guard, acc)

    if len(initial) == 1:
        g.set_init_state(index[initial[0]["data"]["id"]])
    else:
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


def _onehot_automaton(symbols):
    """Automaton accepting exactly the words with one symbol of Σ at every step."""
    terms = _onehot_terms(symbols)
    formula = "G(" + " | ".join(terms[s] for s in symbols) + ")"
    return spot.translate(formula, "BA", "sbacc")


def _translate_target(target):
    try:
        return spot.translate(_normalize_formula(target), "BA", "sbacc")
    except (SyntaxError, RuntimeError) as exc:
        raise ValueError(f"Invalid target formula: {exc}") from exc


def _reference(target, symbols):
    """The target language restricted to the one-symbol-per-step alphabet."""
    return spot.product(_translate_target(target), _onehot_automaton(symbols))


def target_automaton_states(target, symbols):
    """State count of the (one-hot-restricted) target automaton — teacher feedback."""
    _require_spot()
    return _reference(target, symbols).num_states()


def check_buchi_equivalence(automaton_json, target, symbols):
    """True iff the drawn automaton's language equals the target over Σ."""
    _require_spot()
    student = build_buchi(automaton_json, symbols)
    return spot.are_equivalent(student, _reference(target, symbols))


def is_deterministic(automaton_json, symbols):
    """True iff the drawn automaton is deterministic.

    A deterministic Büchi automaton has a single initial state (slide 14: I⊆Q;
    determinism requires |I|=1). build_buchi collapses several initials into one
    synthesized state, which would hide multi-initiality from spot, so the
    initial count is checked here before the transition-determinism test.
    """
    _require_spot()
    nodes, _ = _real_elements(automaton_json)
    if sum(1 for n in nodes if n["data"].get("initial")) > 1:
        return False
    return spot.is_deterministic(build_buchi(automaton_json, symbols))


def _symbolic_word_to_props(word_str, symbols):
    """Rewrite a symbol lasso word to a propositional one for spot.parse_word.

    `a; b; cycle{a}` over Σ={a,b} → `(a & !b); (b & !a); cycle{(a & !b)}`.
    Returns (prop_word, None) or (None, user_message). Each step must be exactly
    one symbol: boolean operators (& | !) and parentheses are rejected so a
    student cannot smuggle a compound letter like `a & b` or `a | b` past the
    one-symbol-per-step contract.
    """
    if re.search(r"[&|!()]", word_str):
        return None, "Each step of the word is exactly one symbol — no & | ! operators."
    terms = _onehot_terms(symbols)
    problem = {}

    def repl(m):
        tok = m.group(0)
        if tok == "cycle":
            return tok
        if tok not in symbols:
            problem["msg"] = (
                f"'{tok}' is not in the alphabet {{{', '.join(symbols)}}} — "
                "a word uses one symbol per step."
            )
            return tok
        return terms[tok]

    rewritten = _IDENT_RE.sub(repl, word_str)
    if problem:
        return None, problem["msg"]
    return rewritten, None


def word_accepted(automaton_json, word_str, symbols):
    """Grade a typed lasso word against the automaton → (accepted, message).

    The word is one symbol per step, e.g. `a; b; cycle{a}`. The automaton is
    teacher data (build_buchi raises on a bad one); the word is student data — a
    parse failure, missing cycle, or off-alphabet symbol comes back as
    (False, message), never an exception.
    """
    _require_spot()
    symbols = list(symbols or [])
    aut = build_buchi(automaton_json, symbols)
    text = (word_str or "").strip()
    if not text:
        return False, "Enter a word — for example  a; cycle{b}."
    if "cycle{" not in text:
        return False, "A Büchi word needs a repeating part: write it as prefix; cycle{...}."
    prop_word, err = _symbolic_word_to_props(text, symbols)
    if err:
        return False, err
    try:
        word = spot.parse_word(prop_word, aut.get_dict())
    except (SyntaxError, RuntimeError) as exc:
        return False, f"Could not read the word: {exc}"
    return spot.contains(aut, word.as_automaton()), ""
