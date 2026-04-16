# User-Facing Output

q-imgen 的 stdout 是机器侧 JSON。用户看到的应该是**压缩过、可视的 markdown 消息**,不是原始对象。

## 默认模板(单次成功)

```md
> **🎨 生成完成**
>
> > 银发精灵弓箭手,魔法森林
>
> - **模型** Nano Banana 2 · **渠道** my-channel
> - **比例** 3:4 · **尺寸** 2K · **参考图** 无
> - **输出** `./output/img_000.png`
```

渲染规则:

- prompt 用 blockquote 单独引出来
- 参数行尽量紧凑,同一行字段用 `·` 分隔
- 输出路径用 inline code
- `image_size == null` 时显示 **默认**,不要显示 `null` / `None` / `未指定`

## 多图融合

参考图变成单独一段:

```md
> **🎨 生成完成**
>
> > 第一张图的角色穿上第二张图的服装
>
> - **模型** Nano Banana Pro · **渠道** my-channel
> - **比例** 3:4 · **尺寸** 默认
> - **参考图**
>   - `character.png`(角色)
>   - `outfit.png`(服装)
> - **输出** `./output/merge_000.png`
```

注释规则:

- prompt 里已经写了 `"角色" / "服装" / "场景" / "第一张图"` 这类指代时,直接沿用
- 否则看文件名和上下文做最保守的解释,不要过度脑补

## 什么时候不要用完整模板

| 场景 | 改用 |
|---|---|
| `q-imgen batch` | 一行汇总 + 失败 task 列表,不要逐 task 刷屏 |
| 用户请求里已经写明全部结构参数(prompt + 模型 + 比例 + 尺寸) | 只回 `✓ /tmp/output/logo_000.png` |
| 同 prompt 快速迭代且结构性参数没变 | 只回 1 行更新 |

## 三种迭代模式

- **fresh reroll**: 只改 prompt,不带上一轮输出。适合对比提示词,例如"换个风格看看""再来一张"
- **anchored edit**: 把上一轮输出作为 `--image` 传回去。适合保持角色、延续构图、局部修改
- **source-anchored edit**: 回到原始参考图 / 设定图,不用上一轮输出。适合"别被上次带偏""还是按原设定来"

默认是 **fresh reroll**,不是 **anchored edit**。不要因为用户在连续迭代,就自动把上一张喂回去。

## 快速迭代的展示规则

结构性参数 =:

- `model`
- `channel`
- `aspect_ratio`
- `image_size`
- `ref_images`

规则:

- **全部没变** → 1 行更新:`第 2 次 → \`./output/img_001.png\`(角度调整)`
- **任意一个变了** → **重新显示完整模板**

最重要的是 `ref_images`:

- `fresh reroll` → 通常可以继续 1 行更新
- 如果开始把上一张输出喂回去当参考图,必须重显完整模板
- 如果参考图换了、增减了,必须重显完整模板
- 如果从 `anchored edit` 切回 `fresh reroll`,也应该重显完整模板,因为参考条件已经变了
- 用户必须看得见 agent 这次是不是在"凭空再画一张",还是"基于上一张继续做"

例如:

```md
> **🎨 生成完成**
>
> > 银发精灵弓箭手,魔法森林,黄昏光线(保持角色一致)
>
> - **模型** Nano Banana Pro · **渠道** my-channel
> - **比例** 3:4 · **尺寸** 2K
> - **参考图**
>   - `elf_000.png`(上一轮的输出 · 锁定人物特征)
> - **输出** `./output/elf_003.png`
```

纯提示词对比时则应该保持轻量:

```md
第 2 次 → `./output/elf_001.png`(提示词微调: 仰视,更暗)
```

## 批量任务

批量只做汇总:

```md
> **📦 批量生成完成**: 18 / 20 成功
>
> - **模型** Nano Banana 2 · **渠道** my-channel
> - **任务文件** tasks.json
> - **输出目录** `./output`
> - **失败 task**
>   - `#3` — HTTP 429
>   - `#11` — API returned no images
```

规则:

- 永远不要把整个 `results` 数组贴给用户
- 失败 task 只保留 `task_index + 错误简述`
- 错误简述要砍掉长 JSON、长堆栈、重复前缀

## 失败模板

```md
> **❌ 生成失败**
>
> > a small purple star on white background
>
> - **渠道** broken · **模型** Nano Banana Pro
> - **错误** failed to reach https://does-not-exist.invalid/v1: SSL handshake failed
> - **建议** 检查 `q-imgen channel show broken` 看 `base_url` 是否正确;或换个渠道重试:`--channel my-channel`
```

规则:

- 失败时把 **渠道放前面、模型放后面**
- 仍然保留 prompt blockquote,让用户知道是哪次请求失败
- `建议` 最多 1-2 条,必须按错误类型定制

## 不要做的事

- 不要把原始 JSON 贴给用户
- 不要显示完整 model ID
- 不要显示 `api_key` 或 `base_url`
- 不要说"我帮你优化了 prompt",除非你真的改了,而且展示的是**实际发出去的 prompt**
