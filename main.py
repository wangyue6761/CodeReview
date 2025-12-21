"""Main entry point for the AI Code Review Agent.

This script demonstrates the complete workflow:
1. Initialize Storage (DAO layer)
2. Build Assets (RepoMap) if needed
3. Initialize Autonomous ReAct Agent
4. Run the agent workflow
5. Display review results
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from core.config import Config
from dao.factory import get_storage
from assets.implementations.repo_map import RepoMapBuilder
from agents.bot import run_react_agent
from external_tools.syntax_checker import CheckerFactory, get_config
from external_tools.syntax_checker.config_loader import create_checker_instance
from util import (
    generate_asset_key,
    get_git_info,
    load_diff_from_args,
    print_review_results,
    validate_repo_path,
)
from util.git_utils import extract_files_from_diff, get_changed_files

async def run_syntax_checking(
    repo_path: Path,
    pr_diff: str,
    args: argparse.Namespace
) -> List[dict]:
    """Run syntax/lint checking on changed files.
    
    This function determines which files have changed (either from Git or from diff),
    then runs appropriate syntax checkers on those files.
    
    Args:
        repo_path: Root path of the repository.
        pr_diff: The Git diff content.
        args: Parsed command line arguments.
    
    Returns:
        List of linting errors as dictionaries. Each error has keys:
        file, line, message, severity, code.
    """
    try:
        # Determine changed files
        if args.diff:
            # Mode: --diff-file - extract files from diff content
            changed_files = extract_files_from_diff(pr_diff)
        elif args.base:
            # Mode: Git branches - use git command
            try:
                changed_files = get_changed_files(repo_path, args.base, args.head)
            except Exception as e:
                print(f"  ⚠️  Warning: Could not get changed files from Git: {e}")
                # Fallback: try to extract from diff
                changed_files = extract_files_from_diff(pr_diff)
        else:
            # Should not happen (validated earlier), but handle gracefully
            changed_files = []
        
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
    """Build repository map if it doesn't exist in storage.
    
    This function checks if the repo_map asset exists in the DAO layer for the
    specific repository, branch, and commit combination. If it doesn't exist, it
    builds and saves it. The build process is idempotent.
    
    Args:
        workspace_root: Root directory of the workspace.
        branch: Git branch name (optional). If None, will try to detect from Git.
        commit: Git commit hash (optional). If None, will try to detect from Git.
    
    Returns:
        The asset key used for storage.
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
    """Parse command line arguments.
    
    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="AI Code Review Agent - Analyze Git PR diffs using LLM agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
        Examples:
        # Git branch mode: compare feature-x branch with main
        python main.py --repo ./project --base main --head feature-x
        
        # Git branch mode: compare current HEAD with main
        python main.py --repo ./project --base main
        
        # Local diff file mode
        python main.py --repo ./project --diff ./changes.diff
                """
    )
    
    parser.add_argument(
        "--repo",
        type=str,
        required=True,
        help="Path to the repository to review (required)"
    )
    
    # Diff source: either Git branches or local file
    parser.add_argument(
        "--base",
        type=str,
        default=None,
        help="Target branch for Git diff mode (e.g., 'main', 'master')"
    )
    
    parser.add_argument(
        "--diff",
        type=str,
        default=None,
        help="Path to a local .diff file (alternative to --base/--head). Takes priority if both are provided."
    )
    
    parser.add_argument(
        "--head",
        type=str,
        default="HEAD",
        help="Source branch or commit for Git diff mode (default: HEAD). Only used with --base."
    )
    
    parser.add_argument(
        "--output",
        type=str,
        default="review_results.json",
        help="Path to save the review results JSON file (default: review_results.json)"
    )
    
    return parser.parse_args()


async def main():
    """Main entry point for the code review agent."""
    args = parse_arguments()
    
    print("🚀 AI Code Review Agent - MVP")
    print("=" * 80)
    
    # Validate and resolve repository path
    repo_path = validate_repo_path(Path(args.repo))
    print(f"📁 Repository: {repo_path}")
    
    # Load configuration and set workspace root to repo path
    config = Config.load_default()
    config.system.workspace_root = repo_path
    
    print(f"📝 Configuration loaded: LLM Provider = {config.llm.provider}")
    print(f"📁 Workspace root: {config.system.workspace_root}")
    
    # Load diff: either from Git or from file (includes argument validation)
    pr_diff, branch, commit = load_diff_from_args(args, repo_path)
    
    # Step 1: Initialize Storage (DAO layer)
    print("\n💾 Initializing storage backend...")
    storage = get_storage()
    await storage.connect()
    print("✅ Storage initialized")
    
    # Step 2: Build Assets if needed
    print("\n📦 Checking assets...")
    # Git info already retrieved in load_diff_from_args
    asset_key = await build_repo_map_if_needed(repo_path, branch=branch, commit=commit)
    
    # Store asset_key in config for tools to use
    config.system.asset_key = asset_key
    
    # Step 2.5: Run Pre-Agent Syntax/Lint Checking
    print("\n🔍 Running pre-agent syntax/lint checking...")
    lint_errors = await run_syntax_checking(
        repo_path=repo_path,
        pr_diff=pr_diff,
        args=args
    )
    
    if lint_errors:
        print(f"  ⚠️  Found {len(lint_errors)} linting error(s):")
        for error in lint_errors[:10]:  # Show first 10
            file_path = error.get("file", "unknown")
            line = error.get("line", 0)
            message = error.get("message", "")
            severity = error.get("severity", "error")
            icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(severity, "•")
            print(f"    {icon} {file_path}:{line} - {message}")
        if len(lint_errors) > 10:
            print(f"    ... and {len(lint_errors) - 10} more")
    else:
        print("  ✅ No linting errors found")
    
    # Step 3 & 4: Initialize and Run Autonomous ReAct Agent
    print("\n🤖 Initializing autonomous ReAct agent...")
    print("  → Agent will autonomously:")
    
    try:
        results = await run_react_agent(
            pr_diff=pr_diff,
            config=config,
            lint_errors=lint_errors
        )
        
        # Print results
        print_review_results(results, workspace_root=repo_path, config=config)
        
        # Save results to file
        output_file = Path(args.output)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Results saved to: {output_file}")
        
    except Exception as e:
        print(f"\n❌ Error running agent: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
