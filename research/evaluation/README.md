# Evaluation study analysis

`analyse_study.py` produces every figure and summary statistic reported for the LTLab evaluation
study (COS700, University of Pretoria, October 2026).

## Inputs

The script needs three CSV files. None of them are in this repository.

| Argument | File |
|---|---|
| `--group1` | Google Forms response export for the form that gave test Form A first (order AB) |
| `--group2` | Google Forms response export for the form that gave test Form B first (order BA) |
| `--attempts` | The tool's attempts export for the evaluation module (Manage page, "Export CSV") |

The files are joined on the participant code that the tool shows each signed-in user. No file
contains a name, email address or account identifier.

## Running

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python analyse_study.py --group1 G1.csv --group2 G2.csv --attempts attempts.csv
```

Figures (PDF and PNG) and `summary.json` are written to `output/`, or to `--out DIR` if given.
`--open-answers FILE` also writes the open-ended answers to a text file for coding.

## Engine checks

`verify_engine.py` re-runs two checks reported in the write-up: the tool's lasso evaluator agrees
with Spot on 3,000 random formula and lasso pairs, and the answer keys of the path-reading test
items hold. It imports the application code, so run it from the repository root with the backend
dependencies installed: `python research/evaluation/verify_engine.py`.

## Data availability

Participants consented to their responses being reported as group totals and under participant
numbers. They did not consent to the raw responses being published, and the open-ended answers
are free text. The raw exports are therefore withheld; `output/` and `data/` are ignored by git.
