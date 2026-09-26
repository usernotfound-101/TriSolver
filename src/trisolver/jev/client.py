"""HTTP client for Jev's System One endpoint, with an offline fallback.

Live calls are cached on disk keyed by the exact request body, so re-running
a demo is instant, deterministic, and does not spend quota twice on the
identical question.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..config import Config
from . import simulate as _simulate

_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4
_TIMEOUT_SECONDS = 30.0


class JevError(RuntimeError):
    """Raised when Jev is configured but unreachable or rejecting requests."""


@dataclass(slots=True)
class JevResponse:
    model: str
    answers: dict[str, Any]
    mode: str  # "live" | "cached" | "simulated"
    usage: dict[str, Any] = field(default_factory=dict)
    quota: dict[str, Any] = field(default_factory=dict)

    @property
    def is_real(self) -> bool:
        return self.mode in ("live", "cached")


class JevClient:
    """Ask typed questions about one state.

    Mode is decided once, at construction:
      * ``live``      -- a key is present (or ``--jev live`` was forced)
      * ``simulated`` -- no key, heuristics stand in and everything is stamped
    """

    def __init__(self, config: Config, cache_dir: Path | None = None) -> None:
        self.config = config
        self.cache_dir = cache_dir or (config.workdir / ".jev-cache")
        self._client: httpx.Client | None = None

        requested = config.jev_mode
        if requested == "simulated":
            self.mode = "simulated"
        elif requested == "live":
            if not config.jev_api_key:
                raise JevError("--jev live requires JEV_API_KEY to be set (see .env.example)")
            self.mode = "live"
        else:  # auto
            self.mode = "live" if config.jev_api_key else "simulated"

        self.calls = 0
        self.cache_hits = 0

    # ----------------------------------------------------------------- api

    def ask(
        self,
        state: str,
        questions: dict[str, dict],
        *,
        hints: dict[str, Any] | None = None,
    ) -> JevResponse:
        """Send one state plus a set of typed questions; get typed answers."""
        hints = hints or {}
        if self.mode == "simulated":
            payload = _simulate.simulate(state, questions, hints)
            self.calls += 1
            return JevResponse(
                model=payload["model"], answers=payload["answers"], mode="simulated",
                usage=payload.get("usage", {}),
            )

        body = {"state": state, "questions": questions, "model": self.config.jev_model}
        cached = self._cache_read(body)
        if cached is not None:
            self.cache_hits += 1
            return JevResponse(
                model=cached.get("model", ""), answers=cached.get("answers", {}), mode="cached",
                usage=cached.get("usage", {}), quota=cached.get("quota", {}),
            )

        payload = self._post(body)
        self._cache_write(body, payload)
        self.calls += 1
        return JevResponse(
            model=payload.get("model", ""), answers=payload.get("answers", {}), mode="live",
            usage=payload.get("usage", {}), quota=payload.get("quota", {}),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "JevClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -------------------------------------------------------------- internals

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            self._client = httpx.Client(timeout=_TIMEOUT_SECONDS)
        headers = {
            "Authorization": f"Bearer {self.config.jev_api_key}",
            "content-type": "application/json",
        }
        last_error = ""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self._client.post(self.config.jev_base_url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"transport error: {exc}"
            else:
                if response.status_code == 200:
                    return response.json()
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                if response.status_code not in _RETRY_STATUS:
                    break
            if attempt < _MAX_ATTEMPTS:
                time.sleep(min(8.0, 0.5 * 2 ** (attempt - 1)))
        raise JevError(f"Jev request failed after {_MAX_ATTEMPTS} attempt(s). {last_error}")

    def _cache_key(self, body: dict[str, Any]) -> str:
        blob = json.dumps(
            {"url": self.config.jev_base_url, "body": body}, sort_keys=True, ensure_ascii=False
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def _cache_read(self, body: dict[str, Any]) -> dict[str, Any] | None:
        path = self.cache_dir / f"{self._cache_key(body)}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _cache_write(self, body: dict[str, Any], payload: dict[str, Any]) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{self._cache_key(body)}.json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # a demo must not die because the cache is unwritable
