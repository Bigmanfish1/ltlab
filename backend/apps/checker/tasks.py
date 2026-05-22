from celery import shared_task


@shared_task
def run_ltl_check(kripke_graph: dict, ltl_formula: str) -> dict:
    """
    Placeholder for the LTL model checking task.

    Receives a Kripke structure (as a JSON-serialisable dict from Cytoscape.js)
    and an LTL formula string. Returns a result dict indicating whether the
    property holds and, if not, a counterexample trace.

    This will be implemented in the Engine Integration phase.
    """
    raise NotImplementedError("LTL model checking engine not yet implemented.")
