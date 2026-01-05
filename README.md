# AI 代码审查系统

基于多智能体的代码审查系统，使用静态分析工具和 LLM 智能体分析 Git PR 差异。系统采用多智能体工作流模式，通过意图分析、任务管理和专家执行等节点，生成全面的代码审查报告。

## 架构

系统采用模块化、分层架构，具有可扩展的 DAO（数据访问对象）层：

```
dao/             # DAO 层：可扩展的存储后端（文件、SQL、NoSQL 就绪）
assets/          # 资产层：代码分析和索引（AST、RepoMap、CPG）
tools/           # 工具层：MCP 兼容工具，封装资产查询
core/            # 核心层：配置、LLM 客户端和共享状态
agents/          # 智能体层：基于 LangGraph 的多智能体工作流
external_tools/  # 外部工具：语法检查器（ruff、biome、go vet、pmd）
main.py          # 入口点
log/             # 日志目录：智能体观察和工具调用日志
```

### 核心组件

- **DAO 层**：可扩展的存储抽象，支持基于文件的存储（MVP），接口已就绪，可迁移到 SQL/NoSQL 后端
- **资产**：代码分析产物（RepoMap、AST、CPG），通过 DAO 持久化
- **工具**：MCP 兼容工具（FetchRepoMapTool、ReadFileTool），智能体可使用
- **多智能体工作流**：包含 4 个节点的流水线工作流
  - **Intent Analysis**：并行分析变更文件的意图
  - **Manager**：生成任务列表并按风险类型分组
  - **Expert Execution**：并行执行专家组任务（并发控制）
  - **Reporter**：生成最终审查报告
- **语法检查器**：支持 Python（ruff）、TypeScript/JavaScript（biome）、Go（go vet）和 Java（pmd）的静态分析

## 技术栈

- **Python 3.10+**
- **LangGraph**：使用 StateGraph 和 Nodes 进行智能体编排
- **Pydantic v2**：所有智能体 I/O 和资产模式的数据验证
- **LangChain Core**：用于提示模板、工具和消息处理
- **unidiff**：Git diff 解析和行号映射
- **Tree-sitter**：代码解析（计划在后续版本中实现）

## 安装

1. 安装依赖：
```bash
pip install -r requirements.txt
```

2. 对于 OpenAI 或 DeepSeek 提供商（可选）：
```bash
pip install openai
```

3. 对于 YAML 配置文件支持（可选）：
```bash
pip install pyyaml
```

4. 安装外部语法检查工具（可选，用于静态分析）：
   
   **Python 检查器（Ruff）：**
   ```bash
   # 使用 pip 安装
   pip install ruff
   
   # 或使用 pipx（推荐，避免污染全局环境）
   pipx install ruff
   ```
   
   **TypeScript/JavaScript 检查器（Biome）：**
   ```bash
   # 使用 npm 全局安装
   npm install -g @biomejs/biome
   
   # 或使用 Homebrew (macOS)
   brew install biome
   
   # 或使用 Cargo (Rust 工具链)
   cargo install --locked biome
   ```
   
   **Go 检查器（go vet）：**
   ```bash
   # go vet 是 Go 标准库的一部分，随 Go 一起安装，无需额外安装
   # 只需确保已安装 Go 1.x 或更高版本
   go version  # 验证 Go 是否已安装
   
   # go vet 是官方工具，速度快（秒级返回），无需配置文件
   ```
   
   **Java 检查器（PMD）：**
   ```bash
   # 使用 Homebrew (macOS)
   brew install pmd
   
   # 或手动安装
   # 下载最新版本
   wget https://github.com/pmd/pmd/releases/latest/download/pmd-bin-7.x.x.zip
   unzip pmd-bin-7.x.x.zip
   sudo mv pmd-bin-7.x.x /opt/pmd
   sudo ln -s /opt/pmd/bin/run.sh /usr/local/bin/pmd
   ```
   
   **验证安装：**
   ```bash
   ruff --version && biome --version && go version && pmd --version
   ```
   
   注意：如果工具未安装，系统会优雅降级（跳过检查并显示警告），不会导致系统崩溃。

5. 配置 DeepSeek（如使用）：
5. 配置 LLM（推荐使用环境变量，避免把 key 写进配置文件）：
   - 通用环境变量（最高优先级）：
     ```bash
     export LLM_PROVIDER="zhipuai"     # openai / deepseek / zhipuai
     export LLM_MODEL="glm-4.6"        # 按 provider 选择
     export LLM_API_KEY="your-key"
     ```
   - provider 专用环境变量（当 `LLM_API_KEY` 未设置时使用）：
     ```bash
     export DEEPSEEK_API_KEY="your-deepseek-key"   # provider=deepseek
     export ZHIPUAI_API_KEY="your-zhipuai-key"     # provider=zhipuai
     ```
   - 或在 `config.yaml` 中配置（不推荐直接提交 key）：
     ```yaml
     llm:
       provider: "zhipuai"  # openai / deepseek / zhipuai
       model: "glm-4.6"
       api_key: null
       temperature: 0
     ```

## 使用方法

### 命令行接口

使用 Git 分支比较模式：

```bash
# 比较 feature-x 分支与 main
python main.py --repo ./project --base main --head feature-x

# 比较当前 HEAD 与 main
python main.py --repo ./project --base main --head HEAD
```

### 命令行选项

- `--repo`（必需）：要审查的仓库路径
- `--base`（必需）：Git diff 的目标分支（如 'main', 'master'）
- `--head`（必需）：Git diff 的源分支或提交（如 'feature-x', 'HEAD'）
- `--output`：保存审查结果 JSON 的路径（默认：review_results.json）

### 示例：使用 Sentry 仓库测试

```bash
python main.py \
  --repo /Users/wangyue/Code/CodeReviewData/ReviewDataset/sentry-greptile \
  --base performance-optimization-baseline \
  --head performance-enhancement-complete
```

### 编程式使用

```python
import asyncio
from core.config import Config
from agents.workflow import run_multi_agent_workflow

async def review_code():
    config = Config.load_default()
    
    pr_diff = """your git diff here"""
    changed_files = ["file1.py", "file2.py"]
    
    results = await run_multi_agent_workflow(
        diff_context=pr_diff,
        changed_files=changed_files,
        config=config
    )
    
    print(f"发现 {len(results['confirmed_issues'])} 个问题")
    for issue in results['confirmed_issues']:
        print(f"{issue['file_path']}:{issue['line_number']} - {issue['description']}")

asyncio.run(review_code())
```

## 工作流

系统使用多智能体工作流，遵循以下流程：

1. **初始化存储**：初始化 DAO 层（MVP 使用基于文件的存储）
2. **构建资产**：如需要，构建并持久化仓库地图
3. **语法检查**：对变更文件执行静态分析（ruff、biome、go vet、pmd）
4. **意图分析**：并行分析所有变更文件的意图（Map-Reduce 模式）
5. **任务管理**：Manager 节点生成任务列表并按风险类型分组
6. **专家执行**：并行执行专家组任务，使用并发控制
7. **报告生成**：Reporter 节点生成最终审查报告
8. **日志记录**：所有观察和工具调用自动记录到日志文件

### 风险类型

系统识别以下 6 种风险类型：

- **ROBUSTNESS_BOUNDARY_CONDITIONS** (`Robustness_Boundary_Conditions`)：健壮性与边界条件
- **CONCURRENCY_TIMING_CORRECTNESS** (`Concurrency_Timing_Correctness`)：并发与时序正确性
- **AUTHORIZATION_DATA_EXPOSURE** (`Authorization_Data_Exposure`)：鉴权与数据暴露风险
- **INTENT_SEMANTIC_CONSISTENCY** (`Intent_Semantic_Consistency`)：需求意图与语义一致性
- **LIFECYCLE_STATE_CONSISTENCY** (`Lifecycle_State_Consistency`)：生命周期与状态一致性
- **SYNTAX_STATIC_ERRORS** (`Syntax_Static_Errors`)：语法与静态错误

### 智能体自主性

智能体具有完全自主性：
- 如不需要可跳过工具调用
- 重试失败的工具调用（带失败跟踪）
- 接近迭代限制时提供回退审查
- 基于上下文和先前观察做出决策

## 配置

编辑 `config.yaml` 或通过环境变量配置：

```yaml
llm:
  provider: "deepseek"  # 选项: "openai", "deepseek", "zhipuai"
  model: "deepseek-chat"  # deepseek-chat / glm-4.6 / gpt-4o 等
  api_key: null  # 可通过 DEEPSEEK_API_KEY 或 LLM_API_KEY 环境变量设置
  base_url: "https://api.deepseek.com"
  temperature: 0.7

system:
  workspace_root: "."
  assets_dir: "assets_cache"
  timeout_seconds: 600  # 10 分钟
  max_concurrent_llm_requests: 10

  # ===== Noise control: file path filtering =====
  # 默认排除锁文件/生成物/二进制/媒体/日志等，可用 include/exclude 覆盖
  path_filter_enabled: true
  path_filter_include_globs: []
  path_filter_exclude_globs: []

  # ===== Reporter threshold =====
  confidence_threshold: 0.6
  # 可选：按风险类型设置更严格阈值（例如降低 Robustness 误报）
  # confidence_threshold_by_risk_type:
  #   Robustness_Boundary_Conditions: 0.75

  # ===== Manager gating / budgeting =====
  manager_anchor_window: 5
  manager_drop_unanchored: true
  manager_unanchored_confidence: 0.2
  manager_max_work_items_total: 15
  manager_max_items_per_file: 3
  manager_max_items_per_risk_type: {}
  manager_risk_type_weights: {}
  manager_severity_weights: {}
  manager_merge_line_window: 5
  manager_merge_jaccard: 0.75

  # ===== Expert calibration =====
  expert_confidence_clamp_on_budget_stop: 0.55
```

环境变量优先级：`LLM_API_KEY` > provider 专用 key（如 `DEEPSEEK_API_KEY` / `ZHIPUAI_API_KEY`）

## 功能特性

### 核心功能

- ✅ **多智能体工作流**：4 节点流水线（意图分析、管理、专家执行、报告）
- ✅ **降噪与聚焦**：默认过滤低信号文件（锁文件/生成物/二进制/媒体/日志等）；Manager 强制锚定到变更行±N，并做预算分配与去重合并
- ✅ **置信度校准**：在专家触发工具预算/轮次熔断时自动压低置信度上限，避免“无证据推断”进入 confirmed
- ✅ **可扩展 DAO 层**：基于文件的存储（MVP），接口已就绪，可迁移到 SQL/NoSQL/GraphDB 后端
- ✅ **资产管理**：RepoMap 构建器，自动 DAO 持久化（幂等构建）
- ✅ **MCP 兼容工具**：标准化工具接口（FetchRepoMapTool、ReadFileTool）
- ✅ **语法检查器**：支持 Python（ruff）、TypeScript/JavaScript（biome）、Go（go vet）和 Java（pmd），采用 Agent-Defined Config (ADC) 策略，强制使用内置配置
- ✅ **全面日志记录**：自动记录智能体观察和工具调用到结构化日志文件
- ✅ **多 LLM 提供商**：支持 OpenAI、DeepSeek 和 mock 提供商（用于测试）
- ✅ **并发控制**：使用 Semaphore 限制并发 LLM 请求数量
- ✅ **错误处理**：优雅降级，详细错误报告
- ✅ **类型安全**：完整的类型提示和 Pydantic v2 验证

### 日志记录

智能体观察和工具调用自动保存到：
```
log/
  └── {repo_name}/
      └── {model_name}/
          └── {timestamp}/
              ├── observations.log    # 智能体观察和工具调用
              └── expert_analyses.log # 专家分析结果
```

每个日志文件包含：
- 所有智能体观察（推理步骤）
- 所有工具调用（输入参数和结果）
- 专家分析结果
- 元数据（仓库、模型、时间戳）
注：终端输出会跳过/脱敏可能包含密钥的字段，避免 API key 泄漏到日志中。

## 项目结构

```
CodeReview/
├── dao/                    # 数据访问对象层
│   ├── base.py            # BaseStorageBackend 接口
│   ├── factory.py          # 存储工厂（单例模式）
│   └── backends/
│       └── local_file.py   # 基于文件的存储实现
├── assets/                 # 资产构建器
│   ├── base.py             # BaseAssetBuilder 接口
│   ├── registry.py         # 资产注册表
│   └── implementations/
│       └── repo_map.py     # RepoMap 构建器
├── tools/                  # MCP 兼容工具
│   ├── base.py             # BaseTool 接口
│   ├── repo_tools.py       # FetchRepoMapTool
│   ├── file_tools.py       # ReadFileTool
│   └── langchain_tools.py  # LangChain 标准工具
├── agents/                 # 智能体实现
│   ├── workflow.py         # 多智能体工作流
│   ├── prompts/            # 提示词模板
│   │   ├── intent_analysis.txt
│   │   ├── manager.txt
│   │   ├── reporter.txt
│   │   └── expert_*.txt    # 各专家提示词
│   └── nodes/              # 工作流节点
│       ├── intent_analysis.py  # 意图分析节点
│       ├── manager.py           # Manager 节点
│       ├── expert_execution.py  # 专家执行节点
│       └── reporter.py          # 报告生成节点
├── core/                   # 核心工具
│   ├── config.py           # 配置管理
│   ├── llm_factory.py      # LLM 工厂（openai/deepseek/zhipuai）
│   └── state.py            # LangGraph 状态定义
│   └── zhipuai_compat.py   # ZhipuAI 工具调用兼容层
├── external_tools/         # 外部工具
│   └── syntax_checker/     # 语法检查器
│       ├── base.py         # 检查器基类
│       ├── factory.py      # 检查器工厂
│       ├── config_loader.py # 配置加载器
│       └── implementations/
│           ├── python_ruff.py
│           ├── typescript_biome.py
│           ├── go_vet.py
│           └── java_pmd.py
├── util/                   # 工具函数
│   ├── arg_utils.py        # 参数验证
│   ├── git_utils.py        # Git 操作
│   ├── diff_utils.py       # Diff 解析
│   ├── file_utils.py       # 文件操作
│   ├── pr_utils.py         # PR 处理
│   └── logger.py           # 日志记录
├── log/                    # 智能体日志（自动生成）
├── .storage/               # DAO 存储目录（自动生成）
├── main.py                 # 入口点
├── config.yaml             # 配置文件示例
└── requirements.txt        # 依赖列表
```

## 未来增强

- [ ] Tree-sitter 集成用于 AST 分析
- [ ] 控制流图（CPG）生成
- [ ] SQL/NoSQL/GraphDB 存储后端
- [ ] 真实 GitHub API 集成
- [ ] 代码嵌入的向量存储
- [ ] 高级查询功能
- [ ] 审查结果的 Web UI
- [ ] CI/CD 集成
- [ ] 更多语言支持（Java、Rust 等）

## 开发

### 编码标准

项目遵循严格的编码标准：

- **类型提示**：所有函数和方法必须使用
- **文档字符串**：所有类和公共方法使用 Google 风格（中文）
- **异步 IO**：所有 IO 绑定操作使用 async/await
- **错误处理**：智能体永不崩溃；错误在结果中返回
- **依赖注入**：不硬编码依赖；使用 DI 模式
- **抽象接口**：所有主要组件使用 ABC 接口

### 设计原则

- **高内聚、低耦合**：模块化架构，清晰的边界
- **可扩展性**：易于添加新的存储后端、工具和智能体
- **幂等性**：资产构建和操作是幂等的
- **可观测性**：全面的日志记录用于调试和监控

### 测试

当前版本未内置 mock provider；运行测试需配置可用的 LLM provider/key（或在后续补充 mock provider 以便离线测试）。

## 许可证

[添加您的许可证]
