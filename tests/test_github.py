"""Live ingestion. Mocked at the transport, because the real bugs here were
in how requests are made, not in what is done with the answers.
"""

import io
import zipfile

import httpx
import pytest

from trisolver.ingest.github import _archive_logs, _failed_job_logs, _follow, fetch_run

API = "https://api.github.com"
SLUG = "acme/storefront"
RUN = 42
BLOB = "https://productionresultssa19.blob.core.windows.net/actions-results/abc/log.txt"


def client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        headers={"Authorization": "Bearer secret-token"},
    )


def jobs_payload(*conclusions: str) -> dict:
    return {
        "jobs": [
            {"id": 100 + i, "name": f"job-{i}", "conclusion": c}
            for i, c in enumerate(conclusions)
        ]
    }


def empty_zip() -> bytes:
    buffer = io.BytesIO()
    zipfile.ZipFile(buffer, "w").close()
    return buffer.getvalue()


def test_follow_does_not_leak_the_token_to_the_signed_url(monkeypatch):
    """The blob URL carries its own auth and 404s if the bearer tags along.

    This was a real failure against a live run: the redirect was followed with
    the Authorization header attached, Azure answered BlobNotFound, and the
    run looked like it had no failures at all.

    ``_follow`` builds its own credential-free client for the second hop, so
    the mock transport has to be installed on ``httpx.Client`` itself.
    """
    seen: dict[str, httpx.Headers] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[str(request.url)] = request.headers
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": BLOB})
        return httpx.Response(200, text="log body")

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    with real_client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        headers={"Authorization": "Bearer secret-token"},
    ) as c:
        response = _follow(c, f"{API}/repos/{SLUG}/actions/jobs/1/logs")

    assert response.text == "log body"
    assert "authorization" in seen[f"{API}/repos/{SLUG}/actions/jobs/1/logs"]
    assert "authorization" not in seen[BLOB], "the GitHub token leaked to the blob host"


def test_only_failed_jobs_are_downloaded():
    requested: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=jobs_payload("success", "failure", "timed_out"))
        job_id = int(request.url.path.split("/")[-2])
        requested.append(job_id)
        return httpx.Response(200, text=f"body of {job_id}")

    with client(handler) as c:
        log = _failed_job_logs(c, SLUG, RUN)

    assert requested == [101, 102]  # the successful job is never fetched
    assert "=== JOB: job-1 ===" in log
    assert "=== JOB: job-2 ===" in log
    assert "job-0" not in log


def test_a_green_run_is_an_explicit_error_not_an_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jobs_payload("success", "success"))

    with client(handler) as c, pytest.raises(RuntimeError, match="no failed jobs"):
        _failed_job_logs(c, SLUG, RUN)


def test_logs_not_uploaded_yet_says_so():
    """GitHub finishes uploading a minute or two after the run ends. Until
    then every log 404s, and that must not read as 'nothing failed'."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=jobs_payload("failure"))
        if request.url.path.endswith("/runs/42/logs"):
            return httpx.Response(200, content=empty_zip())
        return httpx.Response(404, text="BlobNotFound")

    with client(handler) as c, pytest.raises(RuntimeError, match="downloadable yet"):
        _failed_job_logs(c, SLUG, RUN)


def test_the_archive_is_used_when_per_job_logs_are_missing():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("job-0/1_Run pytest.txt", "pytest output")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=jobs_payload("failure"))
        if request.url.path.endswith("/runs/42/logs"):
            return httpx.Response(200, content=buffer.getvalue())
        return httpx.Response(404)

    with client(handler) as c:
        log = _failed_job_logs(c, SLUG, RUN)
    assert "pytest output" in log


def test_an_empty_archive_yields_nothing_rather_than_a_false_negative():
    """A 22-byte valid-but-empty zip is what a just-finished run returns."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=empty_zip())

    with client(handler) as c:
        assert _archive_logs(c, SLUG, RUN, {"job-0"}) == ""


def test_fetch_run_requires_a_run_url():
    with pytest.raises(ValueError, match="Actions run URL"):
        fetch_run("https://github.com/acme/storefront/pull/1", token="x")


def test_fetch_run_requires_a_token():
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        fetch_run("https://github.com/acme/storefront/actions/runs/42", token=None)
