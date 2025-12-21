"""Configuration loader for syntax checkers.

This module loads and manages configuration for syntax checkers,
allowing users to enable/disable specific checkers via configuration file.
"""

import os
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


class CheckerConfig:
    """Configuration for a single checker.
    
    Attributes:
        enabled: Whether the checker is enabled.
        args: Command-line arguments for the checker.
        severity_map: Optional mapping of error codes to severity levels.
    """
    
    def __init__(
        self,
        enabled: bool = True,
        args: str = "",
        severity_map: Optional[Dict[str, str]] = None
    ):
        """Initialize checker configuration.
        
        Args:
            enabled: Whether the checker is enabled.
            args: Command-line arguments for the checker.
            severity_map: Optional mapping of error codes to severity levels.
        """
        self.enabled = enabled
        self.args = args
        self.severity_map = severity_map or {}


class SyntaxCheckerConfig:
    """Configuration manager for syntax checkers."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize configuration loader.
        
        Args:
            config_path: Path to configuration file. If None, uses default location.
        """
        if config_path is None:
            # Default to config.yaml in the syntax_checker directory
            config_path = Path(__file__).parent / "config.yaml"
        
        self.config_path = Path(config_path)
        self._config: Dict = {}
        self._load_config()
    
    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            # Use default configuration if file doesn't exist
            self._config = self._get_default_config()
            return
        
        if not YAML_AVAILABLE:
            print(f"⚠️  Warning: PyYAML not available. Using default configuration.")
            self._config = self._get_default_config()
            return
        
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"⚠️  Warning: Failed to load configuration from {self.config_path}: {e}")
            self._config = self._get_default_config()
    
    def _get_default_config(self) -> Dict:
        """Get default configuration.
        
        Returns:
            Default configuration dictionary.
        """
        return {
            "python": {
                "pylint": {
                    "enabled": True,
                    "args": "--disable=all --enable=E0611,E0401,E1101,E0602"
                },
                "ruff": {
                    "enabled": False,
                    "args": "--output-format=json"
                }
            }
        }
    
    def get_checker_config(
        self,
        language: str,
        checker_name: str
    ) -> Optional[CheckerConfig]:
        """Get configuration for a specific checker.
        
        Args:
            language: Language name (e.g., "python").
            checker_name: Checker name (e.g., "pylint", "ruff").
        
        Returns:
            CheckerConfig object if found, None otherwise.
        """
        language_config = self._config.get(language, {})
        checker_config = language_config.get(checker_name, {})
        
        if not checker_config:
            return None
        
        enabled = checker_config.get("enabled", True)
        args = checker_config.get("args", "")
        severity_map = checker_config.get("severity_map")
        
        return CheckerConfig(
            enabled=enabled,
            args=args,
            severity_map=severity_map
        )
    
    def is_checker_enabled(
        self,
        language: str,
        checker_name: str
    ) -> bool:
        """Check if a specific checker is enabled.
        
        Args:
            language: Language name (e.g., "python").
            checker_name: Checker name (e.g., "pylint", "ruff").
        
        Returns:
            True if checker is enabled, False otherwise.
        """
        config = self.get_checker_config(language, checker_name)
        return config is not None and config.enabled


# Global configuration instance
_global_config: Optional[SyntaxCheckerConfig] = None


def get_config(config_path: Optional[Path] = None) -> SyntaxCheckerConfig:
    """Get global configuration instance.
    
    Args:
        config_path: Optional path to configuration file.
    
    Returns:
        SyntaxCheckerConfig instance.
    """
    global _global_config
    if _global_config is None:
        _global_config = SyntaxCheckerConfig(config_path)
    return _global_config


def get_checker_config_key(checker_class_name: str) -> Tuple[str, str]:
    """Get configuration key (language, checker_name) from checker class name.
    
    This function maps checker class names to their configuration keys.
    For example:
    - PythonPylintChecker -> ("python", "pylint")
    - PythonRuffChecker -> ("python", "ruff")
    
    Args:
        checker_class_name: Name of the checker class.
    
    Returns:
        Tuple of (language, checker_name). Returns ("", "") if not found.
    """
    # Mapping from checker class names to config keys
    # Format: "ClassName" -> ("language", "checker_name")
    checker_map = {
        "PythonPylintChecker": ("python", "pylint"),
        "PythonRuffChecker": ("python", "ruff"),
    }
    
    return checker_map.get(checker_class_name, ("", ""))


def create_checker_instance(checker_class, config: Optional[SyntaxCheckerConfig] = None):
    """Create a checker instance with configuration if available.
    
    This function attempts to load configuration for the checker and create
    an instance with the appropriate parameters. If no configuration is found
    or the checker doesn't support configuration, it creates a default instance.
    
    Args:
        checker_class: The checker class to instantiate.
        config: Optional SyntaxCheckerConfig instance. If None, uses global config.
    
    Returns:
        An instance of the checker class.
    """
    if config is None:
        config = get_config()
    
    # Get configuration key from class name
    language, checker_name = get_checker_config_key(checker_class.__name__)
    
    if language and checker_name:
        # Try to get configuration
        checker_config = config.get_checker_config(language, checker_name)
        
        if checker_config and checker_config.args:
            # Check if the checker class accepts 'args' parameter
            import inspect
            sig = inspect.signature(checker_class.__init__)
            if 'args' in sig.parameters:
                return checker_class(args=checker_config.args)
    
    # Default: create instance without configuration
    return checker_class()
