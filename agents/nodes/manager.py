"""代码审查工作流的 Manager 节点。

接收文件分析结果，生成专家任务列表，并按风险类型分组以支持并行执行。
使用 LCEL 语法和 PydanticOutputParser。
"""

import logging
import re
from bisect import bisect_left
from typing import Dict, Any, List, Optional, Tuple
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.language_models import BaseChatModel
from core.state import ReviewState, RiskItem, RiskType, WorkListResponse
from agents.prompts import render_prompt_template
from collections import defaultdict
from util.runtime_utils import elapsed_tag
from util.diff_utils import parse_diff_with_line_numbers

logger = logging.getLogger(__name__)


def _normalize_path(p: str) -> str:
    s = (p or "").strip()
    if s.startswith("a/") or s.startswith("b/"):
        s = s[2:]
    if s.startswith("/"):
        s = s[1:]
    return s


def _tokenize(s: str) -> set[str]:
    if not s:
        return set()
    parts = re.split(r"[^a-zA-Z0-9_]+", s.lower())
    return {p for p in parts if p}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    if not union:
        return 0.0
    return len(inter) / len(union)


def _is_anchored_to_changes(
    changed_lines_sorted: List[int],
    line_range: Tuple[int, int],
    window: int,
) -> bool:
    """Return True if any changed line falls within [start-window, end+window]."""
    if not changed_lines_sorted:
        return False
    start, end = line_range
    lo = max(1, int(start) - int(window))
    hi = int(end) + int(window)
    idx = bisect_left(changed_lines_sorted, lo)
    return idx < len(changed_lines_sorted) and changed_lines_sorted[idx] <= hi


def _severity_rank(sev: str) -> int:
    s = (sev or "").strip().lower()
    if s == "error":
        return 3
    if s == "warning":
        return 2
    return 1


def _merge_near_duplicates(
    items: List[RiskItem],
    *,
    line_window: int,
    jaccard_threshold: float,
) -> List[RiskItem]:
    """Merge near-duplicate risks within the same file & risk_type."""
    if not items:
        return []

    by_key: Dict[Tuple[str, RiskType], List[RiskItem]] = defaultdict(list)
    for it in items:
        by_key[(it.file_path, it.risk_type)].append(it)

    merged: List[RiskItem] = []
    for (_, _), group in by_key.items():
        ordered = sorted(group, key=lambda x: (x.line_number[0], x.line_number[1], -x.confidence))
        cur: Optional[RiskItem] = None
        cur_tokens: set[str] = set()
        cur_descs: List[str] = []
        for it in ordered:
            if cur is None:
                cur = it
                cur_tokens = _tokenize(it.description)
                cur_descs = [it.description]
                continue

            near = abs(it.line_number[0] - cur.line_number[1]) <= int(line_window)
            sim = _jaccard(cur_tokens, _tokenize(it.description))
            if near and sim >= float(jaccard_threshold):
                # Merge: keep all original descriptions verbatim, expand line range.
                start = min(cur.line_number[0], it.line_number[0])
                end = max(cur.line_number[1], it.line_number[1])
                cur_descs.append(it.description)
                cur = RiskItem(
                    risk_type=cur.risk_type,
                    file_path=cur.file_path,
                    line_number=(start, end),
                    description="\n\n".join(cur_descs),
                    confidence=max(cur.confidence, it.confidence),
                    severity=cur.severity if _severity_rank(cur.severity) >= _severity_rank(it.severity) else it.severity,
                    suggestion=None,
                )
                cur_tokens = _tokenize(cur.description)
            else:
                merged.append(cur)
                cur = it
                cur_tokens = _tokenize(it.description)
                cur_descs = [it.description]
        if cur is not None:
            merged.append(cur)

    return merged


def _budget_work_items(
    items: List[RiskItem],
    *,
    max_total: int,
    max_per_file: int,
    max_per_type: Dict[str, int],
    type_weights: Dict[str, float],
    severity_weights: Dict[str, float],
) -> List[RiskItem]:
    if not items:
        return []

    def tw(it: RiskItem) -> float:
        return float(type_weights.get(it.risk_type.value, 1.0))

    def sw(it: RiskItem) -> float:
        return float(severity_weights.get((it.severity or "warning").lower(), 1.0))

    scored = sorted(items, key=lambda it: (-(it.confidence * tw(it) * sw(it)), -_severity_rank(it.severity), it.file_path, it.line_number))

    selected: List[RiskItem] = []
    per_file: Dict[str, int] = defaultdict(int)
    per_type: Dict[str, int] = defaultdict(int)
    for it in scored:
        if len(selected) >= int(max_total):
            break
        if per_file[it.file_path] >= int(max_per_file):
            continue
        cap_t = max_per_type.get(it.risk_type.value)
        if cap_t is not None and per_type[it.risk_type.value] >= int(cap_t):
            continue
        selected.append(it)
        per_file[it.file_path] += 1
        per_type[it.risk_type.value] += 1
    return selected


async def manager_node(state: ReviewState) -> Dict[str, Any]:
    """Manager 节点：生成任务列表并按风险类型分组。
    
    Returns:
        包含 'work_list' 和 'expert_tasks' 键的字典。
    """
    print("\n" + "="*80)
    meta = state.get("metadata") or {}
    print(f"👔 [节点2] Manager - 生成任务列表并分组 ({elapsed_tag(meta)})")
    print("="*80)
    
    # 获取 LLM（从 metadata）
    llm: BaseChatModel = state.get("metadata", {}).get("llm")
    if not llm:
        logger.error("LLM not found in metadata")
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
        config = state.get("metadata", {}).get("config")
        window = int(getattr(getattr(config, "system", None), "manager_anchor_window", 5) or 5)
        drop_unanchored = bool(getattr(getattr(config, "system", None), "manager_drop_unanchored", True))
        unanchored_cap = float(getattr(getattr(config, "system", None), "manager_unanchored_confidence", 0.2) or 0.2)

        # Build changed-lines map from diff context for anchor checking.
        contexts = parse_diff_with_line_numbers(diff_context or "")
        changed_lines_by_file: Dict[str, List[int]] = {}
        for fp, ctx in contexts.items():
            try:
                lines = sorted(set(getattr(ctx, "added_lines", set()) | getattr(ctx, "modified_lines", set())))
            except Exception:
                lines = []
            changed_lines_by_file[_normalize_path(fp)] = lines

        # Collect all risks from intent analysis.
        raw_items: List[RiskItem] = []
        for file_analysis in file_analyses:
            raw_items.extend(list(file_analysis.potential_risks or []))

        # Add lint tasks (high-signal).
        lint_errors = state.get("lint_errors", [])
        if lint_errors:
            lint_risk_items = _convert_lint_errors_to_risk_items(lint_errors)
            raw_items.extend(lint_risk_items)
            print(f"  📋 添加语法分析任务: {len(lint_risk_items)} 个")

        # Anchor hard-filtering: drop/cap items not near changed lines.
        anchored_items: List[RiskItem] = []
        dropped = 0
        capped = 0
        for it in raw_items:
            # Syntax/static errors are already evidence-based and should not be dropped by anchoring.
            if it.risk_type == RiskType.SYNTAX_STATIC_ERRORS:
                anchored_items.append(it)
                continue

            fp = _normalize_path(it.file_path)
            changed = changed_lines_by_file.get(fp, [])
            is_anchored = _is_anchored_to_changes(changed, it.line_number, window)
            if is_anchored:
                anchored_items.append(it)
                continue

            if drop_unanchored:
                dropped += 1
                continue

            capped += 1
            anchored_items.append(
                RiskItem(
                    risk_type=it.risk_type,
                    file_path=it.file_path,
                    line_number=it.line_number,
                    description=it.description,
                    confidence=min(it.confidence, unanchored_cap),
                    severity=it.severity,
                    suggestion=None,
                )
            )

        if dropped or capped:
            print(f"  🧹 锚点过滤: 丢弃 {dropped} 条, 降置信度 {capped} 条 (window=±{window})")

        # Merge near-duplicates after anchoring.
        merge_line_window = int(getattr(getattr(config, "system", None), "manager_merge_line_window", 5) or 5)
        merge_jaccard = float(getattr(getattr(config, "system", None), "manager_merge_jaccard", 0.75) or 0.75)
        merged_items = _merge_near_duplicates(
            anchored_items,
            line_window=merge_line_window,
            jaccard_threshold=merge_jaccard,
        )

        # Budgeting / prioritization.
        max_total = int(getattr(getattr(config, "system", None), "manager_max_work_items_total", 30) or 30)
        max_per_file = int(getattr(getattr(config, "system", None), "manager_max_items_per_file", 6) or 6)
        max_per_type = dict(getattr(getattr(config, "system", None), "manager_max_items_per_risk_type", {}) or {})
        type_weights = dict(getattr(getattr(config, "system", None), "manager_risk_type_weights", {}) or {})
        sev_weights = dict(getattr(getattr(config, "system", None), "manager_severity_weights", {}) or {})

        # Defaults tuned to reduce "Robustness" noise while keeping high-signal types.
        if not type_weights:
            type_weights = {
                RiskType.SYNTAX_STATIC_ERRORS.value: 1.4,
                RiskType.CONCURRENCY_TIMING_CORRECTNESS.value: 1.3,
                RiskType.AUTHORIZATION_DATA_EXPOSURE.value: 1.3,
                RiskType.LIFECYCLE_STATE_CONSISTENCY.value: 1.1,
                RiskType.INTENT_SEMANTIC_CONSISTENCY.value: 1.0,
                RiskType.ROBUSTNESS_BOUNDARY_CONDITIONS.value: 0.7,
            }
        if not sev_weights:
            sev_weights = {"error": 1.3, "warning": 1.0, "info": 0.7}

        work_list = _budget_work_items(
            merged_items,
            max_total=max_total,
            max_per_file=max_per_file,
            max_per_type=max_per_type,
            type_weights=type_weights,
            severity_weights=sev_weights,
        )

        # Group work_list by risk_type
        expert_tasks = _group_tasks_by_risk_type(work_list)

        print(f"  ✅ worklist")

        print(f"  ✅ Manager 完成! ({elapsed_tag(meta)})")
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
    """格式化文件分析结果用于提示词。"""
    summaries = []
    for analysis in file_analyses:
        summaries.append(
            f"File: {analysis.file_path}\n"
            f"Intent: {analysis.intent_summary}\n"
            f"Potential Risks: {len(analysis.potential_risks)}\n"
        )
    return "\n".join(summaries)

def _format_work_list(work_list: List[Any]) -> str:
    """格式化任务列表用于提示词。"""
    summaries = []
    for w in work_list:
        summaries.append(
            f"File: {w.file_path}\n"
            f"Line Number: {w.line_number}\n"
            f"Confidence: {w.confidence}\n"
            f"Risk Type: {w.risk_type}\n"
            f"Description: {w.description}\n"
        )
    return "\n".join(summaries)

def _get_expanded_format_instructions(parser: PydanticOutputParser) -> str:
    """生成扩展的格式说明（包含嵌套模型结构）。"""
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
    """将 lint 错误转换为 RiskItem 对象（risk_type=Syntax_Static_Errors）。"""
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
                risk_type=RiskType.SYNTAX_STATIC_ERRORS,
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
    """按风险类型分组任务列表。"""
    grouped = {}
    for item in work_list:
        risk_type_str = item.risk_type.value
        if risk_type_str not in grouped:
            grouped[risk_type_str] = []
        grouped[risk_type_str].append(item)
    
    return grouped
