# Models — Nano Banana 2 vs Nano Banana Pro

本文档记录 q-imgen 目前使用的两个 Google Nano Banana 模型的细节能力。**会随 Google 更新而变,所以放在 references/ 而不是 SKILL.md**。

官方文档(只在模型能力 / 参数支持 / 官方行为确实可疑时再核对,不要平时反复去看): <https://ai.google.dev/gemini-api/docs/image-generation?hl=zh-cn>

## 模型 ID 对照

| 本文称呼 | 完整 model ID | Google 命名 |
|---|---|---|
| **Nano Banana 2**(默认) | `gemini-3.1-flash-image-preview` | Nano Banana 2 |
| **Nano Banana Pro** | `gemini-3-pro-image-preview` | Nano Banana Pro |

我们不用 `gemini-2.5-flash-image`(老版 Nano Banana)—— 如果某个用户的渠道配的是它,可以用,但没必要主动切过去,Nano Banana 2 全面优于它。

## 能力矩阵

| 能力 | Nano Banana 2 | Nano Banana Pro |
|---|---|---|
| 文生图 | ✓ | ✓ |
| 图像编辑 | ✓ | ✓ |
| 多轮编辑 | ✓ | ✓ |
| 最多 14 张参考图 | ✓ | ✓ |
| Google Search grounding(模型自己查资料) | ✓ | ✓ |
| Image Search grounding(图像搜索辅助) | ✓ | — |
| Thinking mode(基础) | ✓ | ✓ |
| Advanced reasoning(多步复杂指令) | — | ✓ |
| 图中渲染文字的质量 | 一般 | 显著更好 |
| 延迟 | 低 | 明显更高 |
| 成本(典型定价) | 低 | 明显更高 |

(q-imgen 当前 CLI 不暴露 grounding / thinking 的开关 —— 如果需要再加 `--use-search` 这类 flag。)

## 分辨率与长宽比

### 两者都支持的 aspect ratio

`1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9`

### 仅 Nano Banana 2 额外支持

`1:4`, `4:1`, `1:8`, `8:1` —— 非常长条的场景(banner、长图、海报竖条等)走 Nano Banana 2,Nano Banana Pro 不支持。

### Image size

| size flag | Nano Banana 2 | Nano Banana Pro |
|---|---|---|
| `512` | ✓ | — |
| `1K` | ✓ | ✓ |
| `2K` | ✓ | ✓ |
| `4K` | ✓ | ✓ |

`512` 仅 Nano Banana 2 有 —— 快速草图 / 批量缩略图 / 低分辨率迭代走 Nano Banana 2 + `--image-size 512`,非常便宜。

默认不加 `--image-size` 时,模型自己决定(通常 1K)。

## 什么时候用 Nano Banana Pro —— 可执行的判断

**默认 Nano Banana 2**。下面这些情况换 Nano Banana Pro:

1. **图里要清晰可读的文字** —— logo、海报、表情包、产品包装、界面 mockup。Nano Banana 2 经常会把字画糊或写错。
2. **Prompt 里有多层条件或时序** —— "画一个 X,然后在左上角加 Y,如果背景是深色就用浅色描边"。Nano Banana 2 容易漏执行一部分。
3. **上一次 Nano Banana 2 出的结果明显不对(姿势错乱、元素漏画、指令被忽略)** —— 不要改 prompt 死磕 Nano Banana 2,直接 `--model gemini-3-pro-image-preview` 重跑。
4. **用户明说"用 Nano Banana Pro"或"用最好的模型"或"质量优先"** —— 别问原因。

**不要用 Nano Banana Pro 的场景**(反例):

- "随便生成一张猫的图" → Nano Banana 2
- 批量 10+ 张 → Nano Banana 2(Nano Banana Pro 慢且贵)
- 只是改颜色 / 换背景 / 裁剪 → Nano Banana 2(简单编辑不需要推理)
- 短条长条 banner(`1:4` / `4:1` 等)→ 只能 Nano Banana 2
- 512px 小图 → 只能 Nano Banana 2

## 渠道 × 模型的关系

一个 channel 存的是**默认 model**,但调用时可以用 `--model` 临时覆盖。典型做法:

- 把渠道的默认 model 设成 Nano Banana 2(日常用)
- 需要 Nano Banana Pro 时用 `q-imgen generate "..." --model gemini-3-pro-image-preview` 覆盖
- 不需要为 Nano Banana Pro 单独建一个渠道

除非:某个渠道的 api_key 只允许其中一个模型(计费原因)—— 那时建两个渠道是合理的。

## 失败模式与对策

| 现象 | 可能原因 | 对策 |
|---|---|---|
| `HTTP 429` | Nano Banana 2 配额比 Nano Banana Pro 宽松但也会打到上限 | 加 `--delay`、换渠道、或短时间内少量请求 |
| 图中文字乱码 | Nano Banana 2 的已知弱点 | 换 Nano Banana Pro |
| 多步指令被忽略一部分 | Nano Banana 2 对复杂指令的 attention 有限 | 换 Nano Banana Pro,或拆成多轮编辑 |
| 参考图风格没影响到输出 | 参考图太多(>5)Nano Banana 2 可能取舍 | 精简到 2-3 张核心参考,或换 Nano Banana Pro |
| `unsupported image type` | 非 PNG/JPEG/WebP | 先转格式,q-imgen 不做格式转换 |

## 未来可能加的 CLI 开关

**现在没加,等真的需要再说**,记在这里避免重复提议:

- `--use-search` / `--grounding` — 打开 Google Search / Image Search grounding
- `--thinking-budget N` — 控制 thinking mode 的推理预算
- `--negative-prompt` — 反向提示词

加这些 flag 前确认两件事:(a) Google API 确实稳定支持;(b) 有真实场景反复需要。**否则 YAGNI,不加。**
