---
name: q-imgen
description: >
  Google Nano Banana / Gemini 生图 CLI。用于文生图、图生图、图像编辑、多图融合、角色一致性、批量出图、渠道切换、历史查询。用户提到 nanobanana / banana / 大香蕉 / Gemini 生图 / q-imgen / q-imagen / 图生图 / 多图融合 / 参考图 / 角色一致性 时必须使用。
  Also covers OpenAI Images channels for Yunwu gpt-image-2 / gpt-image-2-all via q-imgen protocol `openai_images`.
metadata:
  requires:
    bins: ["q-imgen"]
  cliHelp: "q-imgen --help"
---

# q-imgen

一个最小的 Nano Banana CLI。q-imgen 是**执行层原语**:发 prompt,带参考图,拿回图片路径;批量、渠道切换、历史查询也都只做最小闭环。

## 核心定位

- q-imgen 只负责 **generate / batch / channel / history / status** 这几件事
- q-imgen **不负责** prompt 优化、启发式路由、工作流编排、角色设定管理
- q-imgen 的 stdout 是**机器侧 JSON**,你负责把它整理成用户能看的消息

## Agent 调用约定

- agent 需要本机限流状态时,优先用 `q-imgen status --json`;人类排查时再用文本版 `q-imgen status`
- `generate` 和 `batch` 的失败 JSON 现在都有稳定字段:
  - `error_code`: `auth_error` / `rate_limit` / `provider_busy` / `invalid_model` / `invalid_request` / `network_error` / `no_image_returned` / `local_limiter_error` / `unknown_error`
  - `retryable`: `true` 表示可以稍后重试、降并发、换 channel 再试; `false` 表示先修配置或输入
- `batch` 顶层除了 `results` 之外,还会给 agent 可直接消费的 summary:
  - `failed`
  - `retryable_failures`
  - `failed_task_indexes`
  - `error_counts`
- **输出目录纯洁性** 是硬约束: `-o/--output-dir` 下只放图片; history / limiter / status / 诊断信息都不写进输出目录
- agent 默认优先看结构化字段,不要靠字符串模糊匹配错误原因,也不要假设输出目录里会有 sidecar JSON

**每次在本轮里第一次进入 q-imgen skill 时,必须先读** [references/user-notes.md](references/user-notes.md)。用户要求检查更新或你发现版本可能过旧时,按 [references/update-check.md](references/update-check.md) 操作。那里是这个 skill 的记忆层:用户偏好、项目偏好、真实踩坑和已有工作流,优先级高于你自己的临场猜测。任务做完后,如果这次新增了稳定偏好、有效经验或新坑,要**及时回写**进去,不要拖到以后。

## 首次使用

在执行任何生图命令前,先跑 `q-imgen channel list`。如果输出为空（没有配置任何渠道）,**主动引导用户完成首次配置**:

1. 告诉用户需要一个 API 渠道才能使用,需要提供: `base_url`、`api_key`、`protocol`（gemini、openai 或 openai_images）、`model`
2. 用户提供信息后,执行:

```bash
q-imgen channel add <name> --protocol <protocol> --base-url <url> --api-key <key> --model <model>
```

3. 验证: `q-imgen channel list` 确认渠道已添加且标记为默认

不要跳过这一步直接生图 — 没有渠道一定会报错。

## 快速决策

### 模型

默认用 **Nano Banana 2**(`gemini-3.1-flash-image-preview`)。

换 **Nano Banana Pro**(`gemini-3-pro-image-preview`) 只有几个硬理由:

- 图里要清晰文字: logo、海报、包装、UI mockup
- prompt 明显是多步复杂指令
- 上一版结果不对,用户要求"更强的模型"
- 用户明确说"用 pro" / "质量优先" / "最好的模型"

遇到尺寸 / 比例 / banner 长图 / 文字渲染这类边界判断时,先读 [references/models.md](references/models.md)。平时不要为了谨慎频繁回看官方文档;只有在模型能力、参数支持或官方行为**确实可疑**时,才去核对 Gemini Image Generation 官方文档。SKILL.md 这里只保留默认决策,不重复能力表。

### 图像输入模式

同一个 `--image` 参数表达 3 种模式:

| 输入 | 命令形态 | 实际效果 |
|---|---|---|
| 0 张 | `q-imgen generate "prompt"` | 文生图 |
| 1 张 | `q-imgen generate "prompt" --image a.png` | 图生图 / 单图编辑 |
| 2~14 张 | `q-imgen generate "prompt" --image a.png --image b.png ...` | 多图融合 / 多图编辑 / 角色一致性 |

关键规则:

- **最多 14 张参考图**
- prompt 必须**显式指代**每张图的角色: `"第一张图的角色穿上第二张图的服装"` 比 `"融合这几张图"` 好
- 角色一致性、风格迁移、服装替换都是多图模式,不是新命令

### 迭代策略

连续修改时,把场景分成 3 种:

- **fresh reroll**: 只改 prompt,**不带任何上一轮输出**。适合探索方向、对比不同提示词、"再来一张看看"
- **anchored edit**: 把上一轮输出作为 `--image` 传回去。适合"基于上一张改""保持角色一致""别动构图只改颜色"
- **source-anchored edit**: 不用上一轮输出,而是回到原始参考图 / 设定图。适合"别被上一轮带偏""按最初设定来"

默认判断:

- 用户在**比较提示词**、试几个方向时 → `fresh reroll`
- 用户明确说"保持""延续""基于上一张改"时 → `anchored edit`
- 用户强调"回到原设定 / 原图"时 → `source-anchored edit`

不要把"连续迭代"误解成"总要喂上一张输出"。很多时候用户只是要**对比 prompt 的效果**,不是要锁定上一张的瑕疵和构图。

向用户汇报时,`fresh reroll` 更容易继续用 1 行更新;只要 `ref_images` 从无变成有,或从 A 换成 B,就必须重显完整模板。精确展示规则见 [references/output-format.md](references/output-format.md)。

### 渠道

用户可能配置了多个 channel。默认原则:

- **不要替用户选渠道**
- 不指定 `--channel` 就用默认渠道
- 只有用户明确说要换渠道,或当前渠道确实坏了,才切换

常用命令:

```bash
q-imgen channel list
q-imgen channel show
q-imgen channel show <name>
q-imgen generate "..." --channel google
q-imgen status
```

Protocol selection:

- `gemini`: Gemini-native endpoint, `POST {base_url}/models/{model}:generateContent`.
- `openai`: OpenAI-compatible chat endpoint, `POST {base_url}/chat/completions`.
- `openai_images`: OpenAI Images endpoint, `POST {base_url}/images/generations`. Use this for Yunwu `gpt-image-2` / `gpt-image-2-all`.

Yunwu `gpt-image-2` channel example:

```bash
q-imgen channel add yunwu-gpt-image --protocol openai_images --base-url https://yunwu.ai/v1 --api-key <key> --model gpt-image-2
```

OpenAI Images-specific generation controls:

```bash
q-imgen generate "poster concept" --channel yunwu-gpt-image --image-size 1024x1536 --quality high --background transparent --output-format webp --num-images 2
```

For `openai_images`, `--image` becomes `input_images`; `--image-size` is sent as the Images API `size` field. `--quality`, `--background`, `--output-format`, and `--num-images` are passed through only when set.
For `openai_images`, size shortcuts are normalized before the request: `--aspect-ratio 1:1 --image-size 2K` sends `size: "2048x2048"`; `--aspect-ratio 3:4 --image-size 2K` sends `size: "1536x2048"`.

没配渠道时,q-imgen 会自己报 `no channels configured`;这时按提示引导用户补 `channel add` 即可。

## 常用流程

### 单次生成

```bash
q-imgen generate "银发精灵弓箭手,魔法森林" -o ./output
q-imgen generate "把和服改成蓝色" --image input.png -o ./output
q-imgen generate "第一张图的角色穿上第二张图的服装" --image a.png --image b.png
```

### 批量

```bash
q-imgen batch tasks.json -o ./output --delay 1.0
```

batch task 的字段和约束读 [references/nanobanana-batch-format.md](references/nanobanana-batch-format.md)。尤其是 `prompt` 必填、`--delay` 只是串行间隔、部分失败如何看 `results`,都在那里。

### 脚本调用（Python 库）

q-imgen 除了 CLI,也可以作为 Python 包在脚本里直接调用。适用于需要自定义循环、条件逻辑、图片预处理/后处理的工作流。

```python
from q_imgen import generate

images = generate(
    "prompt",
    images=["ref.png", pil_image_obj],  # str | Path | PIL.Image 混合
    channel="my-proxy",                  # None = 默认渠道
    aspect_ratio="1:1",
    image_size="1K",
    quality=None,
    background=None,
    output_format=None,
    num_images=None,
    timeout=300,
    max_retries=3,
)

for img in images:
    img.save("output.png")  # 返回 PIL.Image,用户自己控制保存
```

关键设计:

- **输入**: `images` 接受文件路径和 `PIL.Image` 对象混合,脚本可以先用 Pillow 做预处理再传入
- **输出**: 返回 `list[PIL.Image.Image]`,不保存文件、不写 history、不打印 — 纯函数,脚本自己决定怎么处理结果
- **错误**: 失败抛异常（`ChannelError` / `GeminiError` / `OpenAIError` / `OpenAIImagesError`）,不返回 status dict
- **渠道**: 复用 CLI 的 channel 配置（`~/.q-imgen/channels.json`）,不用在脚本里写 base_url/api_key

典型场景: 角色 × 场景的批量生图、golden anchor 风格对齐、带条件分支的生图流程。这些逻辑由脚本自己控制,q-imgen 只负责单次生成这个原子操作。

完整 API 文档、CLI 与库的选择指南、以及各类示例见 [references/python-api.md](references/python-api.md)。

### 历史

只有用户**明确问历史**时才查:

- 我之前画过某张图吗
- 昨天那张输出路径是什么
- 本周 / 本月用了多少次 pro

查询时直接用 [references/history-queries.md](references/history-queries.md) 里的命令模板,不要现场现编 jq。这个 reference 同时定义了字段 schema,比如 `ref_images` / `outputs` / `workdir` 这些字段的真实含义。

如果你需要回到项目源码里追具体实现、或检查项目文档有没有更新,直接看项目地址: <https://github.com/hezi-ywt/q-imgen>。

## 向用户汇报

原则很简单:

- **不要**把 q-imgen 的原始 JSON 贴给用户
- 成功时给紧凑、可视的 markdown 摘要
- 失败时给精简后的错误 + 1-2 条具体建议
- 用模型别名(`Nano Banana 2` / `Nano Banana Pro`),不要显示完整 model ID
- 不要显示 `api_key` / `base_url`

精确模板、迭代时何时只回 1 行、`ref_images` 变化何时必须重显完整模板、以及失败模板,都在 [references/output-format.md](references/output-format.md)。

最重要的展示原则有两条:

- 用户已经写明全部结构参数时,不要复读;通常只回输出路径
- 只要这轮的 `ref_images` 和上一轮不同,就不要偷懒用 1 行,必须让用户看见你这次到底参考了什么

## 错误处理

常见错误和修法在 [references/troubleshooting.md](references/troubleshooting.md)。

处理错误时记住:

- 先把原始错误**压缩成人能看懂的一句**
- 然后给 1-2 条**按错误类型定制**的下一步
- `401` / `429` / `no images` / 网络错误 的处理方式不一样,不要用一套套话

如果错误和某个具体网关 / 渠道行为有关,先看 [references/user-notes.md](references/user-notes.md) 里有没有已有经验;任务结束后如果你又确认了一个新规律,立刻补进去,不要让同样的坑出现第二次。

## 不要做的事

- **不要替用户选渠道**。默认渠道就是默认渠道,除非用户明说或当前渠道坏了
- **不要在 q-imgen 层做 prompt 优化**。那是上层 skill / agent 的职责
- **不要为了"看起来专业"就上 Nano Banana Pro**。没命中硬理由就用 Nano Banana 2
- **不要手工拼 API URL**。直接 `q-imgen generate` / `batch`
- **不要默认去查 history**。只有用户明确问历史时才查

## References

- [references/models.md](references/models.md) — 两个模型的能力对比、ratio/size 支持、何时换 pro
- [references/nanobanana-batch-format.md](references/nanobanana-batch-format.md) — batch JSON 任务格式
- [references/output-format.md](references/output-format.md) — 给用户看的成功 / 失败 / 批量 / 迭代模板
- [references/troubleshooting.md](references/troubleshooting.md) — 常见错误与修法
- [references/history-queries.md](references/history-queries.md) — history 字段 schema 和查询命令模板
- [references/user-notes.md](references/user-notes.md) — 偏好、经验、教训、工作流(agent 维护)
- [references/python-api.md](references/python-api.md) — Python 库 API 文档、CLI 与库的选择指南、示例
- [references/update-check.md](references/update-check.md) — 检查和执行 git 更新
