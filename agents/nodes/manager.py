"""Manager Node for the code review workflow.

重构说明：
- 使用 PydanticOutputParser 解析结构化输出（LangGraph 标准做法）
- 使用 LCEL 语法：prompt | llm | parser
- 替代手动 JSON 解析，提高类型安全和错误处理

This node receives file analyses and generates a work list of tasks for expert agents.
It groups tasks by risk type to enable parallel execution.
"""

import logging
from typing import Dict, Any, List
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from core.state import ReviewState, RiskItem, RiskType, WorkListResponse
from core.langchain_llm import LangChainLLMAdapter
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
    
    # 获取 LangChain LLM 适配器（从 metadata）
    llm_adapter: LangChainLLMAdapter = state.get("metadata", {}).get("llm_adapter")
    if not llm_adapter:
        # 如果没有适配器，从 llm_provider 创建
        llm_provider = state.get("metadata", {}).get("llm_provider")
        if llm_provider:
            llm_adapter = LangChainLLMAdapter(llm_provider=llm_provider)
        else:
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
        
        # 渲染提示模板（已经完成变量替换）
        rendered_prompt = render_prompt_template(
            "manager",
            diff_context=diff_context[:3000],  # Limit context size
            file_analyses_summary=analyses_summary,
            num_files=len(file_analyses)
        )
        
        # 重构说明：使用 PydanticOutputParser 解析结构化输出
        # 这是 LangGraph 标准做法，替代手动 JSON 解析
        parser = PydanticOutputParser(pydantic_object=WorkListResponse)
        
        # 生成展开的格式说明（包含 RiskItem 的完整结构）
        format_instructions = _get_expanded_format_instructions(parser)
        
        # 创建消息列表（直接使用已渲染的文本，避免 ChatPromptTemplate 解析 JSON 示例）
        messages = [
            SystemMessage(content="You are a Manager Agent for code review. Generate a work list of tasks for expert agents."),
            HumanMessage(content=rendered_prompt + "\n\n" + format_instructions)
        ]
        
        print("  🤖 调用 LLM 生成工作列表...")
        # 使用 LCEL 语法：messages -> llm -> parser
        try:
            # 调用 LLM
            response = await llm_adapter.ainvoke(messages, temperature=0.4)
            # 解析为 Pydantic 模型
            response_text = response.content if hasattr(response, 'content') else str(response)
            parsed_response: WorkListResponse = parser.parse(response_text)
            work_list = parsed_response.work_list
        except Exception as e:
            # 如果解析失败，回退到从 file_analyses 提取风险
            logger.error(f"Failed to parse manager response with PydanticOutputParser: {e}")
            logger.warning("Falling back to extracting risks from file_analyses")
            work_list = []
            for analysis in file_analyses:
                work_list.extend(analysis.potential_risks)
        
        # Convert lint_errors to RiskItems and add to work_list
        lint_errors = state.get("lint_errors", [])
        if lint_errors:
            lint_risk_items = _convert_lint_errors_to_risk_items(lint_errors)
            work_list.extend(lint_risk_items)
            print(f"  📋 添加语法分析任务: {len(lint_risk_items)} 个")
        
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


# 重构说明：_parse_manager_response 函数已被移除
# 现在使用 PydanticOutputParser 直接解析为 WorkListResponse 模型
# 这样可以：
# 1. 自动验证所有字段类型
# 2. 自动处理 line_number 等必需字段的验证
# 3. 提供更好的错误信息
# 4. 符合 LangGraph 标准做法


def _get_expanded_format_instructions(parser: PydanticOutputParser) -> str:
    """Generate expanded format instructions that include nested model structures.
    
    This function expands the JSON schema to show the full structure of nested models
    (like RiskItem) instead of just references.
    
    Args:
        parser: PydanticOutputParser instance.
    
    Returns:
        Expanded format instructions string.
    """
    import json
    
    # Get the JSON schema from the Pydantic model
    schema = WorkListResponse.model_json_schema()
    
    # Expand the schema to resolve $ref references
    def expand_refs(schema_dict: dict, definitions: dict = None) -> dict:
        """Recursively expand $ref references in the schema."""
        if definitions is None:
            definitions = schema_dict.get("$defs", {})
        
        if isinstance(schema_dict, dict):
            if "$ref" in schema_dict:
                # Resolve the reference
                ref_path = schema_dict["$ref"]
                if ref_path.startswith("#/$defs/"):
                    def_name = ref_path.split("/")[-1]
                    if def_name in definitions:
                        # Recursively expand the referenced definition
                        expanded = expand_refs(definitions[def_name], definitions)
                        return expanded
            else:
                # Recursively process all values
                return {k: expand_refs(v, definitions) for k, v in schema_dict.items()}
        elif isinstance(schema_dict, list):
            return [expand_refs(item, definitions) for item in schema_dict]
        else:
            return schema_dict
    
    # Expand the schema
    expanded_schema = expand_refs(schema)
    
    # Remove $defs since we've expanded all references
    if "$defs" in expanded_schema:
        del expanded_schema["$defs"]
    
    # Generate a clean JSON schema string
    schema_str = json.dumps(expanded_schema, indent=2, ensure_ascii=False)
    
    # Get enum values dynamically
    risk_type_values = [rt.value for rt in RiskType]
    risk_type_str = ", ".join([f'"{v}"' for v in risk_type_values])
    
    # Create expanded format instructions
    expanded_instructions = f"""You must respond with a JSON object that matches the following schema:

        {schema_str}

        Important notes:
        - The "risk_type" field must be one of: {risk_type_str}
        - The "line_number" field must be a positive integer (1-indexed)
        - The "confidence" field must be a float between 0.0 and 1.0
        - The "severity" field must be one of: "error", "warning", "info"
        - The "suggestion" field is optional (can be null or omitted)

        Return only the JSON object, without any markdown code blocks or additional text."""
    
    return expanded_instructions


def _convert_lint_errors_to_risk_items(lint_errors: List[Dict[str, Any]]) -> List[RiskItem]:
    """Convert lint errors to RiskItem objects.
    
    Args:
        lint_errors: List of lint error dictionaries with keys: file, line, message, severity, code.
    
    Returns:
        List of RiskItem objects with risk_type=syntax.
    """
    risk_items = []
    for error in lint_errors:
        try:
            file_path = error.get("file", "")
            line_number = error.get("line", 1)
            message = error.get("message", "")
            severity = error.get("severity", "error")
            code = error.get("code", "")
            
            # Build description with error code if available
            if code:
                description = f"[{code}] {message}"
            else:
                description = message
            
            # Convert single line number to range format [line, line]
            line_num = int(line_number) if line_number else 1
            risk_item = RiskItem(
                risk_type=RiskType.SYNTAX,
                file_path=file_path,
                line_number=[line_num, line_num],  # Must be [start, end] format
                description=description,
                confidence=0.8,  # Lint errors have high confidence from static analysis
                severity=severity,
                suggestion=None  # Expert will provide suggestions
            )
            risk_items.append(risk_item)
        except Exception as e:
            logger.warning(f"Failed to convert lint error to RiskItem: {e}, error: {error}")
            continue
    
    return risk_items


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
