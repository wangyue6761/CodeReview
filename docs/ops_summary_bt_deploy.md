# CodeReview 上线复盘：本地工具 → 公网 GitHub PR 审查服务（宝塔 + FastAPI + PAT）

## 目标

- 在 GitHub PR Conversation 里评论 `@cptbot review`
- 服务端自动拉取 PR 代码、运行多智能体审查
- 以 **PR Review 行内评论** 回贴 RiskItem（无法落点的条目兜底发一条普通评论）

## 总体方案（PAT 版）

- 服务端：FastAPI（`github_pat/app.py`）接收 GitHub Webhook（`issue_comment`）
- 触发：评论正文包含 `@cptbot review`
- 执行：后台 worker
  - mirror + worktree：同仓库只保留一份 mirror，多 PR 只 `fetch + worktree`
  - 调用审查引擎：复用 `main.py` 的工作流，读取 `confirmed_issues`
  - 回贴：优先创建 PR Review（行内评论）；不在 diff hunk 的条目汇总成 issue comment
- 安全：
  - webhook 签名校验（`X-Hub-Signature-256` + `GITHUB_WEBHOOK_SECRET`）
  - 仓库白名单 `ALLOWED_REPOS`

## 服务端关键配置（`.env`）

必须项：
- `GITHUB_TOKEN`：fine-grained PAT（写评论/发 Review 必须）
- `GITHUB_WEBHOOK_SECRET`：Webhook secret（验签）
- `ALLOWED_REPOS`：允许触发的仓库列表（逗号分隔 `owner/repo`）

LLM 必须项（否则会报 `api_key client option must be set`）：
- 推荐最稳：`LLM_PROVIDER` + `LLM_API_KEY`
  - DeepSeek 示例：`LLM_PROVIDER=deepseek`、`LLM_API_KEY=...`、可选 `LLM_BASE_URL=https://api.deepseek.com`
  - Zhipu 示例：`LLM_PROVIDER=zhipuai`、`LLM_API_KEY=...`
- 注意：项目只识别 `LLM_API_KEY` / `DEEPSEEK_API_KEY` / `ZHIPUAI_API_KEY`，自定义变量名不会生效。

调试项：
- `ALLOW_UNSIGNED_WEBHOOKS=1`：仅用于自测跳过验签；上线务必改回 `0`

提示：
- `.env` 默认不提交（在 `.gitignore` 中忽略）；不要把 token/keys 写进代码或提交到仓库。

## 宝塔部署流程（Ubuntu）

### 1) 域名 & 证书（Let’s Encrypt）

- Let’s Encrypt **不支持 IP 证书**，需要域名。
- DNS A 记录必须指向当前宝塔服务器公网 IP。

验证方法：
- 服务器执行 `getent hosts <domain>`，确认解析 IP 就是当前服务器公网 IP。

### 2) Nginx 反向代理到 uvicorn

推荐：
- uvicorn 仅监听 `127.0.0.1:8000`
- 站点反向代理转发到 `http://127.0.0.1:8000`
- Webhook 地址：`https://<domain>/github/webhook`

注意：
- 反代不要把 `Host` 写死成 `127.0.0.1`，建议 `$host`。

### 3) Supervisor 常驻 & 实时日志

要实时看到工作流的 `print`，关键是禁用 stdout 缓冲（`python -u`）。

示例 command（不合并 stderr）：

```ini
command=/bin/bash -lc 'set -euo pipefail; cd /home/halo/cpgbot/CodeReview; set -a; source .env; set +a; exec /root/miniconda3/envs/cpgbot/bin/python -u -m uvicorn github_pat.app:app --host 127.0.0.1 --port 8000 --log-level info --access-log'
```

实时查看：
- `tail -f .../cpgbot.out.log`：access log + stdout（更实时的工作流输出）
- `tail -f .../cpgbot.err.log`：warning/异常等（例如 Lite-CPG warning）

## GitHub 侧配置

结论：Webhook 是“按仓库”配置的，**每个仓库都需要配置一次**。

仓库 Settings → Webhooks：
- Payload URL：`https://<domain>/github/webhook`
- Content type：`application/json`
- Secret：与服务器 `.env` 的 `GITHUB_WEBHOOK_SECRET` 一致
- Events：Issue comments

验证：
- Webhook Deliveries → Redeliver
- 服务端 `.storage/github_pat/jobs.sqlite3` 里应出现新 job（`queued/running/done`）

## PR 测试集准备（批量创建 PR）

脚本：`test/sync_prs_and_recreate.py`
- 默认循环 5 个仓库里的 4 个（跳过 sentry），同步 upstream PR 分支到你的 fork 并创建 PR
- 本地缺仓库会自动 `git clone`（默认目录 `/Users/wangyue/Code/CodeReviewData/bottest`，可用参数覆盖）

## 常见踩坑（避坑 Tips）

1) 域名解析到别的 IP（例如 `cpgbot.com -> 50.6.227.68`）
- 现象：在本机改 Nginx/申请证书都不生效，HTTP-01 验证 404/307
- 排查：`getent hosts <domain>` / `dig +short <domain>`
- 解决：改 DNS A 记录到宝塔服务器公网 IP，等待 TTL 生效

2) PAT 权限不足导致 403
- 现象：`Resource not accessible by personal access token`
- 解决：fine-grained PAT 勾选目标仓库，并给 `Issues: write`、`Pull requests: write`、`Contents: read`
- 脚本自检：`docs/check_pat.sh`

3) 关闭免验签后 `curl` 触发失败
- 原因：本地模拟请求没有 `X-Hub-Signature-256`
- 解决：调试可临时 `ALLOW_UNSIGNED_WEBHOOKS=1`；上线用 GitHub Redeliver 验证

4) LLM key 没被读取
- 现象：`api_key client option must be set...`
- 原因：变量名写错 / 未设置 `LLM_PROVIDER` / 未重启进程加载 `.env`
- 解决：使用 `LLM_PROVIDER` + `LLM_API_KEY`，改完重启 supervisor

5) 触发命令大小写
- 现状：触发逻辑是大小写敏感的字符串匹配，需用 `@cptbot review`（小写）

6) Webhook Content type 选错导致 `EOF` / 修改配置不自动投递
- 现象：GitHub Deliveries 显示 `We couldn't deliver this payload: EOF`，服务端和 Nginx error 里可能没有对应日志
- 原因：Webhook 选了 `application/x-www-form-urlencoded`（页面上显示为 `form`），与服务端按 JSON 解析不匹配
- 解决：
  - 每个仓库 Webhook 的 `Content type` 统一选 `application/json`
  - 修改 Webhook 配置后 **不会自动触发一次投递**：需要在 Deliveries 里手动 `Ping/Redeliver`，或在 PR 里新发一条包含 `@cptbot review` 的评论（必须是新建 comment，编辑旧 comment 不触发）

## 收尾检查清单

- [ ] `ALLOW_UNSIGNED_WEBHOOKS=0` 且 GitHub Redeliver 触发正常
- [ ] `ALLOWED_REPOS` 覆盖所有目标仓库
- [ ] PAT 权限通过 `docs/check_pat.sh` 自检
- [ ] Supervisor 常驻，日志可实时滚动
- [ ] 反代与 HTTPS 正常：`https://<domain>/healthz` 返回 `{"ok": true}`
