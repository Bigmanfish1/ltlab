"""Engine-level structural complexity caps and AP-subset validation.

These tests require SPOT (spottl) to be installed; they are automatically
skipped when SPOT is not available (e.g. on the host dev machine).
"""

import unittest

from django.test import TestCase

try:
    import spot  # type: ignore[import]
    SPOT_AVAILABLE = True
except ImportError:
    SPOT_AVAILABLE = False

from apps.checker.engine import (
    MAX_FORMULA_APS,
    MAX_FORMULA_NODES,
    MAX_TEMPORAL_OPS,
    validate_request,
)


def _graph(props_by_id: dict[str, list[str]], initial_id: str) -> dict:
    """Build a minimal Cytoscape graph dict for testing."""
    nodes = []
    for nid, props in props_by_id.items():
        nodes.append({
            "data": {
                "id": nid,
                "name": nid,
                "initial": nid == initial_id,
                "props": props,
            }
        })
    return {"elements": {"nodes": nodes, "edges": []}}


@unittest.skipUnless(SPOT_AVAILABLE, "SPOT not installed — skipping engine tests")
class EngineFormulaParseTests(TestCase):

    def test_valid_formula_passes(self):
        g = _graph({"s0": ["p"]}, "s0")
        validate_request(g, "G p")   # should not raise

    def test_invalid_formula_syntax_raises(self):
        g = _graph({"s0": []}, "s0")
        with self.assertRaises(ValueError) as ctx:
            validate_request(g, "G (p &&& q)")
        self.assertIn("Invalid", str(ctx.exception))

    def test_unicode_operators_normalised(self):
        # Unicode → and ∧ should be accepted (normalised before parsing).
        g = _graph({"s0": ["p", "q"]}, "s0")
        validate_request(g, "G (p ∧ q)")  # should not raise


@unittest.skipUnless(SPOT_AVAILABLE, "SPOT not installed — skipping engine tests")
class EngineStructuralCapTests(TestCase):

    def _too_many_aps_formula(self) -> tuple[str, dict]:
        """Build a formula + graph with MAX_FORMULA_APS+1 distinct APs."""
        aps = [f"p{i}" for i in range(MAX_FORMULA_APS + 1)]
        formula = " & ".join(aps)
        g = _graph({"s0": aps}, "s0")
        return formula, g

    def _too_many_temporal_formula(self) -> tuple[str, dict]:
        """Build a formula with MAX_TEMPORAL_OPS+2 temporal operators.

        Uses G followed by (MAX_TEMPORAL_OPS+1) nested X operators.  SPOT has
        no law to collapse X^n into fewer operators (X^n p means "p in exactly
        n steps"), so the count survives normalisation.  The formula uses only
        1 AP so the AP-count check does not trigger first.
        """
        xs = " ".join(["X"] * (MAX_TEMPORAL_OPS + 1))
        formula = f"G {xs} p"
        g = _graph({"s0": ["p"]}, "s0")
        return formula, g

    def test_too_many_aps_raises(self):
        formula, g = self._too_many_aps_formula()
        with self.assertRaises(ValueError) as ctx:
            validate_request(g, formula)
        msg = str(ctx.exception)
        self.assertIn("proposition", msg.lower())
        self.assertIn(str(MAX_FORMULA_APS), msg)

    def test_max_aps_exactly_passes(self):
        aps = [f"p{i}" for i in range(MAX_FORMULA_APS)]
        formula = " & ".join(aps)
        g = _graph({"s0": aps}, "s0")
        validate_request(g, formula)  # should not raise

    def test_too_many_temporal_ops_raises(self):
        formula, g = self._too_many_temporal_formula()
        with self.assertRaises(ValueError) as ctx:
            validate_request(g, formula)
        msg = str(ctx.exception)
        self.assertTrue(
            "temporal" in msg.lower() or "complex" in msg.lower(),
            f"Expected 'temporal' or 'complex' in error: {msg!r}"
        )


@unittest.skipUnless(SPOT_AVAILABLE, "SPOT not installed — skipping engine tests")
class EngineAPSubsetTests(TestCase):

    def test_undeclared_ap_raises(self):
        """Formula references 'z' but no state declares it."""
        g = _graph({"s0": ["p"]}, "s0")
        with self.assertRaises(ValueError) as ctx:
            validate_request(g, "G z")
        self.assertIn("z", str(ctx.exception))

    def test_declared_subset_passes(self):
        g = _graph({"s0": ["p", "q"]}, "s0")
        validate_request(g, "G (p & q)")  # should not raise

    def test_partial_use_of_declared_aps_passes(self):
        # Formula uses only some of the declared props — fine.
        g = _graph({"s0": ["p", "q", "r"]}, "s0")
        validate_request(g, "G p")  # should not raise

    def test_multiple_undeclared_aps_listed_in_error(self):
        g = _graph({"s0": ["p"]}, "s0")
        with self.assertRaises(ValueError) as ctx:
            validate_request(g, "G (x & y)")
        msg = str(ctx.exception)
        self.assertIn("x", msg)
        self.assertIn("y", msg)

    def test_ap_on_different_state_counts_as_declared(self):
        # 'q' is only on s1 — formula G q should still pass.
        g = _graph({"s0": ["p"], "s1": ["q"]}, "s0")
        validate_request(g, "G q")  # should not raise
