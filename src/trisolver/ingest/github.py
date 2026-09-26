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

    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True, headers=headers) as client:
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


def _failed_job_logs(client: httpx.Client, slug: str, run_id: int) -> str:
    """Download the run's log archive and keep only the jobs that failed.

    The archive holds one text file per job step. Concatenating only the
    failed jobs, with the ``=== JOB: name ===`` separator the splitter reads,
    keeps the state we send to Jev focused on what actually broke.
    """
    jobs = _json(client.get(f"{API}/repos/{slug}/actions/runs/{run_id}/jobs?per_page=100"))
    failed = {
        job["name"]
        for job in jobs.get("jobs", [])
        if job.get("conclusion") in ("failure", "timed_out", "cancelled")
    }

    response = client.get(f"{API}/repos/{slug}/actions/runs/{run_id}/logs")
    if response.status_code != 200:
        raise RuntimeError(
            f"Could not download logs for run {run_id}: HTTP {response.status_code}. "
            "Logs expire after 90 days and need actions:read."
        )

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

    if not chunks:  # no per-job match; fall back to the whole archive
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            chunks = [
                archive.read(n).decode("utf-8", errors="replace")
                for n in sorted(archive.namelist())
                if n.endswith(".txt")
            ]
    return "\n".join(chunks)


def _pr_number(run: dict) -> int | None:
    prs = run.get("pull_requests") or []
    if prs:
        return prs[0].get("number")
    return None


def _pr_diff(client: httpx.Client, slug: str, pr_number: int) -> str:
    response = client.get(
        f"{API}/repos/{slug}/pulls/{pr_number}",
        headers={"Accept": "application/vnd.github.v3.diff"},
    )
    return response.text if response.status_code == 200 else ""


def _project_docs(client: httpx.Client, slug: str, ref: str, limit: int = 8000) -> str:
    """Best-effort fetch of the conventions Bob and the gates should respect."""
    for path in ("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/runbook.md", "RUNBOOK.md"):
        response = client.get(
            f"{API}/repos/{slug}/contents/{path}",
            params={"ref": ref} if ref else None,
            headers={"Accept": "application/vnd.github.raw"},
        )
        if response.status_code == 200 and response.text.strip():
            return response.text[:limit]
    return ""
