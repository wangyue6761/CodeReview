"""Python syntax checker using Pylint.

This module implements a syntax checker for Python files using Pylint,
a comprehensive Python linter.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from external_tools.syntax_checker.base import BaseSyntaxChecker, LintError


class PythonPylintChecker(BaseSyntaxChecker):
    """Syntax checker for Python files using Pylint.
    
    This checker uses the `pylint` command to analyze Python files
    and report linting errors. It gracefully handles cases where Pylint
    is not installed or files don't exist.
    """
    
    def __init__(self, args: Optional[str] = None):
        """Initialize the Pylint checker.
        
        Args:
            args: Optional command-line arguments for pylint.
                  Default: "--disable=all --enable=E0611,E0401,E1101,E0602"
        """
        self._pylint_available = self._check_pylint_available()
        self.args = args or "--disable=all --enable=E0611,E0401,E1101,E0602"
        self._warning_shown = False
    
    def _check_pylint_available(self) -> bool:
        """Check if pylint command is available in PATH.
        
        Returns:
            True if pylint is available, False otherwise.
        """
        return shutil.which("pylint") is not None
    
    async def check(
        self,
        repo_path: Path,
        files: List[str]
    ) -> List[LintError]:
        """Run Pylint on the specified Python files.
        
        Args:
            repo_path: Root path of the repository.
            files: List of file paths relative to repo_path to check.
        
        Returns:
            A list of LintError objects found by Pylint. Returns empty list
            if Pylint is not available, if no Python files are found, or if
            no errors are detected.
        """
        if not self._pylint_available:
            if not self._warning_shown:
                print("  ⚠️  Warning: Pylint is not installed. Python syntax checking will be skipped.")
                print("     Install Pylint with: pip install pylint")
                self._warning_shown = True
            return []
        
        # Filter to only Python files and existing files
        python_files = [
            f for f in files
            if f.endswith((".py", ".pyi"))
        ]
        
        if not python_files:
            return []
        
        # Get existing file paths
        existing_files = self._filter_existing_files(repo_path, python_files)
        
        if not existing_files:
            # If using --diff-file mode, files might not exist locally
            # Return empty list gracefully
            return []
        
        # Build pylint command
        # Use relative paths from repo_path
        relative_paths = [str(f.relative_to(repo_path)) for f in existing_files]
        
        try:
            # Parse args string into list
            args_list = self.args.split() if isinstance(self.args, str) else []
            
            cmd = [
                "pylint",
                *args_list,
                "--output-format=json",
                *relative_paths
            ]
            
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=False,  # Don't raise on non-zero exit
                encoding="utf-8"
            )
            
            # Pylint returns non-zero exit code if errors are found
            # Exit codes: 0 = no errors, 1-31 = various error conditions
            # We check for actual failures (not just errors found)
            if result.returncode > 32:
                # Exit code > 32 indicates a fatal error in pylint itself
                return []
            
            # Parse JSON output
            if not result.stdout.strip():
                return []
            
            errors = []
            stdout = result.stdout.strip()
            
            # Pylint outputs JSON array
            try:
                diagnostics = json.loads(stdout)
                if not isinstance(diagnostics, list):
                    diagnostics = [diagnostics]
            except json.JSONDecodeError:
                # If JSON parsing fails, try to parse text output as fallback
                # (though this shouldn't happen with --output-format=json)
                return []
            
            # Process each diagnostic
            for data in diagnostics:
                if not isinstance(data, dict):
                    continue
                
                # Pylint JSON format:
                # {
                #   "type": "error",
                #   "module": "module_name",
                #   "obj": "function_name",
                #   "line": 10,
                #   "column": 5,
                #   "path": "path/to/file.py",
                #   "symbol": "E0611",
                #   "message": "Undefined name 'something'",
                #   "message-id": "E0611"
                # }
                
                # Extract error information
                file_path_str = data.get("path", "")
                if not file_path_str:
                    continue
                
                # Get relative path from repo_path
                file_path = Path(file_path_str)
                if file_path.is_absolute():
                    try:
                        file_path = file_path.relative_to(repo_path)
                    except ValueError:
                        # File is outside repo, skip
                        continue
                
                line_num = data.get("line", 1)
                message = data.get("message", "")
                symbol = data.get("symbol", "") or data.get("message-id", "")
                
                # Determine severity from type field
                # Pylint types: "error", "warning", "convention", "refactor", "info"
                pylint_type = data.get("type", "error").lower()
                severity = "error"
                if pylint_type == "warning":
                    severity = "warning"
                elif pylint_type in ["convention", "refactor", "info"]:
                    severity = "info"
                
                # If we have a symbol code, use it to determine severity
                if symbol:
                    # E = error, W = warning, C = convention, R = refactor, I = info
                    if symbol.startswith("E"):
                        severity = "error"
                    elif symbol.startswith("W"):
                        severity = "warning"
                    elif symbol.startswith(("C", "R", "I")):
                        severity = "info"
                
                errors.append(LintError(
                    file=str(file_path),
                    line=line_num,
                    message=message,
                    severity=severity,
                    code=symbol
                ))
            
            return errors
        
        except Exception:
            # Gracefully handle any errors (subprocess failures, etc.)
            return []
    
    def get_supported_extensions(self) -> List[str]:
        """Get list of file extensions this checker supports.
        
        Returns:
            List of Python file extensions: [".py", ".pyi"].
        """
        return [".py", ".pyi"]
