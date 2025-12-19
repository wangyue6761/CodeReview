"""Utility modules for code review agent.

This package contains utility functions for:
- Logging: Agent observations and tool results
- Git operations: Repository information and diff generation
- PR processing: Diff file loading and result formatting
- Argument validation: Command line argument validation and diff loading
"""

from util.logger import save_observations_to_log
from util.git_utils import (
    get_git_info,
    get_git_diff,
    generate_asset_key,
    get_repo_name,
)
from util.pr_utils import (
    load_diff_from_file,
    print_review_results,
)
from util.arg_utils import (
    validate_repo_path,
    load_diff_from_args,
)

__all__ = [
    "save_observations_to_log",
    "get_git_info",
    "get_git_diff",
    "generate_asset_key",
    "get_repo_name",
    "load_diff_from_file",
    "print_review_results",
    "validate_repo_path",
    "load_diff_from_args",
]
