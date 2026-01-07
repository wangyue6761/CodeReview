from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response

from github_pat.db import JobStore
from github_pat.github_api import GitHubClient
from github_pat.git_cache import RepoCache
from github_pat.settings import Settings
from github_pat.webhook import verify_github_signature
from github_pat.worker import JobWorker, WorkerDeps


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.load()
    if not settings.github_token:
        raise RuntimeError("Missing env: GITHUB_TOKEN")
    if not settings.github_webhook_secret and not settings.allow_unsigned_webhooks:
        raise RuntimeError("Missing env: GITHUB_WEBHOOK_SECRET (or set ALLOW_UNSIGNED_WEBHOOKS=1 for dev)")
    if not settings.allowed_repos:
        raise RuntimeError("Missing env: ALLOWED_REPOS (comma-separated owner/repo)")

    store = JobStore(settings.db_path)
    store.init()

    github = GitHubClient(token=settings.github_token, api_base_url=settings.github_api_base_url)
    repo_cache = RepoCache(mirror_root=settings.mirror_root, work_root=settings.work_root, lock_root=settings.lock_root)
    queue: asyncio.Queue[int] = asyncio.Queue()

    worker = JobWorker(WorkerDeps(settings=settings, store=store, github=github, repo_cache=repo_cache), queue)
    await worker.start()

    app.state.settings = settings
    app.state.store = store
    app.state.queue = queue
    app.state.worker = worker

    try:
        yield
    finally:
        await worker.stop()
        await github.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True}


@app.post("/github/webhook")
async def github_webhook(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    store: JobStore = request.app.state.store
    queue: asyncio.Queue[int] = request.app.state.queue

    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not settings.allow_unsigned_webhooks:
        if not verify_github_signature(secret=settings.github_webhook_secret, body=body, signature_header=signature):
            return Response(status_code=401, content="invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event != "issue_comment":
        return Response(status_code=200, content="ignored")

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return Response(status_code=400, content="invalid json")

    if payload.get("action") != "created":
        return Response(status_code=200, content="ignored")

    issue = payload.get("issue") or {}
    if "pull_request" not in issue:
        return Response(status_code=200, content="ignored")

    comment = payload.get("comment") or {}
    comment_body = str(comment.get("body", ""))
    trigger = (settings.bot_trigger or "").strip()
    if not trigger or trigger.lower() not in comment_body.lower():
        return Response(status_code=200, content="ignored")

    repo = payload.get("repository") or {}
    repo_full_name = str(repo.get("full_name", "")).strip()
    if not repo_full_name:
        return Response(status_code=400, content="missing repository.full_name")
    if settings.allowed_repos and repo_full_name not in settings.allowed_repos:
        return Response(status_code=200, content="repo not allowed")

    pr_number = int(issue.get("number"))
    pr_url = str(issue.get("pull_request", {}).get("url", "")).strip()
    if not pr_url:
        return Response(status_code=400, content="missing issue.pull_request.url")

    comment_id = int(comment.get("id"))
    sender = str((payload.get("sender") or {}).get("login", "")).strip()

    job_id = store.enqueue_job(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        pr_url=pr_url,
        comment_id=comment_id,
        sender=sender,
        cooldown_seconds=settings.cooldown_seconds,
    )
    if job_id is None:
        return Response(status_code=200, content="deduped")

    await queue.put(job_id)
    return Response(status_code=200, content="queued")
