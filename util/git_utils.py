"""Git repository utilities for branch, commit, and diff operations."""

import hashlib
import subprocess
from pathlib import Path
from typing import Optional, Tuple


def get_git_info(repo_path: Path, ref: str = "HEAD") -> Tuple[Optional[str], Optional[str]]:
    """Get Git branch and commit hash for a repository.
    
    Args:
        repo_path: Path to the Git repository.
        ref: Git reference (branch, tag, or commit). Default: "HEAD".
    
    Returns:
        A tuple of (branch_name, commit_hash). Returns (None, None) if not a Git repo or error.
    """
    repo_path = Path(repo_path).resolve()
    
    try:
        # Get current branch name
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", ref],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        branch = branch_result.stdout.strip()
        
        # Get commit hash
        commit_result = subprocess.run(
            ["git", "rev-parse", ref],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        commit_hash = commit_result.stdout.strip()[:12]  # Use short hash (12 chars)
        
        return (branch, commit_hash)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return (None, None)


def get_git_diff(repo_path: Path, base: str, head: str = "HEAD") -> str:
    """Get Git diff using triple-dot syntax.
    
    This function executes `git diff {base}...{head}` in the specified repository
    to get all changes that occurred after the branches diverged.
    
    Args:
        repo_path: Path to the Git repository.
        base: Target branch (e.g., "main", "master").
        head: Source branch or commit (default: "HEAD").
    
    Returns:
        The Git diff content as a string.
    
    Raises:
        ValueError: If repo_path is not a valid Git repository.
        subprocess.CalledProcessError: If git diff command fails.
    """
    repo_path = Path(repo_path).resolve()
    
    if not repo_path.exists():
        raise ValueError(f"Repository path does not exist: {repo_path}")
    
    if not repo_path.is_dir():
        raise ValueError(f"Repository path must be a directory: {repo_path}")
    
    # Check if it's a Git repository
    # Try to find .git directory (could be a file for worktrees or submodules)
    git_dir = repo_path / ".git"
    if not git_dir.exists():
        # Try using git rev-parse to check if it's a git repo
        try:
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=repo_path,
                capture_output=True,
                check=True
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise ValueError(f"Not a Git repository: {repo_path}")
    
    try:
        # Execute git diff with triple-dot syntax
        # Triple-dot (base...head) shows changes in head that are not in base
        result = subprocess.run(
            ["git", "diff", f"{base}...{head}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else "Unknown git error"
        # Provide more helpful error messages
        if "fatal:" in error_msg.lower() or "error:" in error_msg.lower():
            raise ValueError(f"Git diff failed: {error_msg}")
        else:
            raise ValueError(f"Git diff failed: {error_msg}")
    except FileNotFoundError:
        raise ValueError("Git is not installed or not in PATH")


def generate_asset_key(repo_path: Path, branch: Optional[str] = None, commit: Optional[str] = None) -> str:
    """Generate a unique asset key based on repository path, branch, and commit.
    
    The key format: repo_map_{repo_name}_{branch}_{commit_hash}
    If branch or commit is None, uses "unknown" as placeholder.
    
    Args:
        repo_path: Path to the repository.
        branch: Git branch name (optional).
        commit: Git commit hash (optional).
    
    Returns:
        A unique string key for the asset.
    """
    repo_path = Path(repo_path).resolve()
    repo_name = repo_path.name or "unknown_repo"
    # Sanitize repo name for use in file paths
    repo_name = repo_name.replace("/", "_").replace("\\", "_").replace("..", "")
    
    branch = branch or "unknown_branch"
    commit = commit or "unknown_commit"
    
    # Sanitize branch and commit
    branch = branch.replace("/", "_").replace("\\", "_")
    commit = commit.replace("/", "_").replace("\\", "_")
    
    # Generate key
    key = f"repo_map_{repo_name}_{branch}_{commit}"
    
    # Ensure key is not too long (some filesystems have limits)
    if len(key) > 200:
        # Use hash for very long keys
        key_hash = hashlib.md5(key.encode()).hexdigest()[:12]
        key = f"repo_map_{repo_name}_{key_hash}"
    
    return key


def get_repo_name(workspace_root: Path) -> str:
    """Get a recognizable repository name from workspace root.
    
    Args:
        workspace_root: Path to the workspace root.
    
    Returns:
        A recognizable repository name. If workspace_root is ".", returns a descriptive name.
    """
    workspace_root = Path(workspace_root).resolve()
    repo_name = workspace_root.name
    
    # Handle edge cases where name might be empty or "."
    if repo_name in [".", ""] or len(repo_name) == 0:
        # Try to use the parent directory name or a default
        parent_name = workspace_root.parent.name
        if parent_name and parent_name not in [".", ""]:
            return f"{parent_name}_workspace"
        return "current_workspace"
    
    return repo_name
