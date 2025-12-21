"""Prompt templates for agents.

This module provides prompt templates for different agents in the code review system.
Templates are stored as separate files and can be loaded and rendered with dynamic values.

Uses LangChain's PromptTemplate for better template management and validation.
"""

from pathlib import Path
from langchain_core.prompts import PromptTemplate

# Base directory for prompt templates
PROMPTS_DIR = Path(__file__).parent

# Cache for loaded templates
_template_cache: dict[str, PromptTemplate] = {}


def load_prompt_template(template_name: str) -> PromptTemplate:
    """Load a prompt template from file using LangChain's PromptTemplate.
    
    Templates are cached after first load for better performance.
    
    Args:
        template_name: Name of the template file (without .txt extension).
    
    Returns:
        A PromptTemplate instance.
    
    Raises:
        FileNotFoundError: If the template file doesn't exist.
    """
    # Check cache first
    if template_name in _template_cache:
        return _template_cache[template_name]
    
    template_path = PROMPTS_DIR / f"{template_name}.txt"
    
    if not template_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")
    
    # Read template content
    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()
    
    # Create PromptTemplate from content
    prompt_template = PromptTemplate.from_template(template_content)
    
    # Cache it
    _template_cache[template_name] = prompt_template
    
    return prompt_template


def render_prompt_template(template_name: str, **kwargs) -> str:
    """Load and render a prompt template with provided values.
    
    Args:
        template_name: Name of the template file (without .txt extension).
        **kwargs: Variables to substitute in the template.
    
    Returns:
        The rendered prompt string.
    
    Raises:
        FileNotFoundError: If the template file doesn't exist.
        KeyError: If a required variable is missing from kwargs.
    """
    template = load_prompt_template(template_name)
    
    # Use PromptTemplate's format method
    return template.format(**kwargs)


__all__ = ["load_prompt_template", "render_prompt_template", "PROMPTS_DIR"]
