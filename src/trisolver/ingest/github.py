"""Live ingestion from the GitHub Actions API.

Only reached with ``--live`` or an explicit run URL. Needs a token with
``actions:read`` and ``pull_requests:read``.
"""

from __future__ import annotations

import io
import re
import zipfile

import httpx

from .base import RunBundle
from .diff import summarize_diff

API = "https://api.github.com"
RUN_URL = re.compile(r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/actions/runs/(?P<run_id>\d+)")
_TIMEOUT = 60.0


def fetch_run(target: str, *, token: str | None) -> RunBundle:
    match = RUN_URL.search(target)
    if not match:
        raise ValueError(
            f"Not a GitHub Actions run URL: {target!r}\n"
            "Expected https://github.com/<owner>/<repo>/actions/runs/<run_id>"
        )
    if not token:
        raise RuntimeError(
            "Live ingestion needs GITHUB_TOKEN (actions:read, pull_requests:read). "
            "Set it in .env, or drop the --live flag to use a fixture."
        )

    owner, repo, run_id = match.group("owner"), match.group("repo"), int(match.group("run_id"))
    slug = f"{owner}/{repo}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    with httpx.Client(timeout=_TIMEOUT, follow_redirects=False, headers=headers) as client:
        run = _json(client.get(f"{API}/repos/{slug}/actions/runs/{run_id}"))
        raw_log = _failed_job_logs(client, slug, run_id)
        pr_number = _pr_number(run)
        diff_text = _pr_diff(client, slug, pr_number) if pr_number else ""
        docs = _project_docs(client, slug, run.get("head_sha", ""))

    return RunBundle(
        name=f"{owner}-{repo}-{run_id}",
        repo=slug,
        raw_log=raw_log,
        diff=summarize_diff(diff_text),
        pr_number=pr_number,
        run_id=run_id,
        workflow=run.get("name", ""),
        title=run.get("display_title", ""),
        docs=docs,
        source="github",
        extra={"head_sha": run.get("head_sha", ""), "html_url": run.get("html_url", "")},
    )


# --------------------------------------------------------------------------


def _json(response: httpx.Response) -> dict:
    if response.status_code != 200:
        raise RuntimeError(f"GitHub {response.request.url}: HTTP {response.status_code} {response.text[:200]}")
    return response.json()


def _follow(client: httpx.Client, url: str) -> httpx.Response:
    """GET a URL, following one redirect *without* leaking credentials.

    The Actions log endpoints answer 302 with a pre-signed blob URL. That URL
    carries its own authorisation in the query string and rejects the request
    outright if the GitHub ``Authorization`` header comes along with it, so the
    hop has to be made by a client that does not have one.
    """
    response = client.get(url)
    if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as anonymous:
            return anonymous.get(response.headers["location"])
    return response


def _failed_job_logs(client: httpx.Client, slug: str, run_id: int) -> str:
    """Concatenate the logs of every job that failed, newest API first.

    Fetched per job rather than from the run-level archive. The archive is one
    request instead of N, but it is produced asynchronously and comes back as a
    valid, empty, 22-byte zip for a while after a run finishes -- which reads
    as "this run had no failures" rather than as an error. Per-job logs are
    available as soon as the job is, and they arrive already scoped to the jobs
    worth looking at.
    """
    jobs = _json(client.get(f"{API}/repos/{slug}/actions/runs/{run_id}/jobs?per_page=100")).get("jobs", [])
    failed = [j for j in jobs if j.get("conclusion") in ("failure", "timed_out", "cancelled")]
    if not failed:
        raise RuntimeError(
            f"Run {run_id} has no failed jobs "
            f"(conclusions: {', '.join(sorted({str(j.get('conclusion')) for j in jobs})) or 'none'}). "
            "TriSolver triages red runs."
        )

    chunks: list[str] = []
    unavailable: list[str] = []
    for job in failed:
        response = _follow(client, f"{API}/repos/{slug}/actions/jobs/{job['id']}/logs")
        if response.status_code != 200 or not response.text.strip():
            unavailable.append(job["name"])
            continue
        chunks.append(f"=== JOB: {job['name']} ===\n{response.text}")

    if not chunks:
        archived = _archive_logs(client, slug, run_id, {j["name"] for j in failed})
        if archived:
            return archived
        raise RuntimeError(
            f"Run {run_id} failed, but none of its logs are downloadable yet "
            f"({', '.join(unavailable)}). GitHub finishes uploading them a minute or two "
            "after a run ends, and deletes them after 90 days. Try again shortly."
        )
    return "\n".join(chunks)


def _archive_logs(client: httpx.Client, slug: str, run_id: int, failed: set[str]) -> str:
    """Fallback: the run-level zip, one text file per job step."""
    response = _follow(client, f"{API}/repos/{slug}/actions/runs/{run_id}/logs")
    if response.status_code != 200 or not response.content.startswith(b"PK"):
        return ""

    chunks: list[str] = []
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        for name in sorted(archive.namelist()):
            if not name.endswith(".txt"):
                continue
            job_name = name.split("/")[0].split("_", 1)[-1].removesuffix(".txt")
            if failed and not any(job_name.startswith(f.split()[0]) for f in failed):
                continue
            body = archive.read(name).decode("utf-8", errors="replace")
            chunks.append(f"=== JOB: {job_name} ===\n{body}")
    return "\n".join(chunks)


def _pr_number(run: dict) -> int | None:
    prs = run.get("pull_requests") or []
    if prs:
        return prs[0].get("number")
    return None


def _pr_diff(client: httpx.Client, slug: str, pr_number: int) -> str:
    request = client.build_request(
        "GET",
        f"{API}/repos/{slug}/pulls/{pr_number}",
        headers={"Accept": "application/vnd.github.v3.diff"},
    )
    response = client.send(request)
    if response.status_code in (301, 302, 307) and "location" in response.headers:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as anonymous:
            response = anonymous.get(response.headers["location"])
    return response.text if response.status_code == 200 else ""


def _project_docs(client: httpx.Client, slug: str, ref: str, limit: int = 8000) -> str:
    """Best-effort fetch of the conventions Bob and the gates should respect."""
    for path in ("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/runbook.md", "RUNBOOK.md"):
        request = client.build_request(
            "GET",
            f"{API}/repos/{slug}/contents/{path}",
            params={"ref": ref} if ref else None,
            headers={"Accept": "application/vnd.github.raw"},
        )
        response = client.send(request)
        if response.status_code in (301, 302, 307) and "location" in response.headers:
            with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as anonymous:
                response = anonymous.get(response.headers["location"])
        if response.status_code == 200 and response.text.strip():
            return response.text[:limit]
    return ""
