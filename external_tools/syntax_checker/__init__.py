"""Syntax checking tools for various programming languages."""

from external_tools.syntax_checker.base import BaseSyntaxChecker, LintError
from external_tools.syntax_checker.factory import CheckerFactory
from external_tools.syntax_checker.config_loader import get_config

# Import all available checkers
from external_tools.syntax_checker.implementations.python_ruff import PythonRuffChecker
from external_tools.syntax_checker.implementations.python_pylint import PythonPylintChecker

# Load configuration
_config = get_config()

# Register checkers based on configuration
# Python checkers
if _config.is_checker_enabled("python", "pylint"):
    CheckerFactory.register(PythonPylintChecker, [".py", ".pyi"])

if _config.is_checker_enabled("python", "ruff"):
    CheckerFactory.register(PythonRuffChecker, [".py", ".pyi"])

__all__ = ["BaseSyntaxChecker", "CheckerFactory", "LintError", "get_config"]
