"""LangChain 工具定义。

将 file_tools 和 grep_tool 转换为可供 LangGraph 使用的 function call。
使用闭包注入上下文（workspace_root, asset_key），避免重复代码。
"""

from pathlib import Path
from typing import Optional, Dict, Any, List
from langchain_core.tools import tool, BaseTool
from dao.factory import get_storage


def create_tools_with_context(
    workspace_root: Optional[Path] = None,
    asset_key: Optional[str] = None
) -> List[BaseTool]:
    """创建带上下文的 LangChain 工具列表。
    
    通过闭包注入 workspace_root 和 asset_key，创建可直接用于 LangGraph 的工具。
    
    Args:
        workspace_root: 工作区根目录路径。
        asset_key: 仓库映射的资产键。
    
    Returns:
        LangChain 工具列表：fetch_repo_map, read_file, run_grep。
    """
    workspace_root_str = str(workspace_root) if workspace_root else None
    
    @tool
    async def fetch_repo_map() -> Dict[str, Any]:
        """获取仓库结构映射。
        
        从存储层加载仓库映射资产，返回项目结构的摘要。
        
        Returns:
            包含 summary, file_count, files, source_path, error 的字典。
        """
        try:
            storage = get_storage()
            await storage.connect()
            
            key = asset_key if asset_key else "repo_map"
            repo_map_data = await storage.load("assets", key)
            
            if repo_map_data is None:
                return {
                    "summary": "Repository map not found. Please build the repository map first.",
                    "file_count": 0,
                    "files": [],
                    "error": "Repository map not found in storage"
                }
            
            file_tree = repo_map_data.get("file_tree", "No file tree available")
            file_count = repo_map_data.get("file_count", 0)
            files = repo_map_data.get("files", [])
            source_path = repo_map_data.get("source_path", "unknown")
            
            files_preview = files[:50]
            files_display = "\n".join(f"  - {f}" for f in files_preview)
            if len(files) > 50:
                files_display += f"\n  ... and {len(files) - 50} more files"
            
            summary = f"""Repository Structure Summary:
                    Source Path: {source_path}
                    Total Files: {file_count}

                    File Tree:
                    {file_tree}

                    Key Files (first 50):
                    {files_display}
                    """
            
            return {
                "summary": summary,
                "file_count": file_count,
                "files": files_preview,
                "all_files": files,
                "source_path": source_path,
                "error": None
            }
        except Exception as e:
            return {
                "summary": "",
                "file_count": 0,
                "files": [],
                "error": f"Error fetching repository map: {str(e)}"
            }
    
    @tool
    async def read_file(
        file_path: str,
        max_lines: Optional[int] = None,
        encoding: str = "utf-8"
    ) -> Dict[str, Any]:
        """读取文件内容。
        
        Args:
            file_path: 文件路径（相对于工作区根目录或绝对路径）。
            max_lines: 可选的最大行数限制。
            encoding: 文件编码，默认为 'utf-8'。
        
        Returns:
            包含 content, file_path, line_count, encoding, error 的字典。
        """
        try:
            file_path_obj = Path(file_path)
            if not file_path_obj.is_absolute():
                workspace = Path(workspace_root_str) if workspace_root_str else Path.cwd()
                file_path_obj = workspace / file_path_obj
            
            if not file_path_obj.exists():
                return {
                    "content": "",
                    "file_path": str(file_path_obj),
                    "line_count": 0,
                    "encoding": encoding,
                    "error": f"File not found: {file_path_obj}"
                }
            
            with open(file_path_obj, "r", encoding=encoding) as f:
                lines = f.readlines()
                line_count = len(lines)
                
                if max_lines and line_count > max_lines:
                    content = "".join(lines[:max_lines])
                    content += f"\n... (truncated, {line_count - max_lines} more lines)"
                else:
                    content = "".join(lines)
            
            return {
                "content": content,
                "file_path": str(file_path_obj),
                "line_count": line_count,
                "encoding": encoding,
                "error": None
            }
        except Exception as e:
            return {
                "content": "",
                "file_path": str(file_path),
                "line_count": 0,
                "encoding": encoding,
                "error": f"Error reading file: {str(e)}"
            }
    
    @tool
    async def run_grep(
        pattern: str,
        is_regex: bool = False,
        case_sensitive: bool = True,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        context_lines: int = 10,
        max_results: int = 50,
    ) -> str:
        """在代码库中搜索字符串或正则表达式。
        
        Args:
            pattern: 搜索模式（字符串或正则表达式）。
            is_regex: 是否将模式视为正则表达式。默认为 False。
            case_sensitive: 搜索是否区分大小写。默认为 True。
            include_patterns: 要包含的文件名模式列表。默认为 ["*"]。
            exclude_patterns: 要排除的文件模式列表。默认为空列表。
            context_lines: 每个匹配项前后的上下文行数。默认为 10。
            max_results: 返回的最大匹配块数。默认为 50。
        
        Returns:
            包含所有匹配项的格式化字符串，包含文件路径、匹配行和上下文。
        """
        from tools.grep_tool import _grep_internal
        import os
        
        repo_root = workspace_root_str if workspace_root_str else (os.getenv("REPO_ROOT") or os.getcwd())
        
        if include_patterns is None:
            include_patterns = ["*"]
        if exclude_patterns is None:
            exclude_patterns = []
        
        return _grep_internal(
            repo_root=repo_root,
            pattern=pattern,
            is_regex=is_regex,
            case_sensitive=case_sensitive,
            include_patterns=tuple(include_patterns),
            exclude_patterns=tuple(exclude_patterns),
            context_lines=context_lines,
            max_results=max_results,
        )
    
    # return [fetch_repo_map, read_file, run_grep]
    return [read_file, run_grep]

