"""Offline stand-in for Jev, used only when no API key is configured.

It emits the same wire shape as the real service so the parsing path is
identical, but the numbers come from keyword heuristics, not a model. Every
report that uses it is stamped ``jev=simulated`` -- never present simulated
output as a real Jev verdict.
"""

from __future__ import annotations

import math
from typing import Any

_CATEGORY_CUES: dict[str, tuple[tuple[str, float], ...]] = {
    "infra": (
        ("429", 3.0), ("too many requests", 3.0), ("503 server error", 2.5),
        ("502 bad gateway", 2.5), ("connection reset", 2.0), ("could not resolve host", 2.5),
        ("no space left", 3.0), ("oomkilled", 3.0), ("out of memory", 2.0),
        ("runner has received a shutdown", 3.0), ("failed to restore cache", 2.0),
        ("rate limit", 2.5), ("the operation was canceled", 1.5),
    ),
    "dependency": (
        ("resolutionimpossible", 3.5), ("dependency resolver", 3.0),
        ("no matching distribution", 3.0), ("eresolve", 3.0), ("peer dep", 2.0),
        ("version conflict", 2.5), ("yanked", 2.0), ("lockfile", 1.5),
        ("conflict is caused by", 3.0), ("no matching distributions", 2.5),
        ("depends on", 1.5), ("loosen the range of package versions", 2.5),
        ("incompatible", 1.5), ("requires a different", 2.0),
    ),
    "config": (
        ("did not find expected key", 3.5), ("yaml", 2.0), ("invalid workflow file", 3.5),
        ("unrecognized named-value", 3.0), ("secret", 1.5), ("is not set", 1.5),
        ("unknown option", 2.0), ("no such config", 2.0),
        ("parsererror", 3.0), ("scannererror", 3.0), ("expected <block end>", 3.0),
        ("while parsing a block", 2.5), ("malformed", 2.0),
    ),
    "flaky": (
        ("timed out", 2.0), ("timeout", 1.5), ("flaky", 3.0), ("intermittent", 3.0),
        ("race", 2.0), ("address already in use", 2.5), ("passed on retry", 3.0),
        ("attempt 2", 1.5), ("eventually", 1.0), ("sleep", 1.0), ("jitter", 1.5),
        ("nondeterministic", 3.0),
    ),
    "regression": (
        ("assertionerror", 3.0), ("assert ", 1.5), ("expected", 1.0),
        ("typeerror", 2.0), ("attributeerror", 2.0), ("valueerror", 1.5),
        ("but got", 1.5), ("!=", 0.5), ("compile error", 2.0), ("undefined", 1.0),
    ),
}

_SEVERITY_CUES: tuple[tuple[str, float], ...] = (
    ("security", 1.2), ("token", 0.6), ("password", 0.9), ("data loss", 1.4),
    ("corrupt", 1.0), ("payment", 1.0), ("charge", 0.8), ("refund", 0.8),
    ("price", 0.7), ("discount", 0.5), ("migration", 0.6), ("deadlock", 0.8),
)


def simulate(state: str, questions: dict[str, dict], hints: dict[str, Any]) -> dict[str, Any]:
    text = state.lower()
    answers: dict[str, Any] = {}
    for key, spec in questions.items():
        kind = spec.get("type")
        if kind == "choice":
            answers[key] = _choice(text, spec, hints)
        elif kind == "noul":
            answers[key] = _noul(key, text, hints)
        elif kind == "score":
            answers[key] = _score(text, spec, hints)
    return {
        "model": "simulated-offline",
        "answers": answers,
        "usage": {"input_tokens": len(state) // 4, "output_tokens": 0},
    }


# --------------------------------------------------------------------------


def _choice(text: str, spec: dict, hints: dict[str, Any]) -> dict[str, Any]:
    options = list(spec.get("criteria", {}).keys())
    weights: dict[str, float] = {}
    for option in options:
        cues = _CATEGORY_CUES.get(option, ())
        weights[option] = 0.35 + sum(w for cue, w in cues if cue in text)

    # A failure whose implicated files are all inside the diff leans regression.
    overlap = float(hints.get("diff_overlap", 0.0))
    if "regression" in weights:
        weights["regression"] += 1.6 * overlap
    if "flaky" in weights and overlap == 0.0:
        weights["flaky"] += 0.4
    # Step-level failures are rarely a code regression.
    if hints.get("unit_kind") == "step" and "regression" in weights:
        weights["regression"] *= 0.35

    probabilities = _softmax(weights, temperature=2.2)
    picked = max(probabilities, key=lambda k: probabilities[k])
    ordered = sorted(probabilities.values(), reverse=True)
    margin = ordered[0] - (ordered[1] if len(ordered) > 1 else 0.0)
    confidence = round(min(0.97, 0.42 + 1.15 * margin), 3)
    return {
        "type": "choice",
        "choice": picked,
        "probabilities": {k: round(v, 3) for k, v in probabilities.items()},
        "confidence": confidence,
    }


def _noul(key: str, text: str, hints: dict[str, Any]) -> dict[str, Any]:
    if key == "caused_by_pr":
        value = 0.12 + 0.82 * float(hints.get("diff_overlap", 0.0))
    elif key == "addresses_failure":
        value = float(hints.get("addresses_failure", 0.5))
    elif key == "touches_unrelated_files":
        value = float(hints.get("unrelated_ratio", 0.0))
    elif key == "weakens_tests":
        value = 0.95 if hints.get("static_weakens") else 0.04
    elif key == "follows_project_docs":
        value = 0.25 if hints.get("static_weakens") else 0.82
    else:
        value = 0.5
    return {"type": "noul", "noul": round(min(0.99, max(0.01, value)), 3)}


def _score(text: str, spec: dict, hints: dict[str, Any]) -> dict[str, Any]:
    levels = list(spec.get("criteria", {}).keys())
    base = {"regression": 2.0, "dependency": 1.6, "config": 1.4, "infra": 1.2, "flaky": 0.9}
    value = base.get(str(hints.get("category", "regression")), 1.5)
    value += sum(w for cue, w in _SEVERITY_CUES if cue in text)
    value = max(0.0, min(float(len(levels) - 1), value))
    return {
        "type": "score",
        "score": round(value, 2),
        "legend": [spec["criteria"][lvl] for lvl in levels],
        "confidence": 0.7,
    }


def _softmax(weights: dict[str, float], temperature: float = 1.0) -> dict[str, float]:
    if not weights:
        return {}
    peak = max(weights.values())
    exponentials = {k: math.exp((v - peak) / temperature) for k, v in weights.items()}
    total = sum(exponentials.values()) or 1.0
    return {k: v / total for k, v in exponentials.items()}
