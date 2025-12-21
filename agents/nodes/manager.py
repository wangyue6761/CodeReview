"""Manager Node for the code review workflow.

This node receives file analyses and generates a work list of tasks for expert agents.
It groups tasks by risk type to enable parallel execution.
"""

import logging
import json
from typing import Dict, Any, List
from core.state import ReviewState, RiskItem, RiskType
from core.llm import LLMProvider
from agents.prompts import render_prompt_template

logger = logging.getLogger(__name__)


async def manager_node(state: ReviewState) -> Dict[str, Any]:
    """Manager node that generates work list and groups tasks by risk type.
    
    This node:
    1. Receives file_analyses from intent analysis
    2. Generates a work_list of RiskItems
    3. Groups work_list by risk_type into expert_tasks
    
    Args:
        state: Current workflow state with file_analyses.
    
    Returns:
        Dictionary with 'work_list' and 'expert_tasks' keys.
    """
    print("\n" + "="*80)
    print("👔 [节点2] Manager - 生成任务列表并分组")
    print("="*80)
    
    # Get LLM provider from metadata
    llm_provider: LLMProvider = state.get("metadata", {}).get("llm_provider")
    if not llm_provider:
        logger.error("LLM provider not found in metadata")
        return {"work_list": [], "expert_tasks": {}}
    
    file_analyses_dicts = state.get("file_analyses", [])
    diff_context = state.get("diff_context", "")
    
    if not file_analyses_dicts:
        print("  ⚠️  没有文件分析结果")
        logger.warning("No file analyses available for manager")
        return {"work_list": [], "expert_tasks": {}}
    
    # Convert dicts to Pydantic models for processing
    from core.state import FileAnalysis
    file_analyses = [FileAnalysis(**fa) if isinstance(fa, dict) else fa for fa in file_analyses_dicts]
    
    print(f"  📥 接收文件分析: {len(file_analyses)} 个")
    
    try:
        # Prepare file analyses summary for prompt
        analyses_summary = _format_file_analyses(file_analyses)
        
        # Load and render manager prompt
        prompt = render_prompt_template(
            "manager",
            diff_context=diff_context[:3000],  # Limit context size
            file_analyses_summary=analyses_summary,
            num_files=len(file_analyses)
        )
        
        print("  🤖 调用 LLM 生成工作列表...")
        # Get LLM response
        response = await llm_provider.generate(prompt, temperature=0.4)
        
        # Parse response to extract work_list
        work_list = _parse_manager_response(response, file_analyses)
        
        # Group work_list by risk_type
        expert_tasks = _group_tasks_by_risk_type(work_list)
        
        print(f"  ✅ Manager 完成!")
        print(f"     - 生成任务数: {len(work_list)}")
        print(f"     - 专家组数量: {len(expert_tasks)}")
        print(f"     - 任务分组:")
        for risk_type, tasks in expert_tasks.items():
            print(f"       • {risk_type}: {len(tasks)} 个任务")
        print("="*80)
        logger.info(f"Manager generated {len(work_list)} tasks, grouped into {len(expert_tasks)} expert groups")
        
        # Convert Pydantic models to dicts for state (LangGraph TypedDict compatibility)
        work_list_dicts = [item.model_dump() for item in work_list]
        expert_tasks_dicts = {
            risk_type: [item.model_dump() for item in items]
            for risk_type, items in expert_tasks.items()
        }
        
        return {
            "work_list": work_list_dicts,
            "expert_tasks": expert_tasks_dicts
        }
    except Exception as e:
        logger.error(f"Error in manager node: {e}")
        return {"work_list": [], "expert_tasks": {}}


def _format_file_analyses(file_analyses: List[Any]) -> str:
    """Format file analyses for prompt.
    
    Args:
        file_analyses: List of FileAnalysis objects.
    
    Returns:
        Formatted string summary.
    """
    summaries = []
    for analysis in file_analyses:
        summaries.append(
            f"File: {analysis.file_path}\n"
            f"Intent: {analysis.intent_summary}\n"
            f"Potential Risks: {len(analysis.potential_risks)}\n"
        )
    return "\n".join(summaries)


def _parse_manager_response(response: str, file_analyses: List[Any]) -> List[RiskItem]:
    """Parse manager LLM response into list of RiskItems.
    
    Args:
        response: LLM response string.
        file_analyses: List of FileAnalysis objects for context.
    
    Returns:
        List of RiskItem objects.
    """
    work_list = []
    
    try:
        # Try to parse as JSON
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
            work_list_data = data.get("work_list", [])
            
            # If work_list is not in response, try to extract from top level
            if not work_list_data and isinstance(data, list):
                work_list_data = data
            
            for item_data in work_list_data:
                try:
                    risk_item = RiskItem(
                        risk_type=RiskType(item_data.get("risk_type", "maintainability")),
                        file_path=item_data.get("file_path", ""),
                        line_number=item_data.get("line_number", 0),
                        description=item_data.get("description", ""),
                        confidence=item_data.get("confidence", 0.5),
                        severity=item_data.get("severity", "warning"),
                        suggestion=item_data.get("suggestion")
                    )
                    work_list.append(risk_item)
                except Exception as e:
                    logger.warning(f"Failed to parse work list item: {e}")
                    continue
        except json.JSONDecodeError:
            # If JSON parsing fails, extract potential risks from file_analyses
            logger.warning("Failed to parse manager response as JSON, using file_analyses risks")
            for analysis in file_analyses:
                work_list.extend(analysis.potential_risks)
    except Exception as e:
        logger.error(f"Error parsing manager response: {e}")
        # Fallback: use risks from file_analyses
        for analysis in file_analyses:
            work_list.extend(analysis.potential_risks)
    
    return work_list


def _group_tasks_by_risk_type(work_list: List[RiskItem]) -> Dict[str, List[RiskItem]]:
    """Group work list items by risk type.
    
    Args:
        work_list: List of RiskItem objects.
    
    Returns:
        Dictionary mapping risk_type (as string) to list of RiskItems.
    """
    grouped = {}
    for item in work_list:
        risk_type_str = item.risk_type.value
        if risk_type_str not in grouped:
            grouped[risk_type_str] = []
        grouped[risk_type_str].append(item)
    
    return grouped
