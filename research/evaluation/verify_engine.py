"""Engine checks behind claims in the evaluation write-up. Run from the repository root:

    python research/evaluation/verify_engine.py

1. The tool's own lasso evaluator agrees with Spot's word-membership test on random formulas.
2. The answer keys of the path-reading test items (A6, B6) and the formula facts the
   distractors rely on hold according to Spot.
3. The evaluation exercises' answer keys: the judge verdicts for E5 and E6 match Huth and Ryan's,
   the sample paths for E3 and E4 are valid and satisfy their formulas, and every target formula
   for E1 and E2 is accepted by the tool's formula checks.
"""

import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")

import spot  # noqa: E402

from apps.checker.equivalence import validate_formula_submission  # noqa: E402
from apps.checker.tasks import run_ltl_check, run_trace_check  # noqa: E402
from apps.checker.traces import evaluate_lasso  # noqa: E402

APS = ["p", "q", "r"]
CASES = 3000


def cube(labels, aps):
    return "&".join(a if a in labels else f"!{a}" for a in aps)


def lasso_word(states, prefix, cycle, aps):
    head = ";".join(cube(states[s], aps) for s in prefix)
    loop = ";".join(cube(states[s], aps) for s in cycle)
    return (head + ";" if head else "") + "cycle{" + loop + "}"


def holds_on_lasso(formula, states, prefix, cycle, aps):
    word = spot.parse_word(lasso_word(states, prefix, cycle, aps))
    return spot.translate(formula).intersects(word)


def cross_check_evaluator():
    rng = random.Random(20261016)
    formulas = spot.randltl(
        APS, seed=7, tree_size=(3, 14), output="ltl",
        ops_ltl="ap=1,false=0,true=0,not=1,and=1,or=1,implies=1,equiv=0,xor=0,"
                "X=1,F=1,G=1,U=1,R=0,W=0,M=0",
    )
    disagreements = 0
    for n, f in enumerate(formulas):
        if n == CASES:
            break
        ids = [f"s{i}" for i in range(rng.randint(1, 4))]
        states = {s: [a for a in APS if rng.random() < 0.5] for s in ids}
        split = rng.randint(0, len(ids) - 1)
        prefix, cycle = ids[:split], ids[split:]
        nodes = [{"data": {"id": s, "props": states[s], "initial": i == 0}}
                 for i, s in enumerate(ids)]
        mine = evaluate_lasso({"elements": {"nodes": nodes, "edges": []}}, f.to_str(), prefix, cycle)
        disagreements += mine != holds_on_lasso(f, states, prefix, cycle, APS)
    print(f"lasso evaluator vs Spot: {CASES} cases, {disagreements} disagreements")
    return disagreements == 0


def check_test_items():
    ok = True

    def expect(name, cond):
        nonlocal ok
        ok = ok and cond
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")

    vending = {"pay": ["pay"], "select": ["select"], "soda": ["soda"]}
    vend_aps = ["pay", "select", "soda", "beer"]
    for formula, violated in (("G F beer", True), ("F soda", False),
                              ("G F soda", False), ("G !(soda & beer)", False)):
        holds = holds_on_lasso(spot.formula(formula), vending, ["pay"],
                               ["select", "soda", "pay"], vend_aps)
        expect(f"A6 {formula} {'violated' if violated else 'holds'}", holds is not violated)

    mutex = {"s0": ["n1", "n2"], "s1": ["t1", "n2"], "s3": ["t1", "t2"], "s7": ["t1", "c2"]}
    mutex_aps = ["n1", "t1", "c1", "n2", "t2", "c2"]
    for formula, violated in (("G (t1 -> F c1)", True), ("G !(c1 & c2)", False),
                              ("F t1", False), ("G F t1", False)):
        holds = holds_on_lasso(spot.formula(formula), mutex, ["s0"], ["s1", "s3", "s7"], mutex_aps)
        expect(f"B6 {formula} {'violated' if violated else 'holds'}", holds is not violated)

    f = spot.formula
    expect("A3/B3 G F p is not equivalent to F G p", not spot.are_equivalent(f("G F p"), f("F G p")))
    expect("A4/B4 p U q entails F q", spot.contains(f("F q"), f("p U q")))
    expect("A4/B4 G p does not entail p U q", not spot.contains(f("p U q"), f("G p")))
    return ok


def structure(states, edges, initial):
    nodes = [{"data": {"id": s, "props": props, "initial": s == initial}}
             for s, props in states.items()]
    links = [{"data": {"id": f"e{i}", "source": a, "target": b}} for i, (a, b) in enumerate(edges)]
    return {"elements": {"nodes": nodes, "edges": links}}


# The evaluation module's structures, as seeded: Baier and Katoen Ex. 2.2, and Huth and Ryan
# Figs 3.7 (first-attempt protocol) and 3.8 (repaired protocol).
VENDING = structure(
    {"pay": ["pay"], "select": ["select"], "soda": ["soda"], "beer": ["beer"]},
    [("pay", "select"), ("select", "soda"), ("select", "beer"), ("soda", "pay"), ("beer", "pay")],
    "pay",
)
M1 = structure(
    {"s0": ["n1", "n2"], "s1": ["t1", "n2"], "s5": ["n1", "t2"], "s2": ["c1", "n2"],
     "s3": ["t1", "t2"], "s6": ["n1", "c2"], "s4": ["c1", "t2"], "s7": ["t1", "c2"]},
    [("s0", "s1"), ("s0", "s5"), ("s1", "s2"), ("s1", "s3"), ("s5", "s3"), ("s5", "s6"),
     ("s2", "s0"), ("s6", "s0"), ("s3", "s4"), ("s3", "s7"), ("s4", "s5"), ("s7", "s1")],
    "s0",
)
M2 = structure(
    {"s0": ["n1", "n2"], "s1": ["t1", "n2"], "s5": ["n1", "t2"], "s2": ["c1", "n2"],
     "s3": ["t1", "t2"], "s9": ["t1", "t2"], "s6": ["n1", "c2"], "s4": ["c1", "t2"],
     "s7": ["t1", "c2"]},
    [("s0", "s1"), ("s0", "s5"), ("s1", "s2"), ("s1", "s3"), ("s5", "s9"), ("s5", "s6"),
     ("s2", "s0"), ("s6", "s0"), ("s3", "s4"), ("s9", "s7"), ("s4", "s5"), ("s7", "s1")],
    "s0",
)
MUTEX_APS = ["n1", "t1", "c1", "n2", "t2", "c2"]
JUDGE_PARTS = ["G !(c1 & c2)", "G (t1 -> F c1)", "G (t2 -> F c2)"]


def check_exercises():
    ok = True

    def expect(name, cond):
        nonlocal ok
        ok = ok and cond
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")

    for target in ("G (!p)", "F p", "G (p -> F q)"):
        validate_formula_submission(target, ["p", "q"])
        expect(f"E1 target accepted: {target}", True)
    for target in ("G !(c1 & c2)", "G (t1 -> F c1)", "G F c1"):
        validate_formula_submission(target, MUTEX_APS)
        expect(f"E2 target accepted: {target}", True)

    for name, graph, formula, prefix, cycle in (
        ("E3.1", VENDING, "F soda", ["pay"], ["select", "soda", "pay"]),
        ("E3.2", VENDING, "G F beer", [], ["pay", "select", "beer"]),
        ("E3.3", VENDING, "!soda U beer", [], ["pay", "select", "beer"]),
        ("E4.1", M1, "F (t1 & G !c1)", ["s0"], ["s1", "s3", "s7"]),
        ("E4.2", M1, "F (c1 & X ((!c1) U c1))", ["s0"], ["s5", "s3", "s4"]),
    ):
        r = run_trace_check(graph, formula, prefix, cycle)
        expect(f"{name} sample path valid and satisfies {formula}", bool(r["path_ok"] and r["holds"]))

    # Verdicts from Huth and Ryan p.189 (first attempt) and Remark 3.11 (repaired protocol).
    for name, graph, verdicts in (("E5", M1, [True, False, False]), ("E6", M2, [True, True, True])):
        for formula, expected in zip(JUDGE_PARTS, verdicts):
            holds = run_ltl_check(graph, formula)["result"] == "satisfied"
            expect(f"{name} {formula} {'holds' if expected else 'is violated'}", holds == expected)
    return ok


if __name__ == "__main__":
    results = [cross_check_evaluator(), check_test_items(), check_exercises()]
    sys.exit(0 if all(results) else 1)
