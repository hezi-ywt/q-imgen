# q-imgen

基于 channel 路由的原子化 Nano Banana CLI。

[English README](README.md)

一个原语，多条渠道。每个 channel 都是一条完整的图像生成路由：`protocol + base_url + api_key + model`。你可以在 Google 原生 Gemini 接口和 OpenAI 兼容网关之间切换，而不需要改代码或环境变量。

```bash
q-imgen generate "prompt" [--image ref.png ...] [--channel name] [-o ./out]
q-imgen batch tasks.json [--channel name]
q-imgen channel add <name> --protocol {gemini|openai} --base-url URL --api-key KEY --model M
q-imgen channel list | show [name] | use <name> | rm <name>
q-imgen status
```

## 安装

```bash
git clone <your-repo-url>
cd q-imgen
python -m pip install -e .
q-imgen --help
```

如果你暂时不想安装，也可以直接这样运行：

```bash
PYTHONPATH=src python -m q_imgen --help
```

## 两种协议，一套 CLI

q-imgen 内部提供两个 client：

| 协议 | 接口 | 适用场景 |
|---|---|---|
| `gemini` | `POST {base_url}/models/{model}:generateContent` | Google 原生 Gemini API（自动识别 `googleapis.com` 并使用 `?key=` 鉴权），或任何兼容 Gemini payload 格式的代理（使用 Bearer 鉴权） |
| `openai` | `POST {base_url}/chat/completions` | 以 OpenAI chat schema 暴露图像生成能力的兼容网关（one-api / new-api / litellm / 各类代理 等） |

添加 channel 时选对协议即可；后续调用时 q-imgen 会自动分发。

OpenAI client 能兼容真实网关里常见的三种内嵌图片返回形状：`message.images[]`、`message.content` 里的 markdown 图片、OpenAI vision 风格的 content parts，并且会按 URL 去重。所以大多数公开的 OpenAI 兼容图像网关不需要单独做适配。完整说明见 [docs/design-rationale.md](docs/design-rationale.md#why-the-openai-client-accepts-multiple-response-shapes)。

## 快速开始

```bash
# 添加第一个 channel（会自动成为默认 channel）
q-imgen channel add proxy-a \
  --protocol openai \
  --base-url https://your-proxy.example.com/v1 \
  --api-key sk-xxx \
  --model gemini-3.1-flash-image-preview

# 生成图片
q-imgen generate "anime girl in shrine" -o ./output

# 添加第二个 channel（Google 原生）
q-imgen channel add google-native \
  --protocol gemini \
  --base-url https://generativelanguage.googleapis.com/v1beta \
  --api-key AIzaSy... \
  --model gemini-3.1-flash-image-preview

# 切换默认 channel
q-imgen channel use google-native

# 或者每次调用显式指定 channel，不切默认值
q-imgen generate "..." --channel proxy-a

# 单图编辑 / 多图融合
q-imgen generate "change kimono to blue" --image input.png
q-imgen generate "merge A's hair with B's style" --image a.png --image b.png

# 批量生成
q-imgen batch tasks.json -o ./output --delay 1.0
```

## 输出约定

- **`generate` / `batch` 的 stdout**：每次调用输出一个 JSON 对象，适合 agent 或脚本解析
- **`channel add/list/use/rm` 的 stdout**：面向人类阅读的状态文本
- **`channel show` 的 stdout**：输出一个 JSON 对象
- **stderr**：`[q-imgen] ...` 诊断信息
- **退出码**：成功为 `0`，失败为 `1`；batch 的部分失败会返回 `status: "partial"` 且退出码仍为 0，调用方可以继续检查单个 task 的结果

成功输出示例：

```json
{
  "status": "ok",
  "channel": "proxy-a",
  "model": "gemini-3.1-flash-image-preview",
  "prompt": "anime girl in shrine",
  "images": ["./output/img_000.png"],
  "ref_images": []
}
```

## 本地共享 Key 限流

`q-imgen` 现在会在**当前机器本地**按 API key 做一个轻量并发上限控制。

- 作用范围：只影响同一台机器上的本地 `q-imgen` 进程
- 分组方式：按 API key 哈希；不同 channel 只要共用同一把真实 key，也会共享同一个上限
- 默认上限：同一把共享 key 在本机默认允许 `10` 个并发请求
- 状态文件：`~/.q-imgen/state.db`
- 查看命令：`q-imgen status`

这不是远端任务系统。它不会查询 provider 侧队列，也不会显示其他机器上的任务。

失败输出示例（exit 1）：

```json
{
  "status": "error",
  "channel": "proxy-a",
  "model": "gemini-3.1-flash-image-preview",
  "prompt": "anime girl in shrine",
  "error": "API request failed with HTTP 401: ..."
}
```

## 配置存储

配置文件位于 `~/.q-imgen/channels.json`，文件权限为 `chmod 600`。必要时也可以手工编辑：

```json
{
  "default": "proxy-a",
  "channels": {
    "proxy-a": {
      "protocol": "openai",
      "base_url": "https://your-proxy.example.com/v1",
      "api_key": "sk-...",
      "model": "gemini-3.1-flash-image-preview"
    },
    "google-native": {
      "protocol": "gemini",
      "base_url": "https://generativelanguage.googleapis.com/v1beta",
      "api_key": "AIza...",
      "model": "gemini-3.1-flash-image-preview"
    }
  }
}
```

## Batch 任务格式

任务文件是一个 JSON 数组，每个 task 都会继承 CLI 调用时指定的 channel / model：

```json
[
  { "prompt": "silver elf archer, magic forest", "aspect_ratio": "2:3", "image_size": "2K" },
  { "prompt": "cat-ear boy stargazing", "aspect_ratio": "16:9" },
  { "prompt": "change kimono to blue", "images": ["input.png"], "aspect_ratio": "3:4" }
]
```

task 内字段会覆盖本次 CLI 调用的默认值，但只对当前 task 生效。详见 `skills/q-imgen/references/nanobanana-batch-format.md`。  
每个 task 都必须提供 `prompt`；缺少 `prompt` 的 task 会在本地直接失败，并记录到 batch 的 `results` 数组里，不会发出 API 请求。

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

当前测试全部是离线测试，HTTP 调用都被 mock 掉了。这个版本没有内置 live smoke test；如果你需要，可以自行添加 `tests/live_*.py` 并用环境变量控制是否执行。

## Python 库 API

q-imgen 也可以作为 Python 库在脚本中直接调用，适用于需要自定义循环、图片预处理/后处理、任务间依赖的工作流：

```python
from q_imgen import generate

images = generate("一只可爱的狐狸", images=["style_ref.png"], channel="my-proxy",
                  aspect_ratio="1:1", image_size="1K")
images[0].save("output.png")
```

- 返回 `list[PIL.Image.Image]` — 不保存文件、不写 history、不打印
- `images` 参数接受文件路径（`str`/`Path`）和 `PIL.Image` 对象混合
- 复用 CLI 的 channel 配置（`~/.q-imgen/channels.json`）
- 失败时抛异常（`ChannelError` / `GeminiError` / `OpenAIError`）

**CLI 还是库？** CLI 适合 agent 调用、单次生成、batch 任务、shell 管道。库适合需要在生成前后处理图片、串联多次生成结果、或自定义循环逻辑的 Python 脚本。

## 设计哲学

q-imgen 选择做一个可靠的积木块，把搭积木的自由留给使用它的人。

- **原子原语，不做框架**。只做一件事：发 prompt、带参考图、拿回图片。批量、循环、prompt 优化、风格策略、工作流编排全部属于调用方（agent 或脚本），q-imgen 不碰。
- **两个入口，同一个内核**。CLI 面向 agent 和 shell（stdout JSON、exit 0/1），Python 库面向脚本（返回 `PIL.Image`、失败抛异常）。选择权在调用方；两者共享同一套协议 client。
- **channel 是唯一的路由抽象**。不做启发式选择，不做 env var 优先级链。一个 `channels.json` 就是全部真相，调用方显式传 `--channel`。
- **两个协议不统一**。Gemini 和 OpenAI 的 payload 形状根本不兼容，强行抽象只会造成沉默失真。两个 client 独立演化，共同点只有”返回结果或抛异常”这一个接口。
- **观察可以，编排不行**。history 日志是唯一允许的”状态”——它只记录做过什么，不决定下一步做什么。而且是 best-effort 的：日志失败不影响生图。
- **在关键位置保持 agent-safe I/O**。`generate` / `batch` 的 stdout 是数据，stderr 是诊断，退出码是 0/1。channel 管理命令保持人类可读，只有 `channel show` 返回 JSON。
- **API key 安全**。所有错误消息都会清洗 live key 和 `Bearer` token，再向外暴露。
- **Git 原生更新**。不走 PyPI。skill 文件、库代码、CLI 元数据都在同一个仓库里。`git pull` 即时更新 skill 和代码，只有 `pyproject.toml` 变了才需要 `pip install -e .`。

## 项目结构

```text
q-imgen/
├── src/q_imgen/
│   ├── api.py              # Python 库 API：generate() → list[PIL.Image]
│   ├── cli.py              # argparse 入口与子命令处理
│   ├── channels.py         # channels.json 的 CRUD
│   ├── gemini_client.py    # Gemini 原生协议
│   ├── openai_client.py    # OpenAI 兼容协议
│   ├── history.py          # 审计日志（JSONL）
│   └── limiter.py          # 本地共享 Key 并发限流
├── tests/
└── skills/q-imgen/         # 面向 agent 的 skill
```

## License

MIT
