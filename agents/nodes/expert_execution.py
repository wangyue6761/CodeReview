"""代码审查工作流的专家执行节点。

处理专家智能体的并行执行（并发控制）。
每个专家组并发处理特定风险类型的任务。
"""

import asyncio
import logging
import json
from typing import Dict, Any, List, Optional
from core.state import ReviewState, RiskItem, RiskType
from core.llm import LLMProvider
from core.langchain_llm import LangChainLLMAdapter
from core.config import Config
from agents.prompts import render_prompt_template
from agents.expert_graph import build_expert_graph, create_langchain_tools, run_expert_analysis
from util.file_utils import read_file_content
from util.diff_utils import extract_file_diff

logger = logging.getLogger(__name__)


def format_line_number(line_number: tuple[int, int]) -> str:
    """格式化行号范围为字符串（"10:15" 或 "10"）。"""
    start_line, end_line = line_number
    if start_line == end_line:
        return str(start_line)
    else:
        return f"{start_line}:{end_line}"


async def expert_execution_node(state: ReviewState) -> Dict[str, Any]:
    """并行执行专家智能体（并发控制）。
    
    Returns:
        包含 'expert_results' 键的字典。
    """
    print("\n" + "="*80)
    print("🔬 [节点3] Expert Execution - 并行执行专家组")
    print("="*80)
    
    # Get dependencies from metadata
    llm_provider: LLMProvider = state.get("metadata", {}).get("llm_provider")
    config: Config = state.get("metadata", {}).get("config")
    
    if not llm_provider:
        logger.error("LLM provider not found in metadata")
        return {"expert_results": {}}
    
    if not config:
        logger.error("Config not found in metadata")
        return {"expert_results": {}}
    
    expert_tasks_dicts = state.get("expert_tasks", {})
    diff_context = state.get("diff_context", "")
    
    if not expert_tasks_dicts:
        print("  ⚠️  没有专家任务需要执行")
        logger.warning("No expert tasks to execute")
        return {"expert_results": {}}
    
    # Convert dicts to Pydantic models for processing
    from core.state import RiskItem
    expert_tasks = {
        risk_type: [RiskItem(**item) if isinstance(item, dict) else item for item in items]
        for risk_type, items in expert_tasks_dicts.items()
    }
    
    # Get concurrency limit from config
    max_concurrent = config.system.max_concurrent_llm_requests
    
    print(f"  📥 接收专家组任务: {len(expert_tasks)} 个专家组")
    total_tasks = sum(len(tasks) for tasks in expert_tasks.values())
    print(f"  📊 总任务数: {total_tasks}")
    print(f"  🔒 并发控制: Semaphore(max={max_concurrent})")
    print(f"  📋 专家组详情:")
    for risk_type, tasks in expert_tasks.items():
        print(f"     • {risk_type}: {len(tasks)} 个任务")
    
    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(max_concurrent)
    
    # Execute all expert groups in parallel
    expert_results = {}
    
    # Create tasks for each risk type
    tasks = []
    for risk_type_str, risk_items in expert_tasks.items():
        task = run_expert_group(
            risk_type_str=risk_type_str,
            tasks=risk_items,
            global_state=state,
            llm_provider=llm_provider,
            semaphore=semaphore,
            diff_context=diff_context
        )
        tasks.append((risk_type_str, task))
    
    # Wait for all expert groups to complete
    print(f"\n  🚀 开始并行执行 {len(expert_tasks)} 个专家组...")
    results = await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)
    
    # Collect results
    for (risk_type_str, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            import traceback
            error_msg = str(result) if str(result) else type(result).__name__
            error_traceback = traceback.format_exception(type(result), result, result.__traceback__)
            print(f"  ❌ 专家组 {risk_type_str} 执行失败: {error_msg}")
            logger.error(f"Error in expert group {risk_type_str}: {error_msg}")
            logger.error(f"Traceback:\n{''.join(error_traceback)}")
            expert_results[risk_type_str] = []
        else:
            print(f"  ✅ 专家组 {risk_type_str} 完成: {len(result)} 个结果")
            expert_results[risk_type_str] = result
    
    total_results = sum(len(r) for r in expert_results.values())
    print(f"\n  ✅ Expert Execution 完成!")
    print(f"     - 完成专家组: {len(expert_results)}")
    print(f"     - 总结果数: {total_results}")
    print("="*80)
    logger.info(f"Expert execution completed: {len(expert_results)} groups, "
                f"{total_results} total results")
    
    # Convert Pydantic models to dicts for state (LangGraph TypedDict compatibility)
    expert_results_dicts = {
        risk_type: [item.model_dump() for item in items]
        for risk_type, items in expert_results.items()
    }
    
    return {"expert_results": expert_results_dicts}


async def run_expert_group(
    risk_type_str: str,
    tasks: List[RiskItem],
    global_state: ReviewState,
    llm_provider: LLMProvider,
    semaphore: asyncio.Semaphore,
    diff_context: str
) -> List[RiskItem]:
    """Run expert group for a specific risk type.
    
    This function processes all tasks for a given risk type, with concurrency
    control to limit simultaneous LLM API calls.
    
    Args:
        risk_type_str: Risk type as string (e.g., "null_safety", "concurrency", "security").
        tasks: List of RiskItem objects to process.
        global_state: Global workflow state for context.
        llm_provider: LLM provider instance.
        semaphore: Semaphore for concurrency control.
        diff_context: Full diff context.
    
    Returns:
        List of validated RiskItem objects.
    """
    if not tasks:
        return []
    
    print(f"    🔍 [{risk_type_str}] 开始处理 {len(tasks)} 个任务...")
    
    try:
        # 创建 LangChain LLM 适配器
        llm_adapter = LangChainLLMAdapter(llm_provider=llm_provider)
        
        # 获取配置和工作区根目录
        config = global_state.get("metadata", {}).get("config")
        workspace_root = str(config.system.workspace_root) if config else None
        asset_key = config.system.asset_key if config else None
        
        # 创建 LangChain 工具
        langchain_tools = create_langchain_tools(
            workspace_root=workspace_root,
            asset_key=asset_key
        )
    except Exception as e:
        import traceback
        error_msg = str(e) if str(e) else type(e).__name__
        error_traceback = traceback.format_exception(type(e), e, e.__traceback__)
        logger.error(f"Error initializing expert group {risk_type_str}: {error_msg}")
        logger.error(f"Traceback:\n{''.join(error_traceback)}")
        raise  # 重新抛出异常，让外层捕获
    
    # Process each task with concurrency control
    async def process_task(task: RiskItem) -> Optional[RiskItem]:
        """Process a single task with concurrency control using LangGraph subgraph."""
        async with semaphore:
            try:
                # 生成系统提示词
                line_number_str = format_line_number(task.line_number)
                file_content = read_file_content(task.file_path, config) if config else ""
                
                # 格式化可用工具描述
                tool_descriptions = []
                for tool in langchain_tools:
                    desc = getattr(tool, 'description', f'Tool: {tool.name}')
                    tool_descriptions.append(f"- **{tool.name}**: {desc}")
                available_tools_text = "\n".join(tool_descriptions)
                
                try:
                    system_prompt = render_prompt_template(
                        f"expert_{risk_type_str}",
                        risk_type=risk_type_str,
                        file_path=task.file_path,
                        line_number=line_number_str,
                        description=task.description,
                        diff_context=extract_file_diff(diff_context, task.file_path),
                        file_content=file_content,
                        available_tools=available_tools_text,
                        validation_logic_examples=""
                    )
                except FileNotFoundError:
                    # Fallback to generic expert prompt
                    system_prompt = render_prompt_template(
                        "expert_generic",
                        risk_type=risk_type_str,
                        file_path=task.file_path,
                        line_number=line_number_str,
                        description=task.description,
                        diff_context=extract_file_diff(diff_context, task.file_path),
                        file_content=file_content,
                        available_tools=available_tools_text
                    )
                
                # 构建专家子图（每个任务使用相同的图结构，但系统提示词在运行时动态注入）
                expert_graph = build_expert_graph(
                    llm=llm_adapter,
                    tools=langchain_tools,
                    system_prompt=system_prompt
                )
                
                # 运行专家分析子图
                result_dict = await run_expert_analysis(
                    graph=expert_graph,
                    risk_item=task,
                    system_prompt=system_prompt
                )
                
                if not result_dict:
                    logger.warning(f"Failed to get result from expert analysis for {task.file_path}")
                    return None
                
                # 将结果转换为 RiskItem
                validated_item = RiskItem(
                    risk_type=RiskType(result_dict.get("risk_type", task.risk_type.value)),
                    file_path=result_dict.get("file_path", task.file_path),
                    line_number=result_dict.get("line_number", task.line_number),
                    description=result_dict.get("description", task.description),
                    confidence=float(result_dict.get("confidence", task.confidence)),
                    severity=result_dict.get("severity", task.severity),
                    suggestion=result_dict.get("suggestion", task.suggestion)
                )
                
                # 记录专家分析日志
                expert_analysis = {
                    "risk_type": risk_type_str,
                    "file_path": task.file_path,
                    "line_number": task.line_number,
                    "final_response": json.dumps(result_dict, ensure_ascii=False),
                    "validated_item": validated_item.model_dump()
                }
                
                if "metadata" not in global_state:
                    global_state["metadata"] = {}
                if "expert_analyses" not in global_state["metadata"]:
                    global_state["metadata"]["expert_analyses"] = []
                global_state["metadata"]["expert_analyses"].append(expert_analysis)
                
                return validated_item
                
            except Exception as e:
                import traceback
                line_str = format_line_number(task.line_number)
                error_msg = str(e) if str(e) else type(e).__name__
                error_traceback = traceback.format_exception(type(e), e, e.__traceback__)
                logger.error(f"Error processing risk item {task.file_path}:{line_str}: {error_msg}")
                logger.error(f"Traceback:\n{''.join(error_traceback)}")
                return None
    
    # Process all tasks concurrently (with semaphore limiting)
    results = await asyncio.gather(*[process_task(task) for task in tasks])
    
    # Filter out None results (errors)
    validated_results = [r for r in results if r is not None]
    
    print(f"    ✅ [{risk_type_str}] 完成: {len(validated_results)}/{len(tasks)} 个任务验证成功")
    logger.info(f"Expert group {risk_type_str}: processed {len(tasks)} tasks, "
                f"{len(validated_results)} validated results")
    
    return validated_results


