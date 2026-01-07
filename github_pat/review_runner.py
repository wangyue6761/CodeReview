from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.workflow import run_multi_agent_workflow
from core.config import Config
from dao.factory import get_storage
from main import build_repo_map_if_needed, run_syntax_checking
from util import (
    ensure_head_version,
    extract_files_from_diff,
    get_changed_files,
    get_git_diff,
    get_git_info,
    save_observations_to_log,
    validate_repo_path,
)
from util.lite_cpg_utils import prepare_lite_cpg_db


async def run_review_for_pr(
    *,
    repo_path: Path,
    base_branch: str,
    head_branch: str,
    enable_repomap: bool,
    enable_lite_cpg: bool,
    enable_lint: bool,
) -> dict[str, Any]:
    repo_path = validate_repo_path(repo_path)

    config = Config.load_default()
    config.system.workspace_root = repo_path

    pr_diff = get_git_diff(repo_path, base_branch, head_branch)
    if not pr_diff or not pr_diff.strip():
        return {
            "confirmed_issues": [],
            "final_report": "No diff content found.",
            "metadata": {"empty_diff": True},
        }

    if enable_lite_cpg:
        try:
            prepare_lite_cpg_db(
                codereview_root=Path(__file__).resolve().parents[1],
                repo_path=repo_path,
                base_ref=base_branch,
                head_ref=head_branch,
                pr_diff=pr_diff,
                store_blobs=True,
            )
        except Exception:
            pass

    storage = get_storage()
    await storage.connect()

    branch, commit = get_git_info(repo_path, head_branch)
    if enable_repomap:
        asset_key = await build_repo_map_if_needed(repo_path, branch=branch, commit=commit)
        config.system.asset_key = asset_key

    lint_errors: list[dict[str, Any]] = []
    if enable_lint:
        lint_errors = await run_syntax_checking(
            repo_path=repo_path,
            pr_diff=pr_diff,
            base_branch=base_branch,
            head_branch=head_branch,
            config=config,
        )

    try:
        ensure_head_version(repo_path, head_branch)
    except Exception:
        pass

    try:
        changed_files = get_changed_files(repo_path, base_branch, head_branch, config=config)
    except Exception:
        changed_files = extract_files_from_diff(pr_diff, config=config)

    results = await run_multi_agent_workflow(
        diff_context=pr_diff,
        changed_files=changed_files,
        config=config,
        lint_errors=lint_errors,
    )
    try:
        save_observations_to_log(
            results,
            repo_path,
            config,
            base_branch=base_branch,
            head_branch=head_branch,
        )
    except Exception:
        pass
    results["__pr_diff"] = pr_diff
    results["__base_branch"] = base_branch
    results["__head_branch"] = head_branch
    results["__repo_path"] = str(repo_path)
    return results
