"""Argument validation and diff loading utilities."""

import sys
from pathlib import Path
from typing import Optional, Tuple

from util.git_utils import get_git_diff
from util.pr_utils import load_diff_from_file


def validate_repo_path(repo_path: Path) -> Path:
    """Validate and resolve repository path.
    
    Args:
        repo_path: Path to validate.
    
    Returns:
        Resolved repository path.
    
    Raises:
        SystemExit: If the path is invalid.
    """
    repo_path = Path(repo_path).resolve()
    
    if not repo_path.exists():
        print(f"❌ Repository path does not exist: {repo_path}")
        sys.exit(1)
    
    if not repo_path.is_dir():
        print(f"❌ Repository path must be a directory: {repo_path}")
        sys.exit(1)
    
    return repo_path


def load_diff_from_args(
    args,
    repo_path: Path
) -> Tuple[str, Optional[str], Optional[str]]:
    """Load diff content based on command line arguments.
    
    This function handles both file-based and Git-based diff loading modes.
    It validates arguments and provides helpful error messages.
    
    Args:
        args: Parsed command line arguments (argparse.Namespace).
        repo_path: Path to the repository.
    
    Returns:
        A tuple of (diff_content, branch, commit).
        - diff_content: The diff content as a string.
        - branch: Git branch name (if applicable), None otherwise.
        - commit: Git commit hash (if applicable), None otherwise.
    
    Raises:
        SystemExit: If arguments are invalid or diff cannot be loaded.
    """
    pr_diff = None
    branch = None
    commit = None
    
    # Check if both --diff and --base are provided (warn that --diff takes priority)
    if args.diff and args.base:
        print(f"⚠️  Warning: Both --diff and --base provided. Using --diff (file mode) and ignoring --base.")
    
    if args.diff:
        # Mode B: Local diff file (takes priority)
        diff_path = Path(args.diff)
        if not diff_path.is_absolute():
            # If relative, try relative to repo_path first, then current directory
            repo_relative = repo_path / diff_path
            if repo_relative.exists():
                diff_path = repo_relative
            else:
                diff_path = diff_path.resolve()
        
        print(f"\n📂 Loading diff from file: {diff_path}")
        try:
            pr_diff = load_diff_from_file(diff_path)
            print(f"✅ Diff loaded ({len(pr_diff)} characters)")
        except Exception as e:
            print(f"❌ Error loading diff file: {e}")
            sys.exit(1)
        
        # Try to get current Git info for asset key generation
        from util.git_utils import get_git_info
        branch, commit = get_git_info(repo_path)
    
    elif args.base:
        # Mode A: Git branch diff
        print(f"\n🔀 Getting Git diff: {args.base}...{args.head}")
        try:
            pr_diff = get_git_diff(repo_path, args.base, args.head)
            if not pr_diff or len(pr_diff.strip()) == 0:
                print(f"⚠️  Warning: Git diff is empty. No changes found between {args.base} and {args.head}")
            else:
                print(f"✅ Git diff retrieved ({len(pr_diff)} characters)")
        except Exception as e:
            print(f"❌ Error getting Git diff: {e}")
            sys.exit(1)
        
        # Get Git info from head branch for asset key generation
        from util.git_utils import get_git_info
        branch, commit = get_git_info(repo_path, args.head)
    
    else:
        # Neither --diff nor --base provided
        print("❌ Error: Must provide either --base (for Git mode) or --diff (for file mode)")
        print("   Examples:")
        print("     python main.py --repo ./project --base main --head feature-x")
        print("     python main.py --repo ./project --diff ./changes.diff")
        sys.exit(1)
    
    if not pr_diff:
        print("❌ Error: No diff content available")
        sys.exit(1)
    
    print(f"📝 Processing Git diff ({len(pr_diff)} characters)...")
    
    return (pr_diff, branch, commit)
