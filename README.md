# CPGBot - AI 代码审查系统

本系统构建了一套先进的多智能体代码审查框架，深度集成Code Property Graph（CPG）与多种主流静态分析工具（如Semgrep等），并协同大语言模型（LLM）智能体，对 Git Pull Request（PR）中的代码变更进行自动化、多维度、高精度的审查。本系统通过意图分析、任务管理和专家执行等节点，生成全面的代码审查报告，使用多智能体协同机制，实现了静态分析的深度与 LLM 的语义理解能力的优势互补，显著提升代码审查的准确性、覆盖度与智能化水平，适用于大规模、多语言的现代软件开发场景。该系统不仅实现了静态分析与 LLM 的深度融合，更通过多智能体自治架构、CPG 语义增强、工程级可观测性与可扩展数据层，构建了一个生产就绪、可审计、可维护的下一代代码审查平台。

## 交付指南

- 安装、配置与使用：`docs/DELIVERY_GUIDE.md`
- GitHub PAT Webhook 部署：`docs/deploy_github_pat.md`

## 架构


## 快速开始

### 安装依赖

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

### 从git上开始



### 从代码行开始
使用 Git 分支比较模式：

```bash
# 比较 feature-x 分支与 main
python main.py --repo ./project --base main --head feature-x

# 比较当前 HEAD 与 main
python main.py --repo ./project --base main --head HEAD
```

#### 命令行选项

- `--repo`（必需）：要审查的仓库路径
- `--base`（必需）：Git diff 的目标分支（如 'main', 'master'）
- `--head`（必需）：Git diff 的源分支或提交（如 'feature-x', 'HEAD'）
- `--output`：保存审查结果 JSON 的路径（默认：review_results.json）

#### 示例：使用 Sentry 仓库测试

```bash
python main.py \
  --repo /Users/wangyue/Code/CodeReviewData/ReviewDataset/sentry-greptile \
  --base performance-optimization-baseline \
  --head performance-enhancement-complete
```

## 🚀 系统核心优势&特点
本系统兼具深度（CPG + LLM）、广度（多工具 + 多语言）与可靠性（自主决策 + 风险验证），主要有以下三点优势&特点
- **多智能体自主决策架构**：采用四阶段自治工作流（意图分析 → 任务管理 → 专家执行 → 报告合成），各智能体基于上下文独立决策、动态协作，无需人工干预即可完成从变更理解到审查报告生成的全流程，显著提升审查效率与智能化水平。
- **基于CPG 的多静态工具集成**：以 Code Property Graph（CPG）为统一语义中枢，深度融合 ruff、biome、go vet、PMD、Semgrep 等多语言静态分析工具，实现跨工具结果对齐、去重与优先级融合，兼顾深度语义分析与广度规则覆盖。
- **多风险类型全覆盖识别与验证**：同时覆盖六大风险类型（健壮性与边界条件、并发与时序正确性、鉴权与数据暴露风险、需求意图与语义一致性、生命周期与状态一致性、语法与静态错误），并通过置信度校准、熔断机制与 LLM 交叉验证，确保高精度、低误报。


## 代码结构

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

## 功能特性

- ✅ **1. 多智能体工作流自主决策（Self-Orchestrating Pipeline）**    
   *(1)四阶段自治流水线：*    
     意图分析智能体（Intent Analyzer Agent）：解析 PR 标题、描述、关联 Issue、提交历史等上下文，识别开发意图（如功能新增、安全修复、重构等）。    
     任务管理智能体（Manager Agent）：基于意图动态规划审查策略，分配计算预算、设定工具调用优先级、执行并发控制与结果去重。    
     专家执行智能体（Expert Execution Agents）：并行调用静态分析工具、CPG 引擎与 LLM 模型，执行领域特定审查。    
     报告合成智能体（Report Synthesis Agent）：汇总多源发现，生成结构化、可操作的审查报告，并以 PR 评论形式提交。    
  *(2)所有智能体完全自主决策，通过内部通信协议协商任务状态与资源分配。*    
     如不需要可跳过工具调用、重试失败的工具调用（带失败跟踪）、接近迭代限制时提供回退审查、基于上下文和先前观察做出决策。    
- ✅ **2. CPG 辅助代码结构梳理**  
  *集成 Code Property Graph（CPG） 作为统一代码语义表示层*，支持跨语言（Python/TS/Go/Java 等）的控制流、数据流、调用图与污点分析。CPG 为 LLM 提供结构化上下文，显著提升对复杂逻辑错误、安全漏洞（如 SQLi、XSS）的检测能力。  
- ✅ **3. 智能降噪与聚焦机制**  
  *(1)认过滤低信号文件*：自动跳过锁文件（package-lock.json）、生成代码、二进制、媒体、日志等非源码文件。  
  *(2)变更行锚定*：意图分析将所有分析聚焦于 PR 变更行 ±N 范围（可配置），避免无关上下文干扰。  
  *(3)预算分配与去重合并*：限制每个文件/变更块的工具调用次数，对多工具/多轮次发现进行语义去重与合并，避免冗余告警。  
- ✅ **4. 置信度校准与熔断机制**  
  *(1)引入动态置信度校准*：当意图分析智能体识别过多风险因素、专家智能体因工具调用超预算、LLM 响应模糊或分析轮次达到熔断阈值时，自动压低问题置信度上限。  
  *(2)严格防止“无证据推断”被标记为 confirmed*，确保审查结果可审计、可追溯。  
- ✅ **5. 可扩展 DAO 层与资产管理**  
  *(1)DAO（Data Access Object）抽象层*：当前基于文件系统实现（MVP），但接口设计兼容 SQL、NoSQL 或图数据库（如 Neo4j）后端，支持无缝迁移。  
  *(2)RepoMap 构建器*：自动构建仓库元数据图（含模块依赖、入口点、测试覆盖等），通过幂等机制持久化至 DAO，供后续智能体复用。  
- ✅ **6. MCP 兼容工具接口**  
   *(1)所有工具遵循 MCP（Model-Code-Platform）标准化接口*，包括：  
        FetchRepoMapTool：获取仓库结构与依赖关系。
        ReadFileTool：安全读取指定路径文件内容。  
        RunGrepTool：正则匹配所需信息。  
   *(2)支持工具热插拔*，便于集成新静态分析器或 LLM 能力。  
- ✅ **7. 语法与风格检查（Agent-Defined Config, ADC）**  
   *内置多语言语法/风格检查器*，强制使用统一内置配置（ADC 策略），确保团队规范一致性：  
      (1)Python：ruff  
      (2)TypeScript/JavaScript：biome  
      (3)Go：go vet + golangci-lint  
      (4)Java：PMD + Checkstyle  
- ✅ **8. 全面可观测性与日志记录**  
    *(1)结构化日志系统*：自动记录所有智能体的观察、决策、工具调用、LLM 请求/响应到 JSONL 日志文件。  
    *(2)支持审计追踪、性能分析与故障复现。*  
- ✅ **9. 多 LLM 提供商支持与并发控制**  
    *多后端兼容*：支持 OpenAI、DeepSeek 等主流 LLM 提供商，并内置 mock 提供商用于单元测试与离线调试。  
- ✅ **10. 健壮性与工程实践**  
    *(1)优雅降级*：当某工具或 LLM 不可用时，自动跳过并记录警告，不影响整体流程。  
    *(2)详细错误报告*：结构化错误上下文（含堆栈、输入快照、工具状态）便于快速定位。  
    *(3)类型安全*：全代码库采用 Python 类型提示（Type Hints） + Pydantic v2 模型验证，确保数据流强一致性与运行时安全。  

## 技术栈

- **Python 3.10+**
- **LangGraph**：使用 StateGraph 和 Nodes 进行智能体编排
- **Pydantic v2**：所有智能体 I/O 和资产模式的数据验证
- **LangChain Core**：用于提示模板、工具和消息处理
- **unidiff**：Git diff 解析和行号映射
- **Tree-sitter**：代码解析（计划在后续版本中实现）

  

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


## 未来增强

- [ ] Tree-sitter 集成用于 AST 分析
- [ ] 控制流图（CPG）生成
- [ ] SQL/NoSQL/GraphDB 存储后端
- [ ] 真实 GitHub API 集成
- [ ] 代码嵌入的向量存储
- [ ] 高级查询功能
- [ ] 审查结果的 Web UI
- [ ] CI/CD 集成
- [ ] 更多语言支持（Rust 等）

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

## 许可证

[添加您的许可证]
