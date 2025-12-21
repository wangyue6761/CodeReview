"""Factory for creating syntax checkers based on file extensions."""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from external_tools.syntax_checker.base import BaseSyntaxChecker


class CheckerFactory:
    """Factory class for selecting and creating appropriate syntax checkers.
    
    This factory maintains a registry of checkers and selects the appropriate
    checker based on file extensions. It supports multiple checkers for the same
    language (e.g., ruff and pylint for Python).
    """
    
    _checkers: Dict[str, type[BaseSyntaxChecker]] = {}
    _extension_map: Dict[str, List[type[BaseSyntaxChecker]]] = {}
    
    @classmethod
    def register(
        cls,
        checker_class: type[BaseSyntaxChecker],
        extensions: List[str]
    ) -> None:
        """Register a syntax checker for specific file extensions.
        
        Args:
            checker_class: The checker class to register.
            extensions: List of file extensions (e.g., [".py", ".pyi"]).
        """
        cls._checkers[checker_class.__name__] = checker_class
        for ext in extensions:
            # Normalize extension (ensure it starts with .)
            ext = ext if ext.startswith(".") else f".{ext}"
            ext_lower = ext.lower()
            # Support multiple checkers per extension
            if ext_lower not in cls._extension_map:
                cls._extension_map[ext_lower] = []
            if checker_class not in cls._extension_map[ext_lower]:
                cls._extension_map[ext_lower].append(checker_class)
    
    @classmethod
    def get_checkers_for_file(
        cls,
        file_path: str
    ) -> List[type[BaseSyntaxChecker]]:
        """Get all appropriate checker classes for a file.
        
        Args:
            file_path: Path to the file (can be relative or absolute).
        
        Returns:
            List of checker classes for this file. Returns empty list if no checkers registered.
        """
        path = Path(file_path)
        ext = path.suffix.lower()
        return cls._extension_map.get(ext, [])
    
    @classmethod
    def get_checker_for_file(
        cls,
        file_path: str
    ) -> Optional[type[BaseSyntaxChecker]]:
        """Get the first appropriate checker class for a file (for backward compatibility).
        
        Args:
            file_path: Path to the file (can be relative or absolute).
        
        Returns:
            The first checker class for this file, or None if no checker is registered.
        """
        checkers = cls.get_checkers_for_file(file_path)
        return checkers[0] if checkers else None
    
    @classmethod
    def get_checkers_for_files(
        cls,
        files: List[str]
    ) -> Dict[type[BaseSyntaxChecker], List[str]]:
        """Group files by their appropriate checkers.
        
        Args:
            files: List of file paths to check.
        
        Returns:
            Dictionary mapping checker classes to lists of files they should check.
            Multiple checkers can check the same file if registered for the same extension.
        """
        grouped: Dict[type[BaseSyntaxChecker], List[str]] = {}
        
        for file_path in files:
            checker_classes = cls.get_checkers_for_file(file_path)
            for checker_class in checker_classes:
                if checker_class not in grouped:
                    grouped[checker_class] = []
                grouped[checker_class].append(file_path)
        
        return grouped
    
    @classmethod
    def get_all_checkers(cls) -> Dict[str, type[BaseSyntaxChecker]]:
        """Get all registered checkers.
        
        Returns:
            Dictionary mapping checker names to checker classes.
        """
        return cls._checkers.copy()
