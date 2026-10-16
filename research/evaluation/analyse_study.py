"""Analyse the LTLab evaluation study: two Google Form exports plus the app's attempts export.

See README.md in this directory for inputs, outputs and why the data is not in the repository.
"""

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

CONCEPTS = {
    "C1": "X semantics",
    "C2": "F vs G",
    "C3": "GF vs FG",
    "C4": "U semantics",
    "C5": "Safety vs liveness",
    "C6": "Path reading",
}

# Stem prefix -> (form, concept, correct option). Keys copied from create_forms.gs, where the
# correct option is the first one listed; the Forms' own [Score] column is cross-checked below.
ITEMS = {
    "Let a path be the sequence of states s0, s1, s2 and so on. The formula X p": (
        "A", "C1", "p holds in s1, the state directly after the first one"),
    "Which formula says that p holds at some point": ("A", "C2", "F p"),
    "Which reading of G F p is correct?": (
        "A", "C3", "p keeps happening again and again, without end"),
    "For p U q to hold on a path": (
        "A", "C4", "q holds at some position, and p holds at every position before it"),
    '"The two processes are never in their critical sections': (
        "A", "C5", "some finite portion of the path, read from the start, already shows the violation"),
    "A drinks machine has states": ("A", "C6", "G F beer"),
    "Let a path be the sequence of states s0, s1, s2 and so on. The formula X q": (
        "B", "C1", "q holds in s1, the state directly after the first one"),
    "Which formula says that r holds in every state": ("B", "C2", "G r"),
    "Which reading of F G r is correct?": ("B", "C3", "after some point, r holds forever"),
    "For r U s to hold on a path": (
        "B", "C4", "s holds at some position, and r holds at every position before it"),
    '"Whenever a process asks to enter its critical section': (
        "B", "C5", "only as an infinite path, since no finite portion of it can settle the question"),
    "Two processes are each outside their critical section": ("B", "C6", "G (t1 implies F c1)"),
}

LIKERT = [
    ("L1", "Ease of adoption",
     "I could figure out how to use the tool on my own, without a tutorial or outside help."),
    ("L2", "Preference over paper",
     "Given the choice, I would rather practise model checking in this tool than on paper."),
    ("L3", "Confidence gain",
     "After this session I feel more sure of myself when reading and writing LTL formulas."),
    ("L4", "Engagement", "Working through the exercises held my attention."),
    ("L5", "Motivation",
     "Getting an immediate result after each submission encouraged me to keep trying."),
    ("L6", "Recommendation",
     "I would tell other students taking Formal Methods to use this tool."),
    ("L7", "Feedback helpfulness",
     "When my answer was wrong, the tool's response helped me understand what to fix."),
    ("L8", "Counterexample paths",
     "The counterexample paths highlighted on the state graph made it clearer to me why a formula fails."),
]

OPEN = [
    ("Q1", "Which part of the tool did the most for your understanding, and why?"),
    ("Q2", "If you could change one thing about the tool before it is used in a classroom, what would it be?"),
    ("Q3", "Is there anything else you would like to tell us about your experience?"),
]

BACKGROUND = [
    ("level", "What is your current level of study?"),
    ("recency", "When did you last actively work with LTL or model checking?"),
    ("device", "What kind of device are you using for this session?"),
]

# Parts per exercise in the evaluation module, from seed_evaluation.py.
EXERCISES = [
    ("E1", "Specification Patterns", ["G !p", "F p", "G (p → F q)"]),
    ("E2", "Mutual Exclusion Requirements", ["G !(c1 ∧ c2)", "G (t1 → F c1)", "G F c1"]),
    ("E3", "Find a Path: Vending Machine", ["F soda", "G F beer", "!soda U beer"]),
    ("E4", "Find a Path: Starvation", ["F (t1 ∧ G !c1)", "F (c1 ∧ X(!c1 U c1))"]),
    ("E5", "Judge: First-Attempt Protocol", ["G !(c1 ∧ c2)", "G (t1 → F c1)", "G (t2 → F c2)"]),
    ("E6", "Judge: Second-Attempt Protocol", ["G !(c1 ∧ c2)", "G (t1 → F c1)", "G (t2 → F c2)"]),
]

BLUE, ORANGE = "#2a78d6", "#eb6834"
BLUE_DARK, BLUE_LIGHT = "#256abf", "#86b6ef"
GREY_MID, GREY_LINE = "#d9d8d4", "#c3c2b7"
RED, RED_LIGHT = "#e34948", "#f0a19c"
INK, INK_2 = "#0b0b0b", "#52514e"


def read_form(path, order):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    header, body = rows[0], rows[1:]

    def col(stem, suffix=""):
        hits = [i for i, h in enumerate(header)
                if h.startswith(stem) and h.endswith(suffix)
                and (suffix or not h.endswith("]"))]
        if len(hits) != 1:
            raise SystemExit(f"{path}: expected one column for {stem!r}{suffix}, got {hits}")
        return hits[0]

    first_form, second_form = order[0], order[1]
    out = []
    for r in body:
        rec = {
            "code": r[col("Participant code")].strip().upper(),
            "submitted": datetime.strptime(
                r[col("Timestamp")].replace(" EEST", ""), "%Y/%m/%d %I:%M:%S %p"),
            "order": order,
            "background": {k: r[col(q)].strip() for k, q in BACKGROUND},
            "likert": {k: int(r[col(q)]) for k, _, q in LIKERT},
            "open": {k: r[col(q)].strip() for k, q in OPEN},
            "items": {},
        }
        for stem, (form, concept, key) in ITEMS.items():
            answer = r[col(stem)].strip()
            forms_score = float(r[col(stem, " [Score]")].split("/")[0])
            correct = answer == key
            if correct != (forms_score == 1.0):
                raise SystemExit(f"{rec['code']}: key disagrees with Forms score on {stem!r}")
            phase = "pre" if form == first_form else "post"
            rec["items"][(form, concept)] = {"answer": answer, "correct": correct, "phase": phase}
        for phase in ("pre", "post"):
            rec[phase] = sum(v["correct"] for v in rec["items"].values() if v["phase"] == phase)
        rec["gain"] = rec["post"] - rec["pre"]
        out.append(rec)
    return out


def read_attempts(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["participant_code"] = r["participant_code"].strip().upper()
        r["ex"] = int(r["exercise_position"])
        r["part"] = int(r["part_position"])
        r["is_correct"] = r["is_correct"] == "True"
        r["hints_used"] = int(r["hints_used"])
        r["created_at"] = datetime.fromisoformat(r["created_at"])
    return rows


def part_outcomes(attempts, codes):
    """Per (exercise, part): each participant's attempts up to and including the first correct one."""
    by_key = defaultdict(lambda: defaultdict(list))
    for a in sorted(attempts, key=lambda a: a["created_at"]):
        if a["participant_code"] in codes:
            by_key[(a["ex"], a["part"])][a["participant_code"]].append(a)
    outcomes = {}
    for key, per_student in by_key.items():
        rows = {}
        for code, seq in per_student.items():
            first_ok = next((i for i, a in enumerate(seq) if a["is_correct"]), None)
            rows[code] = {
                "submissions": len(seq),
                "solved": first_ok is not None,
                "tries_to_solve": first_ok + 1 if first_ok is not None else None,
                "first_try": first_ok == 0,
                "wrong_before_solve": first_ok if first_ok is not None else len(seq),
                "hint_seen": any(a["hints_used"] > 0 for a in seq),
            }
        outcomes[key] = rows
    return outcomes


def style_axes(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GREY_LINE)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.yaxis.label.set_color(INK_2)
    ax.xaxis.label.set_color(INK_2)


def save(fig, out, name):
    fig.savefig(out / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(out / f"{name}.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def order_balanced_gain(people):
    """Unweighted mean of the two order groups' mean gains: a fixed form-difficulty
    difference enters the AB and BA gains with opposite sign, so it cancels."""
    by = defaultdict(list)
    for p in people:
        by[p["order"]].append(p["gain"])
    return statistics.mean([statistics.mean(by["AB"]), statistics.mean(by["BA"])])


def form_by_position(people):
    cells = defaultdict(list)
    for p in people:
        for form in "AB":
            position = "first" if p["order"][0] == form else "second"
            cells[f"{form}_{position}"].append(
                sum(v["correct"] for (f, _), v in p["items"].items() if f == form))
    return {k: {"n": len(v), "mean": statistics.mean(v)} for k, v in sorted(cells.items())}


def fig_concepts(people, out):
    n = len(people)
    keys = list(CONCEPTS)
    pre = [sum(v["correct"] for p in people for (f, c), v in p["items"].items()
               if c == k and v["phase"] == "pre") / n * 100 for k in keys]
    post = [sum(v["correct"] for p in people for (f, c), v in p["items"].items()
                if c == k and v["phase"] == "post") / n * 100 for k in keys]
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    w = 0.36
    xs = range(len(keys))
    for offset, vals, colour, label in ((-w / 2, pre, BLUE, "Pre-test"),
                                         (w / 2, post, ORANGE, "Post-test")):
        bars = ax.bar([x + offset for x in xs], vals, w - 0.04, color=colour, label=label,
                      edgecolor="white", linewidth=1)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.0f}", ha="center",
                    va="bottom", fontsize=8, color=INK)
    wrapped = {"C5": "Safety vs\nliveness", "C6": "Path\nreading"}
    ax.set_xticks(list(xs), [f"{k}\n{wrapped.get(k, CONCEPTS[k])}" for k in keys])
    ax.set_ylim(0, 112)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel(f"Participants correct (%), n = {n}")
    ax.grid(axis="y", color="#ecebe7", linewidth=0.8)
    ax.set_axisbelow(True)
    style_axes(ax)
    ax.legend(frameon=False, fontsize=9, loc="upper left", bbox_to_anchor=(0, 1.12), ncol=2)
    save(fig, out, "fig_concepts_pre_post")
    return dict(zip(keys, zip(pre, post)))


def fig_scores(people, out):
    ranked = sorted(people, key=lambda p: (p["gain"], p["post"], p["label"]))
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    for y, p in enumerate(ranked):
        marker = "o" if p["order"] == "AB" else "s"
        ax.plot([p["pre"], p["post"]], [y, y], color=GREY_LINE, linewidth=2, zorder=1)
        ax.scatter(p["pre"], y, s=60, color=BLUE, marker=marker, zorder=2,
                   edgecolor="white", linewidth=1.5)
        ax.scatter(p["post"], y, s=60, color=ORANGE, marker=marker, zorder=3,
                   edgecolor="white", linewidth=1.5)
        sign = "+" if p["gain"] > 0 else ""
        ax.text(6.45, y, f"{sign}{p['gain']}", va="center", fontsize=8, color=INK)
    ax.set_yticks(range(len(ranked)), [f"{p['label']} ({p['order']})" for p in ranked])
    ax.set_xlim(-0.3, 6.8)
    ax.set_xticks(range(7))
    ax.set_xlabel("Test score (out of 6)")
    ax.text(6.45, len(ranked) - 0.3, "Gain", fontsize=8, color=INK_2, ha="left")
    ax.grid(axis="x", color="#ecebe7", linewidth=0.8)
    ax.set_axisbelow(True)
    style_axes(ax)
    handles = [
        Line2D([], [], marker="o", color="none", markerfacecolor=BLUE, markersize=7, label="Pre-test"),
        Line2D([], [], marker="o", color="none", markerfacecolor=ORANGE, markersize=7, label="Post-test"),
        Line2D([], [], marker="o", color="none", markerfacecolor=INK_2, markersize=6, label="Order AB"),
        Line2D([], [], marker="s", color="none", markerfacecolor=INK_2, markersize=6, label="Order BA"),
    ]
    ax.legend(handles=handles, frameon=False, fontsize=8, loc="upper left",
              bbox_to_anchor=(0, 1.14), ncol=4)
    save(fig, out, "fig_scores_per_participant")


def fig_likert(people, out):
    colours = {1: RED, 2: RED_LIGHT, 3: GREY_MID, 4: BLUE_LIGHT, 5: BLUE_DARK}
    labels = {1: "Strongly disagree", 2: "Disagree", 3: "Neutral", 4: "Agree", 5: "Strongly agree"}
    items = list(reversed(LIKERT))
    fig, ax = plt.subplots(figsize=(7.2, 3.9))
    for y, (k, name, _) in enumerate(items):
        counts = Counter(p["likert"][k] for p in people)
        left = -(counts[1] + counts[2] + counts[3] / 2)
        for level in range(1, 6):
            c = counts[level]
            if not c:
                continue
            ax.barh(y, c, left=left, color=colours[level], edgecolor="white", linewidth=2,
                    height=0.62)
            text_colour = "white" if level in (1, 5) else INK
            ax.text(left + c / 2, y, str(c), ha="center", va="center", fontsize=8,
                    color=text_colour)
            left += c
    ax.axvline(0, color=INK_2, linewidth=0.8)
    ax.set_yticks(range(len(items)), [f"{k} {name}" for k, name, _ in items])
    n = len(people)
    ax.set_xlim(-n * 0.6, n * 1.05)
    ax.set_xticks([])
    ax.spines["bottom"].set_visible(False)
    style_axes(ax)
    handles = [plt.Rectangle((0, 0), 1, 1, color=colours[i]) for i in range(1, 6)]
    ax.legend(handles, [labels[i] for i in range(1, 6)], frameon=False, fontsize=8, ncol=5,
              loc="upper left", bbox_to_anchor=(-0.32, 1.12))
    save(fig, out, "fig_likert")


def fig_parts(outcomes, n, out):
    keys, labels = [], []
    for e, (eid, _, parts) in enumerate(EXERCISES):
        for p, formula in enumerate(parts):
            keys.append((e, p))
            labels.append(f"{eid}.{p + 1}")
    first = [sum(r["first_try"] for r in outcomes.get(k, {}).values()) for k in keys]
    later = [sum(r["solved"] and not r["first_try"] for r in outcomes.get(k, {}).values())
             for k in keys]
    unsolved = [sum(not r["solved"] for r in outcomes.get(k, {}).values()) for k in keys]
    absent = [n - len(outcomes.get(k, {})) for k in keys]
    fig, ax = plt.subplots(figsize=(7.4, 3.4))
    xs = range(len(keys))
    bottom = [0] * len(keys)
    for vals, colour, label in ((first, BLUE_DARK, "Solved on first submission"),
                                (later, BLUE_LIGHT, "Solved after retrying"),
                                (unsolved, RED, "Attempted, not solved"),
                                (absent, GREY_MID, "Not attempted")):
        ax.bar(xs, vals, 0.7, bottom=bottom, color=colour, label=label, edgecolor="white",
               linewidth=1.5)
        for x, v, b in zip(xs, vals, bottom):
            if v:
                ax.text(x, b + v / 2, str(v), ha="center", va="center", fontsize=7,
                        color="white" if colour in (BLUE_DARK, RED) else INK)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_xticks(list(xs), labels, fontsize=8)
    ax.set_ylabel(f"Participants (n = {n})")
    ax.set_ylim(0, n + 0.2)
    style_axes(ax)
    boundary = 0
    for eid, _, parts in EXERCISES[:-1]:
        boundary += len(parts)
        ax.axvline(boundary - 0.5, color=GREY_LINE, linewidth=0.8, linestyle=":")
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper left", bbox_to_anchor=(-0.02, 1.13))
    save(fig, out, "fig_exercise_parts")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group1", required=True, help="Form export, order AB")
    ap.add_argument("--group2", required=True, help="Form export, order BA")
    ap.add_argument("--attempts", required=True)
    ap.add_argument("--out", default=str(Path(__file__).parent / "output"))
    ap.add_argument("--open-answers", help="optional path for the open answers (keep out of git)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    people = read_form(args.group1, "AB") + read_form(args.group2, "BA")
    # Numbered by keyed-hash code, not submission time, so a participant number cannot be
    # matched to the day someone took part.
    people.sort(key=lambda p: p["code"])
    for i, p in enumerate(people, start=1):
        p["label"] = f"S{i}"
    codes = {p["code"] for p in people}
    if len(codes) != len(people):
        raise SystemExit("duplicate participant code in the form responses")

    attempts = read_attempts(args.attempts)
    app_codes = {a["participant_code"] for a in attempts}
    unmatched_forms = sorted(codes - app_codes)
    app_only = sorted(app_codes - codes)
    used = [a for a in attempts if a["participant_code"] in codes]

    outcomes = part_outcomes(used, codes)
    total_parts = sum(len(parts) for _, _, parts in EXERCISES)
    for p in people:
        mine = [a for a in used if a["participant_code"] == p["code"]]
        rows = [o[p["code"]] for o in outcomes.values() if p["code"] in o]
        p["tool"] = {
            "submissions": len(mine),
            "parts_attempted": len(rows),
            "parts_solved": sum(r["solved"] for r in rows),
            "wrong_before_solve": sum(r["wrong_before_solve"] for r in rows),
            "parts_hint_seen": sum(r["hint_seen"] for r in rows),
            "span_min": round((max(a["created_at"] for a in mine)
                               - min(a["created_at"] for a in mine)).total_seconds() / 60, 1)
            if mine else None,
        }

    n = len(people)
    by_order = defaultdict(list)
    for p in people:
        by_order[p["order"]].append(p)

    form_scores = {"A": [], "B": []}
    for p in people:
        for form in "AB":
            form_scores[form].append(sum(v["correct"] for (f, _), v in p["items"].items()
                                         if f == form))

    mean_pre = statistics.mean(p["pre"] for p in people)
    mean_post = statistics.mean(p["post"] for p in people)

    parts_table = []
    for e, (eid, title, parts) in enumerate(EXERCISES):
        for pi, formula in enumerate(parts):
            rows = outcomes.get((e, pi), {})
            tries = [r["tries_to_solve"] for r in rows.values() if r["solved"]]
            parts_table.append({
                "part": f"{eid}.{pi + 1}", "exercise": title, "formula": formula,
                "attempted": len(rows),
                "solved": sum(r["solved"] for r in rows.values()),
                "first_try": sum(r["first_try"] for r in rows.values()),
                "median_tries_to_solve": statistics.median(tries) if tries else None,
                "max_tries_to_solve": max(tries) if tries else None,
                "hint_seen": sum(r["hint_seen"] for r in rows.values()),
            })

    gains = [p["gain"] for p in people]
    wrong = [p["tool"]["wrong_before_solve"] for p in people]
    rho_wrong = spearmanr(wrong, gains)
    rho_pre = spearmanr([p["pre"] for p in people], gains)

    summary = {
        "n_form_responses": n,
        "n_by_order": {k: len(v) for k, v in by_order.items()},
        "form_window": [min(p["submitted"] for p in people).isoformat(),
                        max(p["submitted"] for p in people).isoformat()],
        "app_codes": len(app_codes),
        "form_responses_without_attempts": len(unmatched_forms),
        "app_accounts_without_form_excluded": len(app_only),
        "attempt_rows_total": len(attempts),
        "attempt_rows_used": len(used),
        "background": {k: dict(Counter(p["background"][k] for p in people)) for k, _ in BACKGROUND},
        "tests": {
            "mean_pre": mean_pre, "mean_post": mean_post,
            "median_pre": statistics.median(p["pre"] for p in people),
            "median_post": statistics.median(p["post"] for p in people),
            "sd_pre": statistics.stdev(p["pre"] for p in people),
            "sd_post": statistics.stdev(p["post"] for p in people),
            "class_normalised_gain": (mean_post - mean_pre) / (6 - mean_pre),
            "improved": sum(g > 0 for g in gains),
            "unchanged": sum(g == 0 for g in gains),
            "declined": sum(g < 0 for g in gains),
            "pre_at_ceiling": sum(p["pre"] == 6 for p in people),
            "gain_distribution": dict(sorted(Counter(gains).items())),
            "by_order": {
                k: {"n": len(v),
                    "mean_pre": statistics.mean(p["pre"] for p in v),
                    "mean_post": statistics.mean(p["post"] for p in v),
                    "mean_gain": statistics.mean(p["gain"] for p in v)}
                for k, v in by_order.items()},
            "form_mean_score": {f: statistics.mean(s) for f, s in form_scores.items()},
            "form_mean_by_position": form_by_position(people),
            "order_balanced_gain": order_balanced_gain(people),
            "leave_one_out": {
                p["label"]: {
                    "pooled_gain": statistics.mean(q["gain"] for q in people if q is not p),
                    "order_balanced_gain": order_balanced_gain([q for q in people if q is not p]),
                    "form_mean_by_position": form_by_position([q for q in people if q is not p]),
                }
                for p in people},
            "per_concept_pct_pre_post": {},
            "per_concept_item_transitions": {},
        },
        "likert": {},
        "exercise_parts": parts_table,
        "tool_vs_gain": {
            "spearman_wrong_before_solve_vs_gain": [rho_wrong.statistic, rho_wrong.pvalue],
            "spearman_pre_vs_gain": [rho_pre.statistic, rho_pre.pvalue],
        },
        "participants": [
            {"label": p["label"], "order": p["order"], "pre": p["pre"], "post": p["post"],
             "gain": p["gain"], **p["tool"]}
            for p in people],
    }

    for k in CONCEPTS:
        trans = Counter()
        for p in people:
            pre = next(v["correct"] for (f, c), v in p["items"].items()
                       if c == k and v["phase"] == "pre")
            post = next(v["correct"] for (f, c), v in p["items"].items()
                        if c == k and v["phase"] == "post")
            trans[f"{'R' if pre else 'W'}->{'R' if post else 'W'}"] += 1
        summary["tests"]["per_concept_item_transitions"][k] = dict(trans)
        wrong_answers = Counter(v["answer"] for p in people for (f, c), v in p["items"].items()
                                if c == k and not v["correct"])
        summary["tests"].setdefault("per_concept_wrong_answers", {})[k] = dict(wrong_answers)

    for k, name, _ in LIKERT:
        vals = sorted(p["likert"][k] for p in people)
        half = len(vals) // 2
        q = (statistics.median(vals[:half]), None, statistics.median(vals[-half:]))
        summary["likert"][k] = {
            "construct": name, "counts": {i: vals.count(i) for i in range(1, 6)},
            "median": statistics.median(vals), "q1": q[0], "q3": q[2],
            "agree_pct": sum(v >= 4 for v in vals) / n * 100,
            "disagree_pct": sum(v <= 2 for v in vals) / n * 100,
        }

    summary["tests"]["per_concept_pct_pre_post"] = fig_concepts(people, out)
    fig_scores(people, out)
    fig_likert(people, out)
    fig_parts(outcomes, n, out)

    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str, ensure_ascii=False))
    if args.open_answers:
        lines = []
        for p in people:
            for k, q in OPEN:
                if p["open"][k]:
                    lines.append(f"[{p['label']} {k}] {p['open'][k]}")
        Path(args.open_answers).write_text("\n\n".join(lines) + "\n")
    print(json.dumps(summary, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
