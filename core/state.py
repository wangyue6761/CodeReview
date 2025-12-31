"""LangGraph 工作流状态定义。

定义 ReviewState TypedDict，用于在节点间传递状态。
"""

from typing import TypedDict, List, Dict, Any, Optional, Annotated, Literal, Tuple
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from langchain_core.messages import BaseMessage, AnyMessage
from langgraph.graph.message import add_messages
import operator


class RiskType(str, Enum):
    """代码审查风险类型。"""
    NULL_SAFETY = "null_safety"  # 第二类：空值陷阱与边界防御
    CONCURRENCY = "concurrency"  # 第三类：并发竞争与异步时序
    SECURITY = "security"  # 第四类：安全漏洞与敏感数据
    BUSINESS_INTENT = "business_intent"  # 第五类：业务意图与功能对齐
    LIFECYCLE = "lifecycle"  # 第六类：生命周期与状态副作用
    SYNTAX = "syntax"  # 语法与静态分析


class RiskItem(BaseModel):
    """代码审查中识别的单个风险项。
    
    line_number: 行号范围 [start, end]，从 1 开始。单行问题使用 [line, line]。
    """
    risk_type: RiskType = Field(..., description="Type of risk")
    file_path: str = Field(..., description="File path where risk was identified")
    line_number: Tuple[int, int] = Field(..., description="Line number range (start_line, end_line)")
    description: str = Field(..., description="Description of the risk")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence score")
    severity: str = Field(default="warning", description="Severity: error, warning, or info")
    suggestion: Optional[str] = Field(default=None, description="Optional suggestion for fixing")
    
    @field_validator('line_number', mode='before')
    @classmethod
    def normalize_line_number(cls, v: Any) -> Tuple[int, int]:
        """规范化行号为元组 (start_line, end_line)。
        
        要求：必须是包含 2 个整数的列表/元组 [start, end]。
        
        Raises:
            ValueError: 输入格式不正确。
        """
        if isinstance(v, (list, tuple)):
            if len(v) == 2:
                start, end = int(v[0]), int(v[1])
                if start > end:
                    raise ValueError(f"start_line ({start}) must be <= end_line ({end})")
                if start < 1:
                    raise ValueError(f"start_line ({start}) must be >= 1 (1-indexed)")
                return (start, end)
            else:
                raise ValueError(
                    f"line_number must be a list/tuple of exactly 2 integers [start, end], "
                    f"got {len(v)} element(s): {v}"
                )
        elif isinstance(v, int):
            raise ValueError(
                f"line_number must be a list/tuple of 2 integers [start, end], "
                f"not a single integer. For single-line issues, use [line, line]. Got: {v}"
            )
        else:
            raise ValueError(
                f"line_number must be a list/tuple of 2 integers [start, end], "
                f"got {type(v).__name__}: {v}"
            )


class FileAnalysis(BaseModel):
    """单个文件的分析结果。"""
    file_path: str = Field(..., description="Path to the analyzed file")
    intent_summary: str = Field(..., description="Summary of file's purpose and changes")
    potential_risks: List[RiskItem] = Field(default_factory=list, description="Potential risks identified")
    complexity_score: Optional[float] = Field(default=None, ge=0.0, le=100.0, description="Complexity score")


class WorkListResponse(BaseModel):
    """Manager 节点的输出响应模型。"""
    work_list: List[RiskItem] = Field(..., description="List of risk items for expert review")


class ReviewState(TypedDict, total=False):
    """LangGraph 工作流状态对象。
    
    所有键都是可选的（total=False），支持增量更新。
    """
    
    # LangGraph 标准：消息历史管理（必须）
    # 使用 Annotated[list, add_messages] 确保消息追加而非重写
    messages: Annotated[List[BaseMessage], add_messages]
    
    # Inputs
    diff_context: str
    changed_files: List[str]
    
    # Intermediate States
    file_analyses: List[Dict[str, Any]]  # Map-Reduce result for intent analysis (FileAnalysis as dict)
    work_list: List[Dict[str, Any]]  # Manager's output, tasks for experts (RiskItem as dict)
    
    # Dynamic State for Parallel Execution of Experts
    expert_tasks: Dict[str, List[Dict[str, Any]]]  # Grouped work_list items by RiskType (RiskItem as dict)
    expert_results: Dict[str, List[Dict[str, Any]]]  # Store results from each expert group (RiskItem as dict)
    
    # Outputs
    confirmed_issues: Annotated[List[Dict[str, Any]], operator.add]  # Accumulated confirmed issues (RiskItem as dict)
    final_report: str
    
    # Legacy/Additional fields
    repo_map_summary: str
    lint_errors: List[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]]


class ExpertState(TypedDict):
    """专家子图状态。
    
    用于 LangGraph 专家分析子图的状态管理。
    
    Attributes:
        messages: 消息历史（LangGraph 标准）。
        risk_context: 待分析的风险项（输入）。
        final_result: 最终验证结果（输出，JSON 字典）。
    """
    messages: Annotated[List[AnyMessage], add_messages]
    risk_context: RiskItem  # 输入：待分析的风险项
    final_result: Optional[dict]  # 输出：最终验证结果(JSON)

