"""Tolerant parsing of Jev answers.

The Choice/Score/Noul contract is stable, but field naming varies a little
between the TypeSafe host and the free-tier host, so each getter accepts a
few aliases rather than hard-failing on a key name.
"""

from __future__ import annotations

from typing import Any


def _first(payload: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def choice(answer: dict[str, Any]) -> tuple[str, dict[str, float], float]:
    """-> (winning option, probability per option, confidence)."""
    probabilities_raw = _first(answer, "probabilities", "distribution", "options", default={}) or {}
    probabilities = {str(k): _as_float(v) for k, v in dict(probabilities_raw).items()}
    picked = _first(answer, "choice", "value", "answer", "label")
    if picked is None and probabilities:
        picked = max(probabilities, key=lambda k: probabilities[k])
    confidence = _as_float(_first(answer, "confidence", default=None), default=_top_mass(probabilities))
    return str(picked), probabilities, confidence


def noul(answer: dict[str, Any]) -> float:
    """-> probability in 0..1 that the proposition is true."""
    return _as_float(_first(answer, "noul", "probability", "value", "p_true", default=0.0))


def score(answer: dict[str, Any], fallback_legend: list[str]) -> tuple[float, list[str], float]:
    """-> (weighted score, legend, confidence)."""
    value = _as_float(_first(answer, "score", "value", default=0.0))
    legend_raw = _first(answer, "legend", "levels", default=None)
    legend = [str(x) for x in legend_raw] if isinstance(legend_raw, list) else list(fallback_legend)
    confidence = _as_float(_first(answer, "confidence", default=1.0), default=1.0)
    return value, legend, confidence


def _top_mass(probabilities: dict[str, float]) -> float:
    return max(probabilities.values()) if probabilities else 0.0
