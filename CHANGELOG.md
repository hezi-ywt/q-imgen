# Changelog

All notable changes to `q-imgen` will be documented in this file.

## 0.4.0 - 2026-04-16

### Added

- **Python 库 API**:`from q_imgen import generate` 返回 `list[PIL.Image.Image]`，不保存文件、不写 history、不打印。脚本工作流可以直接调用，自己控制预处理/后处理/循环逻辑。
- **`generate_images()`**:两个协议 client（`gemini_client` / `openai_client`）各新增 `generate_images()` 函数，返回 `list[PIL.Image.Image]`，供 `api.generate()` 和直接调用。
- **PIL.Image 输入支持**:参考图参数（`images` / `reference_images`）现在接受 `str | Path | PIL.Image.Image` 混合输入。
- **自动图片预处理**:`api.generate()` 在分发到 client 前，自动将超过 2048px 边长的 PIL.Image 缩小（`thumbnail` + `LANCZOS`）。
- **OpenAI 路径重试**:`openai_client` 新增 429/5xx 重试逻辑（默认 3 次，5s 间隔），与 gemini_client 行为对齐。
- **`max_retries` 参数透传**:两个 client 的 `generate()` / `generate_images()` 和 `api.generate()` 都接受 `max_retries` 参数。
- **`__init__.py` 导出**:`generate`、`Channel`、`ChannelError`、`GeminiError`、`OpenAIError`。
- **Skill 更新**:新增 `references/python-api.md`（库 API 文档 + CLI 与库的选择指南）、`references/update-check.md`（git 更新流程）；SKILL.md 新增"首次使用"引导和"脚本调用"章节。
- 13 个新测试（`test_api.py`）覆盖两个协议、PIL 输入、参数透传、图片预处理、错误抛出。

### Changed

- 默认 timeout 从 300s 不变（API 和 CLI 统一）。`_TIMEOUT_SECONDS` 在各模块中显式声明为 300。

## 0.3.0 - 2026-04-15

### Added

- **历史日志**:每次 `generate` / `batch` task 自动追加一条 JSONL 记录到 `~/.q-imgen/history/YYYY-MM-DD.jsonl`(按本地日期分文件),内容包括 `ts` / `prompt` / `model` / `channel` / `protocol` / `aspect_ratio` / `image_size` / `ref_images` / `outputs` / `status` / `error`(仅失败) / `latency_ms` / `workdir`。失败和成功都记录,失败的多一个 `error` 字段。
- **`q-imgen history` 子命令**:打印今天的 log 文件路径(不做任何查询)。所有真实查询用 shell + jq 完成,完整命令模板见 [history-queries.md](skills/q-imgen/references/history-queries.md)。
- **`workdir` 字段自动检测**:有 `.git` 目录就取 git 根目录,否则取 `cwd`,意味着同一仓库不同子目录的调用聚拢到同一个 workdir 值,过滤更聚拢。
- **并发安全的 append**:用 `fcntl.flock` 独占锁,`xargs -P` / asyncio 并发写同一日志文件不会交错。
- **best-effort 写入**:history.append 内部 try/except 全部异常,失败时只在 stderr 输出一行 `[q-imgen] warning: history append failed: ...`,主流程不受影响 —— 用户的图已经生成,日志是次要状态。

### Changed

- `cli._run_single` 现在测量端到端延迟(`time.monotonic_ns`)并把结果写进 history 记录的 `latency_ms` 字段。
- 测试套件:**+23 个新测试**(test_history.py 16 个 + test_cli history 集成 5 个 + 维护性合并 2 个),全部 offline,零 API 成本。现有 cli 测试全部隔离 `HISTORY_DIR` 到临时目录,绝不污染真实 `~/.q-imgen/history/`。

### Notes

- **不写入 history 的失败**:渠道不存在 / channels.json parse 错误 / batch task 文件不存在等 **pre-flight** 错误 —— 因为这种情况下没有合法的 `channel` / `model` / `protocol` 可以填写,记一条无意义。只有进入 `_run_single`(意味着至少有合法 channel)的调用才进 history。
- **不记录的字段**:`api_key` / `base_url` / 原始 API 请求/响应 body / 参考图 base64 内容。详见 design-rationale.md。
- **没有日志轮转 / TTL**:典型用量下文件每天 < 100KB,一年 < 30MB,jq 处理无压力。需要清理时 `find ~/.q-imgen/history -mtime +90 -delete`。

## 0.2.2 - 2026-04-15

### Changed

- **OpenAI 协议响应解析改为多形状并行扫描 + dedup**。0.2.1 把 markdown content 当 fallback 是优先级搞反 —— markdown-in-content 实际是大多数 one-api / new-api / litellm / proxy gateway 中转的**主流形状**,不是边缘案例。`_extract_images_from_response` 现在同时扫三个位置(`message.images[]` / `message.content` 字符串里的 markdown / `message.content` 列表里的 vision parts),按 URL dedup,不做 first-wins 短路。
- 文档 `docs/design-rationale.md` 新增 "Why the OpenAI client accepts multiple response shapes" 一节,说明这是设计决策不是兼容补丁,并指明未来加新形状的标准流程(扫一行 + 一个测试)。

### Added

- 6 个新回归测试覆盖:多图响应、URL with query params、两种 shape 同 URL dedup、vision-style content parts 数组、空/None content 防御、explicit + markdown 同时存在的 merge 行为。

## 0.2.1 - 2026-04-15

### Fixed

- **OpenAI 协议支持 markdown 内嵌图像响应**。之前 `openai_client.py` 只识别 `choices[0].message.images[]` 形状(某些代理的非标准扩展),现在同时支持 `choices[0].message.content` 里的 markdown `![...](data:image/...)` 或 `![...](http://...)`。实测 proxy-gateway.example 用后一种形状返回图像,修复后两种网关都能工作。新增 3 个回归测试覆盖两种 shape 和优先级。

### Notes (not code changes)

- 实测验证 proxy-gateway.example 的两个协议端点都能跑通 Google Nano Banana Pro (`gemini-3-pro-image-preview`):
  - Gemini 原生:`https://proxy-gateway.example/v1beta`
  - OpenAI 兼容:`https://proxy-gateway.example/v1`
  - 同一把 `sk-` key 两个协议都能用
  - 但 proxy gateway 的 OpenAI 端点**不尊重** `image_config.aspect_ratio`,需要严格控制 ratio 时用 Gemini 协议渠道
- 对应经验已写入 `skills/q-imgen/references/user-notes.md` 的 Patterns 区

## 0.2.0 - 2026-04-15

**Breaking redesign.** q-imgen 从"多引擎 wrapper"简化为"纯 nanobanana CLI",Midjourney 支持整体移除。

### Added

- **Channel 抽象**:`(protocol, base_url, api_key, model)` 四元组,存在 `~/.q-imgen/channels.json`(权限 600)
- **`channel` 子命令**:`add` / `list` / `show` / `use` / `rm`,支持多渠道并存与默认渠道切换
- **Gemini 协议 client**(`gemini_client.py`):POST `/models/{model}:generateContent`,自动按 `base_url` 是否为 `googleapis.com` 选择 `?key=` 或 `Bearer` 认证;内置 4xx 不重试、429/5xx 重试策略
- **OpenAI 兼容协议 client**(`openai_client.py`):POST `/chat/completions`,返回 data-URL 或 http URL 都能存盘
- **`--channel` / `--model` 每次调用级覆盖**:不改动存储即可临时换渠道或模型
- **统一错误脱敏**:所有错误消息中的 live api_key 和 `Bearer <token>` 自动替换为 `<redacted>`

### Changed

- `q-imgen generate` / `q-imgen batch` 成为顶层子命令,不再嵌套在引擎名下(旧:`q-imgen nanobanana generate`,新:`q-imgen generate`)
- 持久配置迁移:旧的 `~/.q-imgen/.env` + 扁平 `NANOBANANA_*` 字段表 → 新的 `channels.json`
- 版本号改从 `importlib.metadata` 读取,不再需要同步修改 `__init__.py`
- batch 部分失败现在返回 `status: "partial"` + exit 0(之前是 exit 1),让调用方能看 `results` 决定下一步

### Removed

- **Midjourney 支持**:整条 MJ dispatch / config / routing / 测试全部删除
- **`subprocess` 派发到 `python -m nanobanana`**:Gemini 协议逻辑已吸收进 `gemini_client.py`,q-imgen 完全自包含,不再依赖本地 `nanobanana` 包
- **启发式路由**(`routing.choose_engine` 及其测试):调用方显式指定 `--channel`
- **`FIELDS` 配置字段表**:10 个手写字段 → 1 个 `Channel` dataclass
- **Profile 支持**(`NANOBANANA_PROFILE_*`):用 channel 替代
- **`live_banana_smoke` 测试**:依赖已删除的 subprocess 路径,修复成本 ≈ 重写

### Migration

旧的 `~/.q-imgen/.env` 可以用以下 Python 片段迁移到 `channels.json`(读 env 文件、写一个默认 channel):

```python
from pathlib import Path
from q_imgen.channels import ChannelStore
env = {}
for line in Path.home().joinpath(".q-imgen/.env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    env[k.strip()] = v.strip().strip('"').strip("'")

store = ChannelStore.load()
store.add(
    "default",
    protocol="openai",  # 或 "gemini",看你原来的 NANOBANANA_PROVIDER
    base_url=env.get("NANOBANANA_OPENAI_BASE_URL") or env.get("NANOBANANA_BASE_URL"),
    api_key=env.get("NANOBANANA_OPENAI_API_KEY") or env.get("NANOBANANA_API_KEY"),
    model=env.get("NANOBANANA_OPENAI_MODEL") or env.get("NANOBANANA_MODEL"),
)
store.save()
```

迁移完成后手动删除旧的 `.env` 文件。

## 0.1.0 - 2026-04-15

- Initial GitHub-publishable version of the `q-imgen` project
- Added repo-installable Python CLI with `q-imgen` entrypoint
- Added persistent config via `~/.q-imgen/.env`
- Added Banana provider support for `gemini` and `openai`
- Added default unit tests and manual Banana live smoke tests
