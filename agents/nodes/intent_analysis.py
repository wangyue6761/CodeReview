"""Intent Analysis Node for the code review workflow.

This node implements a Map-Reduce pattern to analyze the intent of changed files
in parallel. Each file is analyzed independently, and results are aggregated.
"""

import asyncio
import logging
import json
import re
from typing import Dict, Any
from core.state import ReviewState, FileAnalysis, RiskItem, RiskType
from core.llm import LLMProvider
from agents.prompts import render_prompt_template

logger = logging.getLogger(__name__)


async def intent_analysis_node(state: ReviewState) -> Dict[str, Any]:
    """Analyze the intent of all changed files in parallel (Map-Reduce pattern).
    
    This function processes all changed files in parallel and aggregates results.
    
    Args:
        state: Current workflow state with changed_files.
    
    Returns:
        Dictionary with 'file_analyses' key containing a list of FileAnalysis objects.
    """
    print("\n" + "="*80)
    print("📋 [节点1] Intent Analysis - 并行分析文件意图")
    print("="*80)
    
    # Get LLM provider from metadata (injected by workflow)
    llm_provider: LLMProvider = state.get("metadata", {}).get("llm_provider")
    if not llm_provider:
        logger.error("LLM provider not found in metadata")
        return {"file_analyses": []}
    
    # Get config for concurrency control
    config = state.get("metadata", {}).get("config")
    max_concurrent = config.system.max_concurrent_llm_requests if config else 5
    
    changed_files = state.get("changed_files", [])
    if not changed_files:
        print("  ⚠️  没有需要分析的文件")
        logger.warning("No changed files to analyze")
        return {"file_analyses": []}
    
    print(f"  📁 待分析文件数: {len(changed_files)}")
    print(f"  🔒 并发控制: Semaphore(max={max_concurrent})")
    print(f"  📝 文件列表:")
    for i, file_path in enumerate(changed_files, 1):
        print(f"     {i}. {file_path}")
    
    diff_context = state.get("diff_context", "")
    
    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(max_concurrent)
    
    # Process all files in parallel
    async def analyze_file(file_path: str) -> FileAnalysis:
        """Analyze a single file."""
        async with semaphore:
            try:
                print(f"  🔍 分析中: {file_path}")
                # Extract relevant diff section for this file
                file_diff = _extract_file_diff(diff_context, file_path)
                
                # Load and render intent analysis prompt
                prompt = render_prompt_template(
                    "intent_analysis",
                    file_path=file_path,
                    file_diff=file_diff,
                    diff_context=diff_context[:2000]  # Limit context size
                )
                
                # Get LLM response
                response = await llm_provider.generate(prompt, temperature=0.3)
                
                # Parse response to extract FileAnalysis
                file_analysis = _parse_intent_analysis_response(response, file_path)
                
                print(f"  ✅ 完成: {file_path}")
                print(f"     意图摘要: {file_analysis.intent_summary[:80]}...")
                print(f"     潜在风险数: {len(file_analysis.potential_risks)}")
                logger.info(f"Analyzed intent for {file_path}: {file_analysis.intent_summary[:100]}...")
                return file_analysis
            except Exception as e:
                logger.error(f"Error analyzing intent for {file_path}: {e}")
                # Return error analysis
                return FileAnalysis(
                    file_path=file_path,
                    intent_summary=f"Error analyzing file: {str(e)}",
                    potential_risks=[],
                    complexity_score=None
                )
    
    # Process all files concurrently
    print(f"\n  🚀 开始并行分析 {len(changed_files)} 个文件...")
    file_analyses = await asyncio.gather(*[analyze_file(f) for f in changed_files])
    
    # Convert Pydantic models to dicts for state (LangGraph TypedDict compatibility)
    file_analyses_dicts = [fa.model_dump() for fa in file_analyses]
    
    total_risks = sum(len(fa.potential_risks) for fa in file_analyses)
    print(f"\n  ✅ Intent Analysis 完成!")
    print(f"     - 分析文件数: {len(file_analyses)}")
    print(f"     - 发现潜在风险: {total_risks} 个")
    print("="*80)
    logger.info(f"Completed intent analysis for {len(file_analyses)} files")
    
    return {
        "file_analyses": file_analyses_dicts
    }


def _extract_file_diff(diff_context: str, file_path: str) -> str:
    """Extract the diff section for a specific file.
    
    Args:
        diff_context: Full diff context.
        file_path: Path to the file.
    
    Returns:
        Extracted diff section for the file.
    """
    # Look for diff header: "diff --git a/path b/path" or "--- a/path"
    patterns = [
        rf"diff --git.*{re.escape(file_path)}.*?\n(.*?)(?=\ndiff --git|\Z)",
        rf"--- a/{re.escape(file_path)}.*?\n(.*?)(?=\n--- a/|\Z)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, diff_context, re.DOTALL)
        if match:
            return match.group(0)
    
    # If no specific section found, return a portion of the diff
    return diff_context[:1000] if diff_context else ""


def _parse_intent_analysis_response(response: str, file_path: str) -> FileAnalysis:
    """Parse LLM response into FileAnalysis object.
    
    Args:
        response: LLM response string.
        file_path: Path to the analyzed file.
    
    Returns:
        FileAnalysis object.
    """
    try:
        # Try to parse as JSON first
        response_clean = response.strip()
        if response_clean.startswith("```json"):
            response_clean = response_clean[7:]
        if response_clean.startswith("```"):
            response_clean = response_clean[3:]
        if response_clean.endswith("```"):
            response_clean = response_clean[:-3]
        response_clean = response_clean.strip()
        
        try:
            data = json.loads(response_clean)
            intent_summary = data.get("intent_summary", response[:500])
            potential_risks_data = data.get("potential_risks", [])
            complexity_score = data.get("complexity_score")
            
            # Convert potential_risks to RiskItem objects
            potential_risks = []
            for risk_data in potential_risks_data:
                try:
                    risk_item = RiskItem(
                        risk_type=RiskType(risk_data.get("risk_type", "maintainability")),
                        file_path=risk_data.get("file_path", file_path),
                        line_number=risk_data.get("line_number", 0),
                        description=risk_data.get("description", ""),
                        confidence=risk_data.get("confidence", 0.5),
                        severity=risk_data.get("severity", "info"),
                        suggestion=risk_data.get("suggestion")
                    )
                    potential_risks.append(risk_item)
                except Exception as e:
                    logger.warning(f"Failed to parse risk item: {e}")
                    continue
            
            return FileAnalysis(
                file_path=file_path,
                intent_summary=intent_summary,
                potential_risks=potential_risks,
                complexity_score=complexity_score
            )
        except json.JSONDecodeError:
            # If JSON parsing fails, create a simple FileAnalysis from text
            return FileAnalysis(
                file_path=file_path,
                intent_summary=response[:500],
                potential_risks=[],
                complexity_score=None
            )
    except Exception as e:
        logger.error(f"Error parsing intent analysis response: {e}")
        return FileAnalysis(
            file_path=file_path,
            intent_summary=f"Error parsing response: {str(e)}",
            potential_risks=[],
            complexity_score=None
        )
