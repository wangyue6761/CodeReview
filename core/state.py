"""State definitions for the LangGraph workflow.

This module defines the ReviewState TypedDict that is passed between nodes
in the LangGraph workflow.

重构说明：
- 添加 messages 字段，使用 Annotated[list, add_messages] 来管理消息历史
- 这是 LangGraph 标准做法，用于在节点间传递对话历史
"""

from typing import TypedDict, List, Dict, Any, Optional, Annotated, Literal
from enum import Enum
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
import operator


class RiskType(str, Enum):
    """Types of risks that can be identified in code review.
    
    风险分类体系：
    - NULL_SAFETY: 空值陷阱与边界防御 (Null Safety & Boundary Defense)
    - CONCURRENCY: 并发竞争与异步时序 (Concurrency & Async Timing)
    - SECURITY: 安全漏洞与敏感数据 (Security & Authorization)
    - BUSINESS_INTENT: 业务意图与功能对齐 (Business Intent & Functional Alignment)
    - LIFECYCLE: 生命周期与状态副作用 (Lifecycle, State & Side Effects)
    """
    NULL_SAFETY = "null_safety"  # 第二类：空值陷阱与边界防御
    CONCURRENCY = "concurrency"  # 第三类：并发竞争与异步时序
    SECURITY = "security"  # 第四类：安全漏洞与敏感数据
    BUSINESS_INTENT = "business_intent"  # 第五类：业务意图与功能对齐
    LIFECYCLE = "lifecycle"  # 第六类：生命周期与状态副作用


class RiskItem(BaseModel):
    """Represents a single risk item identified during code review.
    
    Attributes:
        risk_type: The type of risk (security, performance, etc.).
        file_path: Path to the file where the risk was identified.
        line_number: Line number where the risk occurs (1-indexed).
        description: Description of the risk.
        confidence: Confidence score (0.0 to 1.0).
        severity: Severity level ("error", "warning", "info").
        suggestion: Optional suggestion for fixing the risk.
    """
    risk_type: RiskType = Field(..., description="Type of risk")
    file_path: str = Field(..., description="File path where risk was identified")
    line_number: int = Field(..., description="Line number (1-indexed)")
    description: str = Field(..., description="Description of the risk")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence score")
    severity: str = Field(default="warning", description="Severity: error, warning, or info")
    suggestion: Optional[str] = Field(default=None, description="Optional suggestion for fixing")


class FileAnalysis(BaseModel):
    """Represents the analysis result for a single file.
    
    Attributes:
        file_path: Path to the analyzed file.
        intent_summary: Summary of the file's purpose and changes.
        potential_risks: List of potential risks identified.
        complexity_score: Optional complexity score (0-100).
    """
    file_path: str = Field(..., description="Path to the analyzed file")
    intent_summary: str = Field(..., description="Summary of file's purpose and changes")
    potential_risks: List[RiskItem] = Field(default_factory=list, description="Potential risks identified")
    complexity_score: Optional[float] = Field(default=None, ge=0.0, le=100.0, description="Complexity score")


class WorkListResponse(BaseModel):
    """Manager node 的输出响应模型。
    
    重构说明：
    - 使用 PydanticOutputParser 解析 Manager 节点的输出
    - 确保类型安全和自动验证
    
    Attributes:
        work_list: List of risk items that need expert review.
    """
    work_list: List[RiskItem] = Field(..., description="List of risk items for expert review")


class ReviewState(TypedDict, total=False):
    """State object passed through the LangGraph workflow.
    
    重构说明：
    1. 添加 messages 字段：使用 Annotated[list, add_messages] 来管理消息历史
       这是 LangGraph 标准做法，用于在节点间传递对话历史，支持消息追加而非重写
    2. 保持向后兼容：保留原有的业务字段，同时添加标准化的消息管理
    
    This TypedDict defines all possible state keys that can be used in the
    code review workflow. Keys are optional (total=False) to allow incremental
    state updates.
    
    Attributes:
        messages: 消息历史列表，使用 add_messages 进行追加操作（LangGraph 标准）
        diff_context: The raw Git diff string from the PR.
        changed_files: List of file paths that were changed in the PR.
        file_analyses: Map-Reduce result for intent analysis (List of FileAnalysis).
        work_list: Manager's output, tasks for experts (List of RiskItem).
        expert_tasks: Grouped work_list items by RiskType for parallel execution.
        expert_results: Store results from each expert group.
        confirmed_issues: Final confirmed issues (uses operator.add for accumulation).
        final_report: Final review report string.
        repo_map_summary: A summary of the repository structure (from RepoMap asset).
        lint_errors: List of linting errors from pre-agent syntax checking.
        metadata: Optional dictionary for storing additional workflow metadata.
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

