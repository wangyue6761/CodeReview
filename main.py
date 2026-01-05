"""AI 代码审查系统主入口。

工作流程：
1. 初始化存储（DAO 层）
2. 构建资产（RepoMap，如需要）
3. 初始化多智能体工作流
4. 执行工作流
5. 显示审查结果
"""


import asyncio
import argparse
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from core.config import Config
from dao.factory import get_storage
from assets.implementations.repo_map import RepoMapBuilder
from agents.workflow import run_multi_agent_workflow
from external_tools.syntax_checker import CheckerFactory, get_config
from external_tools.syntax_checker.config_loader import create_checker_instance
from util.lite_cpg_utils import prepare_lite_cpg_db
from util import (
    generate_asset_key,
    get_git_info,
    load_diff_from_args,
    print_review_results,
    validate_repo_path,
    ensure_head_version,
)
from util.git_utils import extract_files_from_diff, get_changed_files, get_git_diff, get_repo_name




async def run_syntax_checking(
    repo_path: Path,
    pr_diff: str,
    base_branch: str,
    head_branch: str,
    config: Optional[Config] = None,
) -> List[dict]:
    """对变更文件执行语法/静态检查。
    
    Args:
        repo_path: 仓库根路径。
        pr_diff: Git diff 内容。
        base_branch: base分支。
        head_branch: head分支。
    
    Returns:
        检查错误列表，每个错误包含：file, line, message, severity, code。
    """
    try:
        # Get changed files from Git
        try:
            changed_files = get_changed_files(repo_path, base_branch, head_branch, config=config)
        except Exception as e:
            print(f"  ⚠️  Warning: Could not get changed files from Git: {e}")
            # Fallback: try to extract from diff
            changed_files = extract_files_from_diff(pr_diff, config=config)
        
        if not changed_files:
            return []
        
        # Group files by checker
        checker_groups = CheckerFactory.get_checkers_for_files(changed_files)
        
        if not checker_groups:
            return []
        
        # Run all checkers
        all_errors = []
        config = get_config()
        
        for checker_class, files in checker_groups.items():
            try:
                # Create checker instance with configuration (if available)
                checker = create_checker_instance(checker_class, config)
                
                errors = await checker.check(repo_path, files)
                # Convert LintError objects to dictionaries
                all_errors.extend([
                    {
                        "file": error.file,
                        "line": error.line,
                        "message": error.message,
                        "severity": error.severity,
                        "code": error.code
                    }
                    for error in errors
                ])
            except Exception as e:
                # Gracefully handle checker failures
                print(f"  ⚠️  Warning: {checker_class.__name__} failed: {e}")
                continue
        
        return all_errors
    
    except Exception as e:
        # Gracefully handle any errors in syntax checking
        print(f"  ⚠️  Warning: Syntax checking failed: {e}")
        return []


async def build_repo_map_if_needed(
    workspace_root: Path,
    branch: Optional[str] = None,
    commit: Optional[str] = None
) -> str:
    """如需要则构建仓库地图（幂等操作）。
    
    Args:
        workspace_root: 工作区根目录。
        branch: Git 分支名（可选，未提供则从 Git 检测）。
        commit: Git 提交哈希（可选，未提供则从 Git 检测）。
    
    Returns:
        用于存储的资产键。
    """
    try:
        # Try to get Git info if not provided
        if branch is None or commit is None:
            detected_branch, detected_commit = get_git_info(workspace_root)
            branch = branch or detected_branch
            commit = commit or detected_commit
        
        # Generate unique asset key
        asset_key = generate_asset_key(workspace_root, branch, commit)
        
        # Initialize storage
        storage = get_storage()
        await storage.connect()
        
        # Check if repo_map already exists for this specific repo/branch/commit
        exists = await storage.exists("assets", asset_key)
        
        if exists:
            print(f"✅ Repository map already exists in storage (key: {asset_key})")
            return asset_key
        
        # Build the repo map (will save to DAO automatically with the unique key)
        print(f"🔨 Building repository map (key: {asset_key})...")
        builder = RepoMapBuilder()
        repo_map_data = await builder.build(workspace_root, asset_key=asset_key)
        
        print(f"✅ Repository map built and saved ({repo_map_data.get('file_count', 0)} files)")
        return asset_key
    
    except Exception as e:
        print(f"⚠️  Warning: Could not build repo map: {e}")
        # Continue anyway - agent can still work without repo map
        # Return a fallback key
        return generate_asset_key(workspace_root, branch, commit)


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数。
    
    Returns:
        解析后的参数命名空间。
    """
    parser = argparse.ArgumentParser(
        description="AI Code Review Agent - Analyze Git PR diffs using LLM agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
        Examples:
        # Compare feature-x branch with main
        python main.py --repo ./project --base main --head feature-x
        
        # Compare current HEAD with main
        python main.py --repo ./project --base main --head HEAD
                """
    )
    
    parser.add_argument(
        "--repo",
        type=str,
        required=True,
        help="Path to the repository to review (required)"
    )
    
    parser.add_argument(
        "--base",
        type=str,
        required=True,
        help="Target branch for Git diff (e.g., 'main', 'master')"
    )
    
    parser.add_argument(
        "--head",
        type=str,
        required=True,
        help="Source branch or commit for Git diff (e.g., 'feature-x', 'HEAD')"
    )
    
    parser.add_argument(
        "--output",
        type=str,
        default="review_results.json",
        help="Path to save the review results JSON file (default: review_results.json)"
    )
    
    return parser.parse_args()


async def run_review(
    repo_path: Path,
    base_branch: str,
    head_branch: str,
    output_file: Path,
    quiet: bool = False
) -> int:
    """代码审查核心逻辑，可被导入调用。
    
    Args:
        repo_path: 仓库路径
        base_branch: base分支
        head_branch: head分支
        output_file: 输出文件路径
        quiet: 是否静默模式（减少输出）
    
    Returns:
        退出码：0表示成功，1表示失败
    """
    def log(msg: str = ""):
        """条件输出函数"""
        if not quiet:
            print(msg)
    
    log("🚀 AI Code Review Agent")
    log("=" * 80)
    
    # Validate and resolve repository path
    repo_path = validate_repo_path(repo_path)
    log(f"📁 Repository: {repo_path}")
    
    # Load configuration and set workspace root to repo path
    config = Config.load_default()
    config.system.workspace_root = repo_path
    
    log(f"📝 Configuration loaded: LLM Provider = {config.llm.provider}")
    log(f"📁 Workspace root: {config.system.workspace_root}")
    
    # Load diff from Git
    log(f"\n🔀 Getting Git diff: {base_branch}...{head_branch}")
    try:
        pr_diff = get_git_diff(repo_path, base_branch, head_branch)
        if not pr_diff or len(pr_diff.strip()) == 0:
            log(f"⚠️  Warning: Git diff is empty. No changes found between {base_branch} and {head_branch}")
        else:
            log(f"✅ Git diff retrieved ({len(pr_diff)} characters)")
    except Exception as e:
        log(f"❌ Error getting Git diff: {e}")
        return 1
    
    # Get Git info from head branch for asset key generation
    branch, commit = get_git_info(repo_path, head_branch)
    
    if not pr_diff:
        log("❌ Error: No diff content available")
        return 1
    
    log(f"📝 Processing Git diff ({len(pr_diff)} characters)...")
    
    # Ensure repository is on HEAD version (not base version) before review
    try:
        log(f"\n🔀 Ensuring repository is on HEAD version ({head_branch})...")
        ensure_head_version(repo_path, head_branch)
        log(f"✅ Repository is on HEAD version")
    except Exception as e:
        log(f"⚠️  Warning: Could not ensure HEAD version: {e}")
        log(f"   Continuing with current version...")

    # Step 0: Build per-diff Lite-CPG index DB (base/head revisions)
    try:
        log("\n🧠 Building Lite-CPG index (per-diff DB)...")
        db_path = prepare_lite_cpg_db(
            codereview_root=Path(__file__).resolve().parent,
            repo_path=repo_path,
            base_ref=base_branch,
            head_ref=head_branch,
            pr_diff=pr_diff,
            store_blobs=True,
        )
        if os.environ.get("LITE_CPG_INDEX_SKIPPED") == "1":
            log(f"✅ Lite-CPG DB already indexed, skip rebuild: {db_path}")
        else:
            log(f"✅ Lite-CPG DB indexed/refreshed: {db_path}")
    except Exception as e:
        log(f"⚠️  Warning: Lite-CPG indexing failed: {e}")
        log("   Tip: This usually means tree-sitter parser binaries are missing/incompatible.")
        log("   Tip: Ensure `pip install -r requirements.txt` in the same env, and consider installing language provider wheels like `tree-sitter-javascript`.")

    # Step 1: Initialize Storage (DAO layer)
    log("\n💾 Initializing storage backend...")
    storage = get_storage()
    await storage.connect()
    log("✅ Storage initialized")
    
    # Step 2: Build Assets if needed
    log("\n📦 Checking assets...")
    asset_key = await build_repo_map_if_needed(repo_path, branch=branch, commit=commit)
    
    # Store asset_key in config for tools to use
    config.system.asset_key = asset_key
    
    # Step 2.5: Run Pre-Agent Syntax/Lint Checking
    log("\n🔍 Running pre-agent syntax/lint checking...")
    lint_errors = await run_syntax_checking(
        repo_path=repo_path,
        pr_diff=pr_diff,
        base_branch=base_branch,
        head_branch=head_branch,
        config=config,
    )
    
    if lint_errors:
        log(f"  ⚠️  Found {len(lint_errors)} linting error(s):")
        for error in lint_errors[:10]:  # Show first 10
            file_path = error.get("file", "unknown")
            line = error.get("line", 0)
            message = error.get("message", "")
            severity = error.get("severity", "error")
            icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(severity, "•")
            log(f"    {icon} {file_path}:{line} - {message}")
        if len(lint_errors) > 10:
            log(f"    ... and {len(lint_errors) - 10} more")
    else:
        log("  ✅ No linting errors found")
    
    # Step 3 & 4: Initialize and Run Multi-Agent Workflow
    log("\n🤖 Initializing multi-agent workflow...")
    log("  → Workflow will:")
    log("    1. Analyze file intents in parallel")
    log("    2. Manager routes tasks to expert agents")
    log("    3. Expert agents validate risks with concurrency control")
    log("    4. Generate final review report")
    
    # Get changed files list for the workflow
    try:
        changed_files = get_changed_files(repo_path, base_branch, head_branch, config=config)
    except Exception as e:
        log(f"  ⚠️  Warning: Could not get changed files from Git: {e}")
        # Fallback: try to extract from diff
        try:
            changed_files = extract_files_from_diff(pr_diff, config=config)
        except Exception as e2:
            log(f"  ⚠️  Warning: Could not extract changed files from diff: {e2}")
            changed_files = []
    
    if not changed_files:
        log("  ⚠️  Warning: No changed files detected, workflow may not produce results")
    
    # Generate timestamp for this run (used for both log and results files)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Sanitize branch names for filesystem
    base_sanitized = base_branch.replace("/", "_").replace("\\", "_").replace("..", "").replace(" ", "_")
    head_sanitized = head_branch.replace("/", "_").replace("\\", "_").replace("..", "").replace(" ", "_")
    
    try:
        results = await run_multi_agent_workflow(
            diff_context=pr_diff,
            changed_files=changed_files,
            config=config,
            lint_errors=lint_errors
        )
        
        # Print results (pass timestamp so log file uses same timestamp)
        if not quiet:
            print_review_results(
                results, 
                workspace_root=repo_path, 
                config=config,
                base_branch=base_branch,
                head_branch=head_branch,
                timestamp=timestamp
            )
        
        # Generate output directory and filename based on repo_name and model_name
        repo_name = get_repo_name(repo_path)
        repo_name = repo_name.replace("/", "_").replace("\\", "_").replace("..", "")
        
        model_name = config.llm.provider or "unknown"
        model_name = model_name.replace("/", "_").replace("\\", "_")
        
        # Create output directory: log/repo_name/model_name/{base}_2_{head}_{timestamp}/
        output_dir = Path("log") / repo_name / model_name / f"{base_sanitized}_2_{head_sanitized}_{timestamp}"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate filename: review_results_{base}_2_{head}.md (no timestamp in filename)
        output_filename = f"review_results_{base_sanitized}_2_{head_sanitized}.md"
        output_file = output_dir / output_filename
        
        # Get final_report from results
        final_report = results.get("final_report", "")
        
        # Write final_report as markdown file
        with open(output_file, "w", encoding="utf-8") as f:
            if final_report:
                f.write(final_report)
            else:
                f.write("# Code Review Report\n\nNo issues found.\n")
        log(f"\n💾 Results saved to: {output_file}")
        
    except Exception as e:
        log(f"\n❌ Error running agent: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


async def main():
    """代码审查系统主入口（命令行模式）。"""
    args = parse_arguments()
    
    return await run_review(
        repo_path=Path(args.repo),
        base_branch=args.base,
        head_branch=args.head,
        output_file=Path(args.output),
        quiet=False
    )


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
