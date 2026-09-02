# zai2api

**Z.ai (chat.z.ai) → OpenAI-Compatible API 网关** — 把智谱 GLM 网页版的免费对话能力转换为标准 OpenAI API 格式，支持流式输出、思考链分离、账号池轮询、管理面板。

> ⚠️ 本项目仅供学习与协议研究，请遵守 Z.ai 服务条款；上游风控随时可能变化。

---

## 究极报告：为什么这个项目长这样

本文是立项前的完整调研 + 逆向成果，也是本项目的设计依据。

### 1. 上游协议档案（chat.z.ai，2026-09 实测逆向）

| 项目 | 内容 |
|---|---|
| 游客 token | `GET /api/v1/auths/` → `{token: "eyJ..."}`（ES256 JWT，无过期字段，guest 角色，仅临时会话） |
| 对话端点 | `POST /api/v2/chat/completions`（**旧 v1 `/api/chat/completions` 已 404 下线**） |
| 必需头 | `Authorization: Bearer <jwt>`、`x-fe-Version: prod-fe-1.1.x`（从首页 HTML 抓取）、`x-region: overseas`、`x-signature` |
| 签名算法 | `ts=毫秒时间戳`；`wKey = HMAC-SHA256(saltKey, floor(ts/300000)).hex`；`msg = "requestId,<uuid>,timestamp,<ts>,user_id,<uid>" + "|" + b64(signature_prompt) + "|" + ts`；`x-signature = HMAC-SHA256(wKey, msg).hex` |
| saltKey | `key-@@@@)))()((9))-xxxx&&&%%%%%`（前端混淆常量，已从 prod-fe-1.1.92 bundle 提取） |
| 请求体 | `{model, chat_id, messages, signature_prompt, stream:true, captcha_verify_param, features:{enable_thinking, web_search, ...}}` |
| 验证码 | Aliyun 无感验证（traceless）：SDK `o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js`，`region:cn`、`prefix:no8xfe`、`SceneId:didk33e0`（chat.z.ai 域名专用），成功回调返回 `captcha_verify_param` |
| SSE 格式 | `data: {"type":"chat:completion","data":{phase, delta_content, edit_content, edit_index, done, usage, error}}` |
| 思考链 | thinking 文本经 `delta_content` 增量下发，包在 `<details type="reasoning">...</details>` HTML 里；answer 文本经 `edit_content+edit_index` 增量下发（两个并行视图） |
| usage | 流首个事件即带 `{prompt_tokens, completion_tokens, total_tokens}` |

**风控演进时间线**（各开源项目交叉印证）：

- 2025-09：游客 token + v1 端点裸奔即可用（zonde306/Cunninger 时代）
- 2026 年中：X-Signature HMAC 签名上线（D3-vin/GLM-ZAI-2API 逆向）
- 2026-09（当前）：v1 端点彻底 404；v2 端点强制 Aliyun 无感验证码；**captcha token 与浏览器指纹绑定**（本项目的关键发现，见 §3）

### 2. 关键发现：captcha 指纹绑定（F019）

在纯 httpx 客户端里带上 camoufox 拿到的合法 `captcha_verify_param` 会被上游以 `verify_failed F019` 拒绝；同一 token 在**同一浏览器页面内**用 `fetch` 发送则验证通过。

结论：Aliyun 无感验证的 securityToken 绑定了浏览器会话指纹（TLS、头序、cookie、设备信号）。因此本项目采用**常驻浏览器会话代理**架构：

```
客户端 → FastAPI → camoufox 常驻页面(chat.z.ai) → 页内 fetch(签名+captcha) → SSE 流
```

页面同时负责：guest token 获取、captcha 求解、签名请求发送。Python 侧只负责签名计算与 SSE 解析。这是 dbg 全链路实测验证的唯一稳定路径。

### 3. GitHub 同类项目全景（zai2api 生态）

| 项目 | Stars | 语言 | 亮点 | 现状 |
|---|---|---|---|---|
| [hmjz100/Z.ai2api](https://github.com/hmjz100/Z.ai2api) | 152 | Python | OpenAI+Anthropic 双格式、THINK_TAGS_MODE 四模式 | 已归档（协议过期） |
| [XxxXTeam/zai2api](https://github.com/XxxXTeam/zai2api) | 113 | Go | token 轮询、模型列表自动同步、注册工具 | 活跃（游客 token 已封） |
| [roseforyou/ZtoApi](https://github.com/roseforyou/ZtoApi) | 121 | Deno | 多模态映射（glm-4.5v） | 维护中 |
| [orbitoo/zai2api](https://github.com/orbitoo/zai2api) | 47 | Python | /v1/responses、账号池、admin 面板 | 活跃 |
| [D3-vin/GLM-ZAI-2API](https://github.com/D3-vin/GLM-ZAI-2API) | 39 | Go | **X-Signature 逆向首发**、5 策略 tool_calls 解析、deviceToken 池 | 活跃 |
| [lixia051/zai2api](https://github.com/lixia051/zai2api) | 14 | Go | headless Chromium 过 Aliyun 无感验证 | 活跃 |
| [SSDFDFDF/zai2api](https://github.com/SSDFDFDF/zai2api) | 6 | Python | 双 token 池、熔断、TOOL_STRATEGY 四模式 | 活跃 |
| [zonde306/zai2api](https://github.com/zonde306/zai2api) | 2 | Deno | 协议参考最清晰（v1 时代） | 协议过期 |

**通用 2api 生态最佳实践**（从 grok2api 7.5k★、chatgpt2api 6.1k★、gemini-web2api 3k★ 等项目归纳）：
- 流式转换统一出口 `data: [DONE]`；`reasoning_content` 承载思考链
- token 池：轮询 + 401 剔除 + 429 冷却 + 持久化；每请求换凭证重试
- 错误必须 surfaced（WAF 挑战页不能伪装成空 200）
- Docker + 管理面板（token CRUD / 日志 / curl 重放 / 统计）是标配
- 反检测分层：纯 HTTP（TLS 指纹伪装）→ 反指纹浏览器（camoufox）→ 全真浏览器（真实登录态）

### 4. 实测模型清单（GET /api/models，游客 token 可拉取）

| 公开名 | 上游 ID | 说明 |
|---|---|---|
| glm-4.7 | `glm-4.7` | **游客可用**，free_think 默认开 |
| glm-5.2 / glm-5.3 / glm-5.3-flash(`x-preview-l`) | 同名 | 需账号 JWT |
| glm-5-turbo / glm-5v-turbo | 同名 | 需账号 JWT；5v 支持视觉 |
| glm-4.5 (`0727-360B-API`) / glm-4.5-air (`0727-106B-API`) | 旧系 | 需账号 JWT |
| glm-4.6v / glm-4.1v(`GLM-4.1V-Thinking-FlashX`) | 同名 | 视觉模型 |
| deep-research (`Z1-Rumination`) / zero (`Z1-32B`) | 同名 | 深度研究/推理 |
| glm-4-flash / glm-4-air | 同名 | 老模型 |

注意：**GLM-4.6 已从上游下架**，别再用旧映射。

---

## 功能

- ✅ `POST /v1/chat/completions` — 流式 + 非流式，标准 `data: [DONE]`
- ✅ `GET /v1/models` — 全模型 + `-thinking` / `-search` / `-nothinking` 后缀变体
- ✅ `reasoning_content` / `<think>` / strip 三种思考链模式（`THINK_TAGS_MODE`）
- ✅ Function calling 模拟（prompt 注入 + 5 策略 JSON 解析，流式 `tool_calls`）
- ✅ 视觉输入（`image_url` base64 / 远程 URL 自动下载转 data URL）
- ✅ `POST /v1/responses` — Codex CLI 兼容
- ✅ 双凭证池：游客匿名 token（零配置）+ 账号 JWT 池（解锁 GLM-5.x），LRU 轮询、401 剔除、429 冷却、SQLite 持久化
- ✅ X-Signature HMAC 签名（时间桶自动对齐）
- ✅ Aliyun 无感验证码自动求解（camoufox 常驻会话，指纹绑定问题已解决）
- ✅ 管理面板 `/admin/`：token CRUD、用量统计、请求日志
- ✅ `GET /healthz` `/readyz` `/status`；根路径指纹隐藏
- ✅ 多 API key 下游鉴权；可选上游代理
- ✅ Docker / docker-compose

## 快速开始

```bash
# 1. 安装（Python 3.11+）
pip install -r requirements.txt
python -m camoufox fetch          # 下载反指纹浏览器（首次）

# 2. 配置
cp .env.example .env              # 改 AUTH_TOKENS / ADMIN_PASSWORD
# 可选：把 chat.z.ai 网页 localStorage 里的 JWT 填入 ZAI_TOKENS 解锁 GLM-5.x

# 3. 启动
python main.py                    # 默认 0.0.0.0:8000

# 4. 测试
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-my-secret-key-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-4.7","stream":true,"messages":[{"role":"user","content":"你好"}]}'
```

OpenAI SDK 直接可用：

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="sk-my-secret-key-1")
resp = client.chat.completions.create(
    model="glm-4.7",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

Docker：

```bash
cp .env.example .env && docker compose up -d
```

## 架构

```
app/
├── config.py          # 环境变量配置
├── models.py          # OpenAI schema + 模型注册表（实测同步）
├── converter.py       # OpenAI↔ZAI 消息/视觉/流式转换（edit_index 重组算法）
├── zai_client.py      # v2 协议客户端：签名、camoufox 常驻会话、SSE 解析
├── token_pool.py      # 双凭证池：LRU/401剔除/429冷却/SQLite 持久化
├── tools_emulator.py  # function calling 模拟（5 策略解析）
├── db.py              # SQLite：token 池 + 请求日志
├── static_files.py    # 内嵌管理面板（无前端构建链）
└── routes/
    ├── openai.py      # /v1/chat/completions /v1/models（重试/换凭证/TTFP 统计）
    ├── responses.py   # /v1/responses（Codex CLI）
    ├── admin.py       # /admin（登录/token CRUD/统计/日志）
    └── health.py      # /healthz /readyz /status
```

**请求流**：鉴权 → 模型后缀解析 → 凭证池取号（账号优先）→ 签名 → 浏览器会话页内请求（captcha 自动注入）→ SSE 重组（reasoning/answer 分离）→ OpenAI chunk 流。

## 风险与已知限制

1. **上游风控是移动靶**：签名 saltKey、SceneId、端点路径随时可能变（本项目已见证 v1→v2 一次）。升级前端版本号即可重新提取。
2. **游客额度**：游客 token 仅 glm-4.7 级文本模型，风控可能间歇性 F001/F008/F019。
3. **camoufox 资源占用**：常驻 Firefox 会话约 300-500MB 内存。
4. **验证码失败重试**：F 系列错误码自动冷却凭证并重试（`RETRY_COUNT` 次），连续失败会在错误信息中给出精确 `verify_code` 便于排查。
5. **机房 IP**：Aliyun WAF 对数据中心 IP 不友好，生产部署建议住宅网络或配置 `UPSTREAM_PROXY`。

## 管理面板

浏览器打开 `http://127.0.0.1:8000/admin/`，输入 `ADMIN_PASSWORD` 登录：
- 上游 token 池状态与 CRUD（只显示 JWT 前 16 位）
- 24h 请求统计（TTFT / 总延迟 / 成功率）
- 最近 50 条请求日志（错误详情）

## 开发

```bash
pip install -r requirements.txt pytest
pytest tests/ -q        # 35 个单测（协议转换/签名/池/工具模拟/API 集成 mock）
```

## 致谢

协议逆向参考了 [D3-vin/GLM-ZAI-2API](https://github.com/D3-vin/GLM-ZAI-2API)（签名算法）、[zonde306/zai2api](https://github.com/zonde306/zai2api)（v1 协议档案）、[lixia051/zai2api](https://github.com/lixia051/zai2api)（无感验证思路）等项目。
