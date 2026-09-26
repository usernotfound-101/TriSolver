"""Answer parsing must survive field-name drift between Jev hosts."""

import json

import httpx
import pytest

from trisolver.config import Config
from trisolver.jev import JevClient, JevError
from trisolver.jev import schema


def test_choice_parses_the_documented_shape():
    picked, probabilities, confidence = schema.choice(
        {"type": "choice", "choice": "flaky", "probabilities": {"flaky": 0.7, "regression": 0.3}, "confidence": 0.81}
    )
    assert (picked, confidence) == ("flaky", 0.81)
    assert probabilities["flaky"] == 0.7


def test_choice_falls_back_to_the_argmax_when_no_winner_is_named():
    picked, _, confidence = schema.choice({"distribution": {"infra": 0.62, "flaky": 0.38}})
    assert picked == "infra"
    assert confidence == pytest.approx(0.62)


def test_noul_accepts_its_aliases():
    assert schema.noul({"noul": 0.42}) == 0.42
    assert schema.noul({"probability": 0.42}) == 0.42
    assert schema.noul({}) == 0.0


def test_score_keeps_the_fallback_legend_when_none_is_returned():
    value, legend, _ = schema.score({"score": 2.4}, ["a", "b", "c", "d"])
    assert value == 2.4
    assert legend == ["a", "b", "c", "d"]


def test_garbage_values_do_not_raise():
    assert schema.noul({"noul": "not-a-number"}) == 0.0
    picked, _, _ = schema.choice({})
    assert picked == "None"


def test_live_mode_without_a_key_is_an_error(tmp_path):
    config = Config(None, "https://example.invalid", "jev-latest", None, tmp_path, "live")
    with pytest.raises(JevError, match="requires JEV_API_KEY"):
        JevClient(config)


def test_auto_mode_simulates_without_a_key(tmp_path):
    config = Config(None, "https://example.invalid", "jev-latest", None, tmp_path, "auto")
    assert JevClient(config).mode == "simulated"


def test_live_calls_are_cached_on_disk(tmp_path, monkeypatch):
    """A second identical question must not cost a second request."""
    config = Config("jv_live_test", "https://example.invalid/v1/systemone", "jev-latest", None, tmp_path, "live")
    client = JevClient(config, cache_dir=tmp_path / "cache")

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.9}}})

    client._client = httpx.Client(transport=httpx.MockTransport(handler))

    questions = {"q": {"type": "noul", "instructions": "?", "criteria": {}}}
    first = client.ask("state", questions)
    second = client.ask("state", questions)

    assert len(requests) == 1
    assert first.mode == "live"
    assert second.mode == "cached"
    assert second.answers == first.answers
    assert json.loads(requests[0].content)["state"] == "state"


def test_http_errors_surface_as_jev_errors(tmp_path):
    config = Config("jv_live_test", "https://example.invalid/v1/systemone", "jev-latest", None, tmp_path, "live")
    client = JevClient(config, cache_dir=tmp_path / "cache")
    client._client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key"))
    )
    with pytest.raises(JevError, match="HTTP 401"):
        client.ask("state", {"q": {"type": "noul", "instructions": "?", "criteria": {}}})
