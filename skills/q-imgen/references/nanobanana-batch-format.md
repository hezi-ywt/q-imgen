# Batch 任务格式

通过 `q-imgen batch tasks.json` 调用。**一个 JSON 数组**,每个元素是一个 task 对象。

## 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `prompt` | ✓ | 文生图 prompt。缺失时该 task 本地直接报错,不会发 API |
| `images` | — | 参考图路径数组(1 张 = 图生图,多张 = 融合,最多 14) |
| `aspect_ratio` | — | 不写就用 CLI `--aspect-ratio` 的默认 |
| `image_size` | — | `512` / `1K` / `2K` / `4K`,不写就让模型决定 |
| `quality` | — | `openai_images` only; passed to Images API `quality` |
| `background` | — | `openai_images` only; passed to Images API `background` |
| `output_format` | — | `openai_images` only; passed to Images API `output_format` |
| `num_images` | — | `openai_images` only; passed to Images API `n` |

OpenAI Images recommended sizes for agents:

| `image_size` | Use |
|---|---|
| `1024x1024` | square / 正方形 |
| `1536x1024` | landscape / 横版 |
| `1024x1536` | portrait / 竖版 |
| `2048x2048` | 2K square / 2K 正方形 |
| `2048x1152` | 2K landscape / 2K 横版 |
| `3840x2160` | 4K landscape / 4K 横版 |
| `2160x3840` | 4K portrait / 4K 竖版 |
| `auto` | provider default / 默认 |

Strict OpenAI Images size rules: max edge <= 3840px; width and height must both be multiples of 16px; long edge / short edge <= 3:1; total pixels must be between 655360 and 8294400.

For `openai_images`, `image_size` shortcuts are normalized before the request. Example: `aspect_ratio: "1:1", image_size: "2K"` sends `size: "2048x2048"`; `aspect_ratio: "3:4", image_size: "2K"` sends `size: "1536x2048"`.

## 示例

```json
[
  {
    "prompt": "银发精灵弓箭手,魔法森林",
    "aspect_ratio": "2:3",
    "image_size": "2K"
  },
  {
    "prompt": "猫耳男孩看星空",
    "aspect_ratio": "16:9"
  },
  {
    "prompt": "把和服改成蓝色",
    "images": ["input.png"],
    "aspect_ratio": "3:4"
  },
  {
    "prompt": "gpt-image-2 poster concept",
    "image_size": "1024x1536",
    "quality": "high",
    "background": "transparent",
    "output_format": "webp",
    "num_images": 2
  }
]
```

## 调用

```bash
q-imgen batch tasks.json -o ./output --delay 1.0
q-imgen batch tasks.json --channel google --model gemini-3-pro-image-preview
q-imgen batch tasks.json --channel yunwu-gpt-image --image-size 1024x1536 --quality high --output-format webp --num-images 2
```

CLI 层面的 `--channel` / `--model` 对整个 batch 生效(不能按 task 覆盖)。如果你需要**有些 task 用 Nano Banana 2、有些用 Nano Banana Pro**,拆成两个 batch 文件分别跑。

## 返回形状

```json
{
  "status": "ok",        // 或 "partial"
  "channel": "default",
  "model": "gemini-3.1-flash-image-preview",
  "total": 3,
  "ok": 3,
  "results": [
    {"task_index": 0, "status": "ok", "prompt": "...", "images": ["..."], ...},
    {"task_index": 1, "status": "error", "error": "..."},
    ...
  ]
}
```

部分失败(`status: "partial"`)返回 exit 0,让调用方自己看 `results` 数组决定下一步(重试失败项 / 忽略 / 报警)。

## 注意事项

- **`--delay` 是串行间隔,不是并发控制**。batch 永远是顺序执行的,需要并发请自己外部 `xargs -P` / `asyncio`。
- **缺失 `prompt` 或 `prompt` 不是字符串** 会表现为单个 task 失败,写进 `results`,不会发出 API 请求。
- **失败的 task 不会自动重试**。如果要重试,读 `results` 里 `status: "error"` 的项重新组装一个新的 tasks.json 跑。
- **rate limit (HTTP 429) 会表现为单个 task 失败**,不会中断整个 batch。批量跑完后检查 `ok` 字段,大量 429 就提高 `--delay`。
