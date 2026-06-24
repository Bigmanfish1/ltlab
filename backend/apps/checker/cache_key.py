"""Stable cache key for LTL verification results.

Shared by views.py (cache read) and tasks.py (cache write) to avoid circular
imports.  The key is a SHA-256 hash over a deterministically serialised
(formula, graph) pair, which means the same formula + graph always hits the
same cache slot regardless of node ordering, phantom elements, or whitespace.
"""

from __future__ import annotations

import hashlib
import json


def _canonical_graph(graph: dict) -> dict:
    """Strip position/style data and sort nodes + edges for stable hashing."""
    elements = graph.get("elements", {})
    nodes = sorted(
        [
            {
                "id":      n["data"]["id"],
                "initial": bool(n["data"].get("initial")),
                "props":   sorted(n["data"].get("props", [])),
            }
            for n in elements.get("nodes", [])
            if not n.get("data", {}).get("phantom")
        ],
        key=lambda n: n["id"],
    )
    edges = sorted(
        [
            {"source": e["data"]["source"], "target": e["data"]["target"]}
            for e in elements.get("edges", [])
            if not e.get("data", {}).get("phantom")
        ],
        key=lambda e: (e["source"], e["target"]),
    )
    return {"nodes": nodes, "edges": edges}


def make_cache_key(formula_str: str, graph: dict) -> str:
    """Return a Redis-safe cache key for the given (formula, graph) pair."""
    canonical = {
        "formula": formula_str.strip(),
        "graph":   _canonical_graph(graph),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"ltl_result:{digest}"
