"""Git 仓库工具，用于分支、提交和 diff 操作。"""

import hashlib
import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from core.config import Config
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

_DEFAULT_EXCLUDE_GLOBS: List[str] = [
    # Dependency / lock files
    "**/package-lock.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/bun.lockb",
    "**/Gemfile.lock",
    "**/Cargo.lock",
    "**/go.sum",
    "**/go.mod",
    "**/poetry.lock",
    "**/Pipfile.lock",
    "**/*.lock",
    # Build output / generated
    "**/dist/**",
    "**/build/**",
    "**/out/**",
    "**/.next/**",
    "**/.nuxt/**",
    "**/.svelte-kit/**",
    "**/.cache/**",
    "**/coverage/**",
    "**/node_modules/**",
    "**/generated/**",
    "**/@generated/**",
    "**/__generated__/**",
    "**/__generated/**",
    "**/_generated/**",
    "**/gen/**",
    "**/@gen/**",
    "**/__gen__/**",
    "**/__gen/**",
    "**/_gen/**",
    # Logs
    "**/*.log",
    # Binary / archives
    "**/*.exe",
    "**/*.dll",
    "**/*.so",
    "**/*.dylib",
    "**/*.class",
    "**/*.o",
    "**/*.a",
    "**/*.wasm",
    "**/*.jar",
    "**/*.war",
    "**/*.zip",
    "**/*.tar",
    "**/*.gz",
    "**/*.bz2",
    "**/*.xz",
    "**/*.7z",
    "**/*.rar",
    # Media / documents / fonts
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.bmp",
    "**/*.tiff",
    "**/*.webm",
    "**/*.svg",
    "**/*.pdf",
    "**/*.doc",
    "**/*.docx",
    "**/*.xls",
    "**/*.xlsx",
    "**/*.ppt",
    "**/*.pptx",
    "**/*.ttf",
    "**/*.otf",
    "**/*.woff",
    "**/*.woff2",
]


def _normalize_posix_path(p: str) -> str:
    s = (p or "").strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    while s.startswith("/"):
        s = s[1:]
    return s


def _path_matches_any(path: str, patterns: List[str]) -> bool:
    if not patterns:
        return False
    posix_path = _normalize_posix_path(path)
    pp = PurePosixPath(posix_path)
    for pat in patterns:
        pat = (pat or "").strip()
        if not pat:
            continue
        try:
            if pp.match(pat):
                return True
        except Exception:
            # If a pattern is malformed, ignore it (avoid breaking reviews).
            continue
    return False


def filter_changed_files(files: List[str], config: Optional[Config] = None) -> List[str]:
    """Filter low-signal file paths (locks, generated, binaries, etc.)."""
    if not files:
        return []

    enabled = True
    include_globs: List[str] = []
    extra_excludes: List[str] = []

    try:
        if config and getattr(config, "system", None):
            enabled = bool(getattr(config.system, "path_filter_enabled", True))
            include_globs = list(getattr(config.system, "path_filter_include_globs", []) or [])
            extra_excludes = list(getattr(config.system, "path_filter_exclude_globs", []) or [])
    except Exception:
        enabled = True

    if not enabled:
        return [f for f in files if (f or "").strip()]

    exclude_globs = [*_DEFAULT_EXCLUDE_GLOBS, *extra_excludes]
    kept: List[str] = []
    for f in files:
        f = (f or "").strip()
        if not f:
            continue
        if _path_matches_any(f, include_globs):
            kept.append(f)
            continue
        if _path_matches_any(f, exclude_globs):
            continue
        kept.append(f)
    # Keep deterministic ordering.
    return sorted(set(kept))


def get_git_info(repo_path: Path, ref: str = "HEAD") -> Tuple[Optional[str], Optional[str]]:
    """获取仓库的 Git 分支和提交哈希。
    
    Returns:
        (branch_name, commit_hash) 元组。如果不是 Git 仓库或出错，返回 (None, None)。
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


def get_changed_files(repo_path: Path, base: str, head: str = "HEAD", config: Optional[Config] = None) -> List[str]:
    """获取两个 Git 引用之间变更的文件列表。
    
    此函数执行 `git diff --name-only {base}...{head}` 以获取两个引用之间变更的文件列表。
    
    Returns:
        相对于仓库根目录的文件路径列表。如果没有变更或不是 Git 仓库，返回空列表。
    
    Raises:
        ValueError: repo_path 不是有效的 Git 仓库。
    """
    repo_path = Path(repo_path).resolve()
    
    if not repo_path.exists():
        raise ValueError(f"Repository path does not exist: {repo_path}")
    
    if not repo_path.is_dir():
        raise ValueError(f"Repository path must be a directory: {repo_path}")
    
    # Check if it's a Git repository
    git_dir = repo_path / ".git"
    if not git_dir.exists():
        try:
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=repo_path,
                capture_output=True,
                check=True
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise ValueError(f"Not a Git repository: {repo_path}")
    
    # Pre-validate branches before attempting diff to provide better error messages
    # Use _check_local_ref_exists because git diff requires local branches
    suggestions = []
    base_exists = _check_local_ref_exists(repo_path, base)
    head_exists = _check_local_ref_exists(repo_path, head)
    
    if not base_exists or not head_exists:
        # Check remote for missing branches and auto-fetch if found
        if not base_exists:
            remote_base = _check_remote_ref(repo_path, base)
            if remote_base:
                # Auto-fetch the branch from remote
                print(f"  🔄 Auto-fetching branch '{base}' from remote '{remote_base}'...")
                if _fetch_branch_from_remote(repo_path, remote_base, base):
                    print(f"  ✅ Successfully fetched branch '{base}'")
                    # Re-check if branch now exists locally
                    base_exists = _check_local_ref_exists(repo_path, base)
                else:
                    suggestions.append(f"  - Base branch '{base}' not found locally.")
                    suggestions.append(f"    ✅ Found in remote '{remote_base}', but auto-fetch failed.")
                    suggestions.append(f"    Please manually run: git fetch {remote_base} {base}:{base}")
            else:
                suggestions.append(f"  - Base branch '{base}' not found locally.")
                suggestions.append(f"    ❌ Not found in any remote. Please check the branch name.")
        
        if not head_exists:
            remote_head = _check_remote_ref(repo_path, head)
            if remote_head:
                # Auto-fetch the branch from remote
                print(f"  🔄 Auto-fetching branch '{head}' from remote '{remote_head}'...")
                if _fetch_branch_from_remote(repo_path, remote_head, head):
                    print(f"  ✅ Successfully fetched branch '{head}'")
                    # Re-check if branch now exists locally
                    head_exists = _check_local_ref_exists(repo_path, head)
                else:
                    suggestions.append(f"  - Head branch '{head}' not found locally.")
                    suggestions.append(f"    ✅ Found in remote '{remote_head}', but auto-fetch failed.")
                    suggestions.append(f"    Please manually run: git fetch {remote_head} {head}:{head}")
            else:
                suggestions.append(f"  - Head branch '{head}' not found locally.")
                suggestions.append(f"    ❌ Not found in any remote. Please check the branch name.")
        
        # If branches still don't exist after auto-fetch, raise error with suggestions
        if not base_exists or not head_exists:
            if suggestions:
                error_msg = "One or more branches not found.\n\n💡 Suggestions:\n" + "\n".join(suggestions)
                raise ValueError(f"Git diff failed: {error_msg}")
    
    try:
        # Execute git diff --name-only with triple-dot syntax
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...{head}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        # Filter out empty lines and return list of file paths
        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        return filter_changed_files(files, config)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else "Unknown git error"
        if "fatal:" in error_msg.lower() or "error:" in error_msg.lower():
            # Additional check if pre-validation didn't catch it
            if not suggestions:
                suggestions = []
                
                # Check if base branch exists
                if not base_exists:
                    suggestions.append(f"  - Base branch '{base}' not found locally.")
                    remote_base = _check_remote_ref(repo_path, base)
                    if remote_base:
                        suggestions.append(f"    ✅ Found in remote '{remote_base}'. To use it:")
                        suggestions.append(f"       cd {repo_path}")
                        suggestions.append(f"       git fetch {remote_base} {base}:{base}")
                    else:
                        suggestions.append(f"    ❌ Not found in any remote. Please check the branch name.")
                
                # Check if head branch exists
                if not head_exists:
                    suggestions.append(f"  - Head branch '{head}' not found locally.")
                    remote_head = _check_remote_ref(repo_path, head)
                    if remote_head:
                        suggestions.append(f"    ✅ Found in remote '{remote_head}'. To use it:")
                        suggestions.append(f"       cd {repo_path}")
                        suggestions.append(f"       git fetch {remote_head} {head}:{head}")
                    else:
                        suggestions.append(f"    ❌ Not found in any remote. Please check the branch name.")
            
            if suggestions:
                error_msg = f"{error_msg}\n\n💡 Suggestions:\n" + "\n".join(suggestions)
            
            raise ValueError(f"Git diff failed: {error_msg}")
        else:
            raise ValueError(f"Git diff failed: {error_msg}")
    except FileNotFoundError:
        raise ValueError("Git is not installed or not in PATH")


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
    
    # Pre-validate branches before attempting diff to provide better error messages
    # Use _check_local_ref_exists because git diff requires local branches
    suggestions = []
    base_exists = _check_local_ref_exists(repo_path, base)
    head_exists = _check_local_ref_exists(repo_path, head)
    
    if not base_exists or not head_exists:
        # Check remote for missing branches and auto-fetch if found
        if not base_exists:
            remote_base = _check_remote_ref(repo_path, base)
            if remote_base:
                # Auto-fetch the branch from remote
                print(f"  🔄 Auto-fetching branch '{base}' from remote '{remote_base}'...")
                if _fetch_branch_from_remote(repo_path, remote_base, base):
                    print(f"  ✅ Successfully fetched branch '{base}'")
                    # Re-check if branch now exists locally
                    base_exists = _check_local_ref_exists(repo_path, base)
                else:
                    suggestions.append(f"  - Base branch '{base}' not found locally.")
                    suggestions.append(f"    ✅ Found in remote '{remote_base}', but auto-fetch failed.")
                    suggestions.append(f"    Please manually run: git fetch {remote_base} {base}:{base}")
            else:
                suggestions.append(f"  - Base branch '{base}' not found locally.")
                suggestions.append(f"    ❌ Not found in any remote. Please check the branch name.")
        
        if not head_exists:
            remote_head = _check_remote_ref(repo_path, head)
            if remote_head:
                # Auto-fetch the branch from remote
                print(f"  🔄 Auto-fetching branch '{head}' from remote '{remote_head}'...")
                if _fetch_branch_from_remote(repo_path, remote_head, head):
                    print(f"  ✅ Successfully fetched branch '{head}'")
                    # Re-check if branch now exists locally
                    head_exists = _check_local_ref_exists(repo_path, head)
                else:
                    suggestions.append(f"  - Head branch '{head}' not found locally.")
                    suggestions.append(f"    ✅ Found in remote '{remote_head}', but auto-fetch failed.")
                    suggestions.append(f"    Please manually run: git fetch {remote_head} {head}:{head}")
            else:
                suggestions.append(f"  - Head branch '{head}' not found locally.")
                suggestions.append(f"    ❌ Not found in any remote. Please check the branch name.")
        
        # If branches still don't exist after auto-fetch, raise error with suggestions
        if not base_exists or not head_exists:
            if suggestions:
                error_msg = "One or more branches not found.\n\n💡 Suggestions:\n" + "\n".join(suggestions)
                raise ValueError(f"Git diff failed: {error_msg}")
    
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
            # Additional check if pre-validation didn't catch it
            if not suggestions:
                suggestions = []
                
                # Check if base branch exists
                if not base_exists:
                    suggestions.append(f"  - Base branch '{base}' not found locally.")
                    remote_base = _check_remote_ref(repo_path, base)
                    if remote_base:
                        suggestions.append(f"    ✅ Found in remote '{remote_base}'. To use it:")
                        suggestions.append(f"       cd {repo_path}")
                        suggestions.append(f"       git fetch {remote_base} {base}:{base}")
                    else:
                        suggestions.append(f"    ❌ Not found in any remote. Please check the branch name.")
                
                # Check if head branch exists
                if not head_exists:
                    suggestions.append(f"  - Head branch '{head}' not found locally.")
                    remote_head = _check_remote_ref(repo_path, head)
                    if remote_head:
                        suggestions.append(f"    ✅ Found in remote '{remote_head}'. To use it:")
                        suggestions.append(f"       cd {repo_path}")
                        suggestions.append(f"       git fetch {remote_head} {head}:{head}")
                    else:
                        suggestions.append(f"    ❌ Not found in any remote. Please check the branch name.")
            
            if suggestions:
                error_msg = f"{error_msg}\n\n💡 Suggestions:\n" + "\n".join(suggestions)
            
            raise ValueError(f"Git diff failed: {error_msg}")
        else:
            raise ValueError(f"Git diff failed: {error_msg}")
    except FileNotFoundError:
        raise ValueError("Git is not installed or not in PATH")


def _check_local_ref_exists(repo_path: Path, ref: str) -> bool:
    """Check if a Git reference exists locally (not in remote-tracking branches).
    
    This function only checks for local references:
    - Local branches (refs/heads/)
    - Local tags (refs/tags/)
    - Commit hashes (if they resolve to a commit and are not remote-tracking branches)
    - Current branch name
    
    It does NOT check remote-tracking branches (origin/branch_name) because
    git diff operations require local branches, not remote-tracking branches.
    
    Args:
        repo_path: Path to the Git repository.
        ref: Git reference (branch, tag, or commit).
    
    Returns:
        True if reference exists locally, False otherwise.
    """
    # First, check if it's a local branch (refs/heads/)
    try:
        branch_ref = f"refs/heads/{ref}"
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", branch_ref],
            cwd=repo_path,
            capture_output=True,
            check=True
        )
        return True
    except subprocess.CalledProcessError:
        pass
    
    # Check if it's a local tag (refs/tags/)
    try:
        tag_ref = f"refs/tags/{ref}"
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", tag_ref],
            cwd=repo_path,
            capture_output=True,
            check=True
        )
        return True
    except subprocess.CalledProcessError:
        pass
    
    # Check if it's the current branch name
    try:
        current_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        if current_branch == ref:
            return True
    except subprocess.CalledProcessError:
        pass
    
    # Check if it resolves to a commit, but exclude remote-tracking branches
    # We need to ensure it's not a remote-tracking branch
    try:
        # Check all remotes to see if this ref exists as a remote-tracking branch
        remote_result = subprocess.run(
            ["git", "remote"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        remotes = [r.strip() for r in remote_result.stdout.strip().split("\n") if r.strip()]
        
        # If it exists as a remote-tracking branch, it's not a local branch
        for remote in remotes:
            try:
                remote_ref = f"refs/remotes/{remote}/{ref}"
                subprocess.run(
                    ["git", "rev-parse", "--verify", "--quiet", remote_ref],
                    cwd=repo_path,
                    capture_output=True,
                    check=True
                )
                # Found as remote-tracking branch, so it's not local
                return False
            except subprocess.CalledProcessError:
                continue
        
        # Not found as remote-tracking branch, check if it resolves as a commit
        # This handles commit hashes and other valid refs
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", ref],
                cwd=repo_path,
                capture_output=True,
                check=True
            )
            # If it resolves and is not a remote-tracking branch, it's valid
            return True
        except subprocess.CalledProcessError:
            pass
    except subprocess.CalledProcessError:
        # Can't check remotes, fall back to simple check
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", ref],
                cwd=repo_path,
                capture_output=True,
                check=True
            )
            return True
        except subprocess.CalledProcessError:
            pass
    
    return False

def _fetch_branch_from_remote(repo_path: Path, remote: str, branch: str) -> bool:
    """Fetch a branch from remote and create a local tracking branch.
    
    This function fetches a remote branch and creates a corresponding local branch.
    It uses `git fetch <remote> <branch>:<branch>` which creates the local branch
    if it doesn't exist, or updates it if it does.
    
    Args:
        repo_path: Path to the Git repository.
        remote: Remote name (e.g., "origin").
        branch: Branch name to fetch.
    
    Returns:
        True if fetch succeeded, False otherwise.
    """
    try:
        # Method 1: Try git fetch remote branch:branch (creates local branch if not exists)
        result = subprocess.run(
            ["git", "fetch", remote, f"{branch}:{branch}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        # Verify that the local branch was created
        if _check_local_ref_exists(repo_path, branch):
            return True
    except subprocess.CalledProcessError:
        # Method 1 failed, try alternative method
        pass
    
    # Method 2: If fetch branch:branch failed, try fetching and then checking out
    try:
        # First, fetch the remote branch (updates remote-tracking branch)
        subprocess.run(
            ["git", "fetch", remote, branch],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        # Then create local branch from remote-tracking branch
        subprocess.run(
            ["git", "branch", branch, f"{remote}/{branch}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        # Verify that the local branch was created
        if _check_local_ref_exists(repo_path, branch):
            return True
    except subprocess.CalledProcessError:
        pass
    
    return False


def _check_remote_ref(repo_path: Path, ref: str) -> Optional[str]:
    """Check if a Git reference exists in any remote.
    
    Args:
        repo_path: Path to the Git repository.
        ref: Git reference (branch, tag, or commit).
    
    Returns:
        Remote name if found (e.g., "origin"), None otherwise.
    """
    try:
        # Get list of remotes
        result = subprocess.run(
            ["git", "remote"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        remotes = [r.strip() for r in result.stdout.strip().split("\n") if r.strip()]
        
        # Check each remote for the branch
        for remote in remotes:
            try:
                ls_result = subprocess.run(
                    ["git", "ls-remote", "--heads", "--tags", remote, ref],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    check=True
                )
                # If command succeeds and has output, the ref exists in this remote
                if ls_result.stdout.strip():
                    return remote
            except subprocess.CalledProcessError:
                continue
        
        return None
    except subprocess.CalledProcessError:
        return None


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


def extract_files_from_diff(diff_content: str, config: Optional[Config] = None) -> List[str]:
    """Extract file paths from a Git diff string.
    
    This function parses a Git diff to extract the list of files that were changed.
    It handles both unified diff format (---/+++) and rename/copy operations.
    
    Args:
        diff_content: The Git diff content as a string.
    
    Returns:
        A list of unique file paths found in the diff. Paths are relative to the
        repository root (as they appear in the diff).
    """
    if not diff_content or not diff_content.strip():
        return []
    
    files = set()
    lines = diff_content.split("\n")
    
    for line in lines:
        # Match unified diff format: --- a/path/to/file or +++ b/path/to/file
        # Also match: --- /dev/null or +++ /dev/null (for new/deleted files)
        if line.startswith("--- ") or line.startswith("+++ "):
            # Extract the file path (remove prefix like "a/" or "b/")
            path_part = line[4:].strip()
            # Skip /dev/null entries
            if path_part == "/dev/null":
                continue
            # Remove "a/" or "b/" prefix if present
            if path_part.startswith("a/") or path_part.startswith("b/"):
                path_part = path_part[2:]
            # Remove leading slash if present
            if path_part.startswith("/"):
                path_part = path_part[1:]
            if path_part:
                files.add(path_part)
        
        # Also match rename/copy operations: rename from/to
        rename_match = re.match(r"^rename from (.+)$", line)
        if rename_match:
            files.add(rename_match.group(1))
        
        rename_to_match = re.match(r"^rename to (.+)$", line)
        if rename_to_match:
            files.add(rename_to_match.group(1))
    
    return filter_changed_files(sorted(list(files)), config)


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


def ensure_head_version(repo_path: Path, head: str = "HEAD") -> None:
    """Ensure the repository is checked out to the HEAD version (not base version).
    
    This function checks if the repository is currently on the head branch/commit,
    and if not, it will checkout to the head version. This ensures that code review
    is performed on the correct version of the code.
    
    Args:
        repo_path: Path to the Git repository.
        head: The head branch or commit to checkout (default: "HEAD").
    
    Raises:
        ValueError: If repo_path is not a valid Git repository.
        RuntimeError: If checkout fails or if there are uncommitted changes that prevent checkout.
    """
    repo_path = Path(repo_path).resolve()
    
    if not repo_path.exists():
        raise ValueError(f"Repository path does not exist: {repo_path}")
    
    if not repo_path.is_dir():
        raise ValueError(f"Repository path must be a directory: {repo_path}")
    
    # Check if it's a Git repository
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repo_path,
            capture_output=True,
            check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise ValueError(f"Not a Git repository: {repo_path}")
    
    # Get current branch/commit
    try:
        current_ref_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        current_ref = current_ref_result.stdout.strip()
        
        # Get current commit hash
        current_commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        current_commit = current_commit_result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.warning(f"Could not get current Git reference: {e}")
        current_ref = None
        current_commit = None
    
    # Get head branch/commit
    try:
        head_ref_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", head],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        head_ref = head_ref_result.stdout.strip()
        
        # Get head commit hash
        head_commit_result = subprocess.run(
            ["git", "rev-parse", head],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        head_commit = head_commit_result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.warning(f"Could not resolve head reference '{head}': {e}")
        raise ValueError(f"Invalid head reference: {head}")
    
    # Check if already on head version
    if current_commit == head_commit:
        logger.info(f"Repository is already on head version: {head_ref} ({head_commit[:12]})")
        return
    
    # Check for uncommitted changes
    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        has_changes = bool(status_result.stdout.strip())
        
        if has_changes:
            logger.warning(f"Repository has uncommitted changes. Stashing before checkout...")
            # Stash uncommitted changes
            stash_result = subprocess.run(
                ["git", "stash", "push", "-m", "Code review agent: auto-stash before checkout"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8"
            )
            if stash_result.returncode != 0:
                raise RuntimeError(
                    f"Cannot checkout to head version: uncommitted changes exist and stash failed. "
                    f"Please commit or stash your changes manually."
                )
    except subprocess.CalledProcessError as e:
        logger.warning(f"Could not check Git status: {e}")
    
    # Checkout to head version
    try:
        logger.info(f"Checking out to head version: {head_ref} ({head_commit[:12]})")
        checkout_result = subprocess.run(
            ["git", "checkout", head],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8"
        )
        logger.info(f"Successfully checked out to head version: {head_ref} ({head_commit[:12]})")
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8") if e.stderr else str(e)
        raise RuntimeError(f"Failed to checkout to head version '{head}': {error_msg}")
