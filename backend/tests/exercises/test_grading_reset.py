import json

from django.test import override_settings

from apps.exercises import services, views
from apps.exercises.models import Attempt, Exercise

from tests.exercises.test_views import PLAIN_STATIC, TeacherViewTestCase

# section C graph: only path from s0 is s0 s1 s1 s1 ... (word {a}{b}{b}...), so
# universally "G a" fails at position 1 (a only at s0) and "F b" holds (b from s1)
JUDGE_GRAPH = {
    "elements": {
        "nodes": [
            {"data": {"id": "s0", "name": "s0", "props": ["a"], "initial": True}},
            {"data": {"id": "s1", "name": "s1", "props": ["b"]}},
        ],
        "edges": [
            {"data": {"id": "e0", "source": "s0", "target": "s1"}},
            {"data": {"id": "e1", "source": "s1", "target": "s1"}},
        ],
    }
}

# same shape, different props on s1 — used to change the grading signature
JUDGE_GRAPH_ALT = {
    "elements": {
        "nodes": [
            {"data": {"id": "s0", "name": "s0", "props": ["a"], "initial": True}},
            {"data": {"id": "s1", "name": "s1", "props": ["c"]}},
        ],
        "edges": [
            {"data": {"id": "e0", "source": "s0", "target": "s1"}},
            {"data": {"id": "e1", "source": "s1", "target": "s1"}},
        ],
    }
}


# ---------------------------------------------------------------------------
# A. English target must use only allowed operators
# ---------------------------------------------------------------------------
FULL_OPS = '["G", "F", "X", "U", "¬", "∧", "∨", "→"]'


@override_settings(STORAGES=PLAIN_STATIC)
class EnglishOperatorRestrictionTests(TeacherViewTestCase):
    def _english_form(self, **overrides):
        data = self._form(
            exercise_type="english_to_formula",
            graph_data="",
            declared_aps='["grant"]',
            parts=json.dumps([{"prompt": "eventually grant", "formula": "F grant"}]),
            allowed_operators='["G"]',
        )
        data.update(overrides)
        return data

    def test_target_operator_outside_allowed_blocks_publish(self):
        data = self._english_form(action="publish")
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exercise.objects.filter(title="New Ex").exists())

    def test_target_operator_within_allowed_publishes(self):
        data = self._english_form(action="publish", allowed_operators=FULL_OPS)
        response = views.exercise_builder(self._req("post", self.teacher, data))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Exercise.objects.get(title="New Ex").is_published)

    def test_validate_form_reports_operator(self):
        form = {
            "title": "New Ex",
            "description": "desc",
            "difficulty": "intermediate",
            "module_id": self.topic.id,
            "exercise_type": "english_to_formula",
            "hints": ["", "", ""],
            "allowed_operators": ["G"],
            "declared_aps": ["grant"],
            "graph_data": "",
            "parts": [{"id": "", "prompt": "eventually grant", "formula": "F grant", "hints": []}],
        }
        errors, _warnings = services.validate_exercise_form(form, None, publishing=True)
        joined = " ".join(errors)
        self.assertIn("F", joined)
        self.assertIn("operator", joined.lower())


# ---------------------------------------------------------------------------
# B. Grading-edit resets attempts
# ---------------------------------------------------------------------------
class GradingResetTests(TeacherViewTestCase):
    def _english_form(self, part_formula="F grant", part_id="", **overrides):
        form = {
            "title": "Grade Ex",
            "description": "desc",
            "difficulty": "intermediate",
            "module_id": self.topic.id,
            "exercise_type": "english_to_formula",
            "hints": ["", "", ""],
            "allowed_operators": ["G", "F", "X", "U", "¬", "∧", "∨", "→"],
            "declared_aps": ["grant"],
            "parts": [
                {"id": part_id, "prompt": "eventually grant", "formula": part_formula, "hints": []}
            ],
        }
        form.update(overrides)
        return form

    def _judge_form(self, part_formula="F b", part_id="", **overrides):
        form = {
            "title": "Judge Ex",
            "description": "desc",
            "difficulty": "intermediate",
            "module_id": self.topic.id,
            "exercise_type": "judge",
            "hints": ["", "", ""],
            "allowed_operators": ["G", "F", "X", "U", "¬", "∧", "∨", "→"],
            "declared_aps": ["a", "b"],
            "parts": [{"id": part_id, "prompt": "", "formula": part_formula, "hints": []}],
        }
        form.update(overrides)
        return form

    def _attempt(self, ex):
        return Attempt.objects.create(
            exercise=ex, student=self.student, part=ex.parts.first(),
            is_correct=True, formula_input="F grant",
        )

    def test_new_exercise_no_reset(self):
        ex = services.persist_exercise(None, self._english_form(), None, True)
        self.assertEqual(ex._attempts_reset, 0)

    def test_description_only_change_keeps_attempts(self):
        ex = services.persist_exercise(None, self._english_form(), None, True)
        pid = str(ex.parts.first().id)
        self._attempt(ex)
        form = self._english_form(part_id=pid, description="totally new copy")
        ex2 = services.persist_exercise(ex, form, None, True)
        self.assertEqual(ex2._attempts_reset, 0)
        self.assertEqual(Attempt.objects.filter(exercise=ex).count(), 1)

    def test_part_formula_change_resets_attempts(self):
        ex = services.persist_exercise(None, self._english_form(), None, True)
        pid = str(ex.parts.first().id)
        self._attempt(ex)
        form = self._english_form(part_formula="G grant", part_id=pid)
        ex2 = services.persist_exercise(ex, form, None, True)
        self.assertEqual(ex2._attempts_reset, 1)
        self.assertEqual(Attempt.objects.filter(exercise=ex).count(), 0)

    def test_graph_change_resets_attempts(self):
        ex = services.persist_exercise(None, self._judge_form(), JUDGE_GRAPH, True)
        pid = str(ex.parts.first().id)
        Attempt.objects.create(
            exercise=ex, student=self.student, part=ex.parts.first(),
            is_correct=True, answer={"verdict": "holds"},
        )
        form = self._judge_form(part_id=pid)
        ex2 = services.persist_exercise(ex, form, JUDGE_GRAPH_ALT, True)
        self.assertEqual(ex2._attempts_reset, 1)
        self.assertEqual(Attempt.objects.filter(exercise=ex).count(), 0)

    def test_signature_change_without_attempts_no_reset(self):
        ex = services.persist_exercise(None, self._english_form(), None, True)
        pid = str(ex.parts.first().id)
        form = self._english_form(part_formula="G grant", part_id=pid)
        ex2 = services.persist_exercise(ex, form, None, True)
        self.assertEqual(ex2._attempts_reset, 0)


# ---------------------------------------------------------------------------
# C. Judge answer cached at save
# ---------------------------------------------------------------------------
class JudgeAnswerCacheTests(TeacherViewTestCase):
    def _judge_form(self, parts=None, **overrides):
        if parts is None:
            parts = [
                {"id": "", "prompt": "", "formula": "G a", "hints": []},
                {"id": "", "prompt": "", "formula": "F b", "hints": []},
            ]
        form = {
            "title": "Judge Cache",
            "description": "desc",
            "difficulty": "intermediate",
            "module_id": self.topic.id,
            "exercise_type": "judge",
            "hints": ["", "", ""],
            "allowed_operators": ["G", "F", "X", "U", "¬", "∧", "∨", "→"],
            "declared_aps": ["a", "b"],
            "parts": parts,
        }
        form.update(overrides)
        return form

    def test_answer_holds_cached_at_save(self):
        ex = services.persist_exercise(None, self._judge_form(), JUDGE_GRAPH, True)
        by_formula = {p.formula: p for p in ex.parts.all()}
        self.assertIs(by_formula["G a"].answer_holds, False)
        self.assertIs(by_formula["F b"].answer_holds, True)

    def test_judge_answer_key_reads_cached_value(self):
        ex = services.persist_exercise(None, self._judge_form(), JUDGE_GRAPH, True)
        key = services.judge_answer_key(ex)
        holds_by_formula = {formula: holds for (_pos, formula, holds) in key}
        self.assertEqual(holds_by_formula["G a"], False)
        self.assertEqual(holds_by_formula["F b"], True)

    def test_editing_formula_recomputes_answer_holds(self):
        ex = services.persist_exercise(None, self._judge_form(), JUDGE_GRAPH, True)
        fb = next(p for p in ex.parts.all() if p.formula == "F b")
        ga = next(p for p in ex.parts.all() if p.formula == "G a")
        parts = [
            {"id": str(ga.id), "prompt": "", "formula": "G a", "hints": []},
            {"id": str(fb.id), "prompt": "", "formula": "G b", "hints": []},
        ]
        ex2 = services.persist_exercise(ex, self._judge_form(parts=parts), JUDGE_GRAPH, True)
        edited = next(p for p in ex2.parts.all() if p.formula == "G b")
        self.assertIs(edited.answer_holds, False)


# ---------------------------------------------------------------------------
# D. Misconception breakdown is a static mock
# ---------------------------------------------------------------------------
class MisconceptionBreakdownMockTests(TeacherViewTestCase):
    def test_breakdown_shape_and_stability(self):
        first = services.misconception_breakdown()
        self.assertTrue(first)
        for row in first:
            self.assertEqual(set(row), {"key", "label", "description", "percentage"})
        second = services.misconception_breakdown()
        self.assertEqual(first, second)
