#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sync upstream dataset PR branches into your fork and (re)create PRs.

Default behavior mirrors the original "sentry-greptile" workflow, but supports multiple repos.

Requirements:
  - Local clones for each repo
  - `git` can push to your fork (SSH agent or credential helper configured)
  - `GITHUB_TOKEN` in environment (fine-grained PAT is OK)

Quick start (4 repos besides sentry):
  export GITHUB_TOKEN=...            # required
  export FORK_OWNER=wangyue6761      # optional
  export LOCAL_REPO_ROOT=/path/to/bottest
  python test/sync_prs_and_recreate.py \\
    --repos keycloak-greptile,discourse-greptile,cal.com-greptile,grafana-greptile
"""

import os
import sys
import json
import subprocess
import argparse
from dataclasses import dataclass
from typing import Dict, List, Tuple
import requests

UPSTREAM_OWNER_DEFAULT = "ai-code-review-evaluation"
ALL_DATASET_REPOS = [
    "sentry-greptile",
    "keycloak-greptile",
    "discourse-greptile",
    "cal.com-greptile",
    "grafana-greptile",
]
# 默认跑 5 个仓库里的 4 个（跳过 sentry）
DEFAULT_REPOS = [r for r in ALL_DATASET_REPOS if r != "sentry-greptile"]

# 你的 fork 信息：默认写死为你的账号（如需覆盖，用 --fork-owner）
FORK_OWNER_DEFAULT = "wangyue6761"

# 你本地仓库路径：默认使用你原来的测试集根目录（如需覆盖，用 --local-root）
LOCAL_REPO_ROOT_DEFAULT = "/Users/wangyue/Code/CodeReviewData/bottest"

# 默认只复现 open PR；如需包含 closed，把 STATE 改成 "all"
STATE_DEFAULT = os.environ.get("PR_STATE", "open")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
if not GITHUB_TOKEN:
    print("ERROR: Please set env GITHUB_TOKEN.")
    sys.exit(1)

API = "https://api.github.com"
SESSION = requests.Session()


@dataclass(frozen=True)
class RepoSpec:
    upstream_owner: str
    upstream_repo: str
    fork_owner: str
    fork_repo: str
    local_path: str


def run_git(args: List[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run git command."""
    p = subprocess.run(["git"] + args, cwd=cwd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed\n"
            f"cwd={cwd}\n"
            f"stdout:\n{p.stdout}\n"
            f"stderr:\n{p.stderr}\n"
        )
    return p


def clone_repo_if_missing(*, repo_url: str, local_path: str) -> None:
    """Clone repo if local_path does not exist."""
    if os.path.exists(local_path):
        return
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    parent = os.path.dirname(local_path) or "."
    name = os.path.basename(local_path.rstrip("/"))
    print(f"==> Clone missing repo: {name}")
    p = subprocess.run(["git", "clone", repo_url, local_path], cwd=parent, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"git clone failed: {repo_url}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")


def fork_clone_url(owner: str, repo: str) -> str:
    """Choose clone URL for fork repo (prefer SSH)."""
    protocol = os.environ.get("CLONE_PROTOCOL", "ssh").strip().lower()
    if protocol == "https":
        return f"https://github.com/{owner}/{repo}.git"
    return f"git@github.com:{owner}/{repo}.git"


def clone_fork_repo_if_missing(*, owner: str, repo: str, local_path: str) -> None:
    """Clone fork repo if missing. Tries SSH first, falls back to HTTPS."""
    if os.path.exists(local_path):
        return
    ssh_url = f"git@github.com:{owner}/{repo}.git"
    https_url = f"https://github.com/{owner}/{repo}.git"
    protocol = os.environ.get("CLONE_PROTOCOL", "ssh").strip().lower()
    first, second = (ssh_url, https_url) if protocol != "https" else (https_url, ssh_url)
    try:
        clone_repo_if_missing(repo_url=first, local_path=local_path)
    except Exception as e1:
        print(f"    -> Clone failed via {first} ({e1}). Trying fallback ...")
        clone_repo_if_missing(repo_url=second, local_path=local_path)


def ensure_remote(cwd: str, name: str, url: str, *, update_url: bool = True) -> None:
    """Ensure git remote exists; optionally update its URL."""
    p = run_git(["remote"], cwd=cwd)
    remotes = [r.strip() for r in p.stdout.splitlines() if r.strip()]
    if name not in remotes:
        run_git(["remote", "add", name, url], cwd=cwd)
        return

    # verify url
    p2 = run_git(["remote", "get-url", name], cwd=cwd)
    current = p2.stdout.strip()
    if update_url and current != url:
        run_git(["remote", "set-url", name, url], cwd=cwd)


def gh_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "pr-reproducer-script",
    }


def _gh_get(url: str, *, params: Dict | None = None) -> requests.Response:
    return SESSION.get(url, headers=gh_headers(), params=params, timeout=60)


def _gh_post(url: str, *, payload: Dict) -> requests.Response:
    return SESSION.post(url, headers=gh_headers(), json=payload, timeout=60)


def list_prs(owner: str, repo: str, state: str) -> List[Dict]:
    """List PRs via GitHub REST. Handles pagination."""
    prs: List[Dict] = []
    page = 1
    per_page = 100
    while True:
        url = f"{API}/repos/{owner}/{repo}/pulls"
        resp = _gh_get(
            url,
            params={"state": state, "per_page": per_page, "page": page, "sort": "created", "direction": "asc"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"List PRs failed: {resp.status_code} {resp.text}")
        batch = resp.json()
        if not batch:
            break
        prs.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    # 按 PR 号排序，便于复现顺序一致
    prs.sort(key=lambda x: x.get("number", 0))
    return prs


def create_pr_in_fork(*, fork_owner: str, fork_repo: str, title: str, body: str, base: str, head: str) -> Dict:
    """
    Create PR in fork repo.
    head must be like: "<FORK_OWNER>:<branch>"
    base is branch name in fork repo.
    """
    url = f"{API}/repos/{fork_owner}/{fork_repo}/pulls"
    payload = {"title": title, "body": body, "head": head, "base": base}
    resp = _gh_post(url, payload=payload)
    if resp.status_code == 201:
        return resp.json()
    # 常见错误：PR 已存在 / 分支不存在等
    raise RuntimeError(f"Create PR failed: {resp.status_code} {resp.text}")


def pr_already_exists(*, fork_owner: str, fork_repo: str, base: str, head_branch: str) -> Tuple[bool, str]:
    """
    Check if a PR from head_branch -> base already exists in fork.
    We search both open and closed PRs to avoid duplicates.
    """
    for state in ["open", "closed"]:
        page = 1
        per_page = 100
        while True:
            url = f"{API}/repos/{fork_owner}/{fork_repo}/pulls"
            resp = _gh_get(url, params={"state": state, "per_page": per_page, "page": page})
            if resp.status_code != 200:
                raise RuntimeError(f"Check existing PRs failed: {resp.status_code} {resp.text}")
            batch = resp.json()
            for pr in batch:
                if pr.get("base", {}).get("ref") != base:
                    continue
                if pr.get("head", {}).get("ref") != head_branch:
                    continue
                if pr.get("head", {}).get("repo", {}).get("owner", {}).get("login") != fork_owner:
                    continue
                return True, pr.get("html_url", "")
            if not batch or len(batch) < per_page:
                break
            page += 1
    return False, ""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync upstream PRs and recreate them in your fork.")
    p.add_argument(
        "--repos",
        type=str,
        default=os.environ.get("REPOS", ",".join(DEFAULT_REPOS)),
        help="Comma-separated repo names. Default: 4 repos (skip sentry-greptile).",
    )
    p.add_argument(
        "--upstream-owner",
        type=str,
        default=os.environ.get("UPSTREAM_OWNER", UPSTREAM_OWNER_DEFAULT),
        help="Upstream org/owner (default: ai-code-review-evaluation).",
    )
    p.add_argument(
        "--fork-owner",
        type=str,
        default=FORK_OWNER_DEFAULT,
        help="Your fork owner/login.",
    )
    p.add_argument(
        "--local-root",
        type=str,
        default=LOCAL_REPO_ROOT_DEFAULT,
        help="Local root directory that contains clones named by repo.",
    )
    p.add_argument(
        "--force-origin-url",
        action="store_true",
        help="Force-set git remote 'origin' URL to https://github.com/<fork_owner>/<repo>.git (may override SSH remotes).",
    )
    p.add_argument(
        "--clone-missing",
        action="store_true",
        default=True,
        help="Auto `git clone` fork repos into local root if missing (default: enabled).",
    )
    p.add_argument(
        "--no-clone-missing",
        action="store_false",
        dest="clone_missing",
        help="Disable auto-clone for missing local repos.",
    )
    p.add_argument(
        "--state",
        type=str,
        default=STATE_DEFAULT,
        choices=["open", "closed", "all"],
        help="Which upstream PRs to recreate.",
    )
    return p.parse_args()


def build_repo_specs(args: argparse.Namespace) -> List[RepoSpec]:
    repos = [r.strip() for r in (args.repos or "").split(",") if r.strip()]
    if not repos:
        raise SystemExit("ERROR: --repos is empty.")
    if not args.local_root:
        raise SystemExit("ERROR: --local-root is required.")

    specs: List[RepoSpec] = []
    for repo in repos:
        specs.append(
            RepoSpec(
                upstream_owner=args.upstream_owner,
                upstream_repo=repo,
                fork_owner=args.fork_owner,
                fork_repo=repo,
                local_path=os.path.join(args.local_root, repo),
            )
        )
    return specs


def process_repo(spec: RepoSpec, state: str, *, force_origin_url: bool, clone_missing: bool) -> List[Dict]:
    cwd = spec.local_path

    # 0) Clone missing local repo from fork (so push works).
    if not os.path.exists(cwd):
        if not clone_missing:
            raise RuntimeError(f"Local repo missing: {cwd} (rerun with --clone-missing)")
        clone_fork_repo_if_missing(owner=spec.fork_owner, repo=spec.fork_repo, local_path=cwd)

    # 1) 确保在 git 仓库中
    try:
        run_git(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    except Exception:
        raise RuntimeError(f"{cwd} is not a git repo. Ensure local clone exists.")

    upstream_url = f"https://github.com/{spec.upstream_owner}/{spec.upstream_repo}.git"
    # origin 这里用 https，若你本地用 ssh 也可以自行改成 git@github.com:xxx/xxx.git
    origin_url = f"https://github.com/{spec.fork_owner}/{spec.fork_repo}.git"

    # 2) 设置 remotes
    ensure_remote(cwd, "upstream", upstream_url, update_url=True)
    # Keep existing origin URL by default (users often use SSH remotes for push).
    ensure_remote(cwd, "origin", origin_url, update_url=force_origin_url)

    # 3) fetch upstream
    print(f"==> [{spec.upstream_repo}] Fetch upstream ...")
    run_git(["fetch", "upstream", "--prune"], cwd=cwd)

    # 4) 列出 upstream 的 PR
    print(f"==> List upstream PRs: {spec.upstream_owner}/{spec.upstream_repo} state={state}")
    prs = list_prs(spec.upstream_owner, spec.upstream_repo, state)
    if not prs:
        print("No PRs found.")
        return []

    print(f"Found {len(prs)} PRs.")

    # 5) 为每个 PR：同步 base/head 分支到你的 fork，然后创建 PR
    results: List[Dict] = []
    for pr in prs:
        number = pr["number"]
        title = pr.get("title", f"PR #{number}")
        body = pr.get("body") or ""
        base_ref = pr["base"]["ref"]
        head_ref = pr["head"]["ref"]

        # 头分支可能来自别的 repo（但你这个数据集一般就在同一个 upstream repo）
        head_repo_full = pr["head"]["repo"]["full_name"]  # e.g. ai-code-review-evaluation/sentry-greptile
        head_repo_url = pr["head"]["repo"]["clone_url"]

        print(f"\n=== PR #{number}: {title}")
        print(f"    base: {base_ref}")
        print(f"    head: {head_repo_full}:{head_ref}")

        # 5.1 同步 base 分支：upstream/base_ref -> origin/base_ref
        # 如果 upstream 没有这个分支会失败；那说明 PR 数据/仓库不一致
        print(f"    -> Sync base branch '{base_ref}' to fork ...")
        # 先 fetch 到远程引用，避免检出分支冲突
        run_git(["fetch", "upstream", f"{base_ref}"], cwd=cwd)
        # 使用 update-ref 更新本地分支（即使被检出也可以）
        run_git(["update-ref", f"refs/heads/{base_ref}", f"refs/remotes/upstream/{base_ref}"], cwd=cwd)
        run_git(["push", "--force", "origin", f"refs/heads/{base_ref}:refs/heads/{base_ref}"], cwd=cwd)

        # 5.2 同步 head 分支：从 head repo 抓取 head_ref，推到 origin
        # 为 head repo 临时加 remote（避免 head repo 不同）
        temp_remote = f"prhead-{number}"
        ensure_remote(cwd, temp_remote, head_repo_url, update_url=True)

        print(f"    -> Sync head branch '{head_ref}' to fork ...")
        # 先 fetch 到远程引用，避免检出分支冲突
        run_git(["fetch", temp_remote, f"{head_ref}"], cwd=cwd)
        # 使用 update-ref 更新本地分支（即使被检出也可以）
        run_git(["update-ref", f"refs/heads/{head_ref}", f"refs/remotes/{temp_remote}/{head_ref}"], cwd=cwd)
        run_git(["push", "--force", "origin", f"refs/heads/{head_ref}:refs/heads/{head_ref}"], cwd=cwd)
        
        # 清理临时 remote
        run_git(["remote", "remove", temp_remote], cwd=cwd, check=False)

        # 5.3 在 fork 创建 PR（如果已存在则跳过）
        exists, url = pr_already_exists(
            fork_owner=spec.fork_owner,
            fork_repo=spec.fork_repo,
            base=base_ref,
            head_branch=head_ref,
        )
        if exists:
            print(f"    -> PR already exists in fork: {url}")
            results.append({"upstream_pr": pr["html_url"], "fork_pr": url, "status": "exists"})
            continue

        # head 参数要求 "<owner>:<branch>"
        fork_head = f"{spec.fork_owner}:{head_ref}"
        fork_body = (
            f"Recreated from upstream PR {pr['html_url']}\n\n"
            f"Original title: {title}\n\n"
            f"{body}"
        )

        print("    -> Create PR in fork ...")
        created = create_pr_in_fork(
            fork_owner=spec.fork_owner,
            fork_repo=spec.fork_repo,
            title=title,
            body=fork_body,
            base=base_ref,
            head=fork_head,
        )
        fork_url = created.get("html_url", "")
        print(f"    -> Created: {fork_url}")
        results.append({"upstream_pr": pr["html_url"], "fork_pr": fork_url, "status": "created"})

    return results


def main():
    args = parse_args()
    specs = build_repo_specs(args)

    all_results: Dict[str, List[Dict]] = {}
    for spec in specs:
        try:
            all_results[spec.upstream_repo] = process_repo(
                spec,
                state=args.state,
                force_origin_url=args.force_origin_url,
                clone_missing=args.clone_missing,
            )
        except Exception as e:
            print(f"ERROR: repo={spec.upstream_repo} failed: {e}")
            all_results[spec.upstream_repo] = [{"status": "error", "error": str(e)}]

    print("\n==== DONE ====")
    print(json.dumps(all_results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
