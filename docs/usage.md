# Usage

`q-imgen` 是一个原子 nanobanana CLI。只有两类命令:

- `q-imgen generate` / `q-imgen batch` —— 真正生图
- `q-imgen channel {add, list, show, use, rm}` —— 管理 endpoint 渠道

## Install

```bash
git clone <repo-url>
cd q-imgen
python -m pip install -e .
q-imgen --help
```

本地无安装的备用方式:

```bash
PYTHONPATH=src python -m q_imgen --help
```

## First-time setup

没配置任何渠道时,`q-imgen generate` 会报:

```
[q-imgen] no channels configured. Run `q-imgen channel add <name> --protocol {gemini|openai} --base-url URL --api-key KEY --model M` to create one.
```

按它说的做即可。第一个添加的渠道会自动成为默认。

### 协议怎么选

| 你用的端点 | protocol |
|---|---|
| `https://generativelanguage.googleapis.com/v1beta`(Google 官方) | `gemini` |
| 自建 / 第三方代理,返回 Gemini `generateContent` 格式 | `gemini` |
| 代理网关,返回 OpenAI `chat/completions` 格式(含 `choices[].message.images`) | `openai` |

不确定的话就先 `--protocol openai` 试一次,报错里会明确告诉你哪里不对(404 / 400 / 格式不对都会带到 stderr)。

### 添加渠道的例子

```bash
# 代理网关,OpenAI 兼容
q-imgen channel add proxy-a \
  --protocol openai \
  --base-url https://sd.rnglg2.top:30000/v1 \
  --api-key sk-xxx \
  --model gemini-3.1-flash-image-preview

# Google 原生
q-imgen channel add google \
  --protocol gemini \
  --base-url https://generativelanguage.googleapis.com/v1beta \
  --api-key AIzaSy... \
  --model gemini-3.1-flash-image-preview
```

## Generate

```bash
# 文生图
q-imgen generate "银发精灵弓箭手,魔法森林" -o ./output

# 图生图 / 图像编辑(一张参考图)
q-imgen generate "把和服改成蓝色" --image input.png -o ./output

# 多图融合
q-imgen generate "把 A 的发型换成 B 的风格" --image a.png --image b.png -o ./output

# 指定渠道(不改默认)
q-imgen generate "..." --channel google

# 临时换模型(不改渠道存储)
q-imgen generate "..." --model some-other-model

# 指定长宽比 / 尺寸 / 前缀
q-imgen generate "..." --aspect-ratio 16:9 --image-size 2K --prefix scene
```

### 输出形状

成功:
```json
{
  "status": "ok",
  "channel": "proxy-a",
  "model": "gemini-3.1-flash-image-preview",
  "prompt": "...",
  "images": ["./output/img_000.png"],
  "ref_images": []
}
```

失败(exit 1):
```json
{
  "status": "error",
  "channel": "proxy-a",
  "model": "gemini-3.1-flash-image-preview",
  "prompt": "...",
  "error": "API request failed with HTTP 401: ..."
}
```

## Batch

task 文件是一个 JSON 数组,每项可覆盖 `prompt` / `images` / `aspect_ratio` / `image_size`:

```json
[
  { "prompt": "银发精灵弓箭手,魔法森林", "aspect_ratio": "2:3", "image_size": "2K" },
  { "prompt": "猫耳男孩看星空", "aspect_ratio": "16:9" },
  { "prompt": "把和服改成蓝色", "images": ["input.png"], "aspect_ratio": "3:4" }
]
```

```bash
q-imgen batch tasks.json -o ./output --delay 1.0
q-imgen batch tasks.json --channel google --model override-model
```

每个 task 都必须有 `prompt` 字段;缺失或类型不对会作为该 task 的本地错误出现在 `results` 里,不会发 API。

输出:
```json
{
  "status": "ok",        // or "partial" if some tasks failed
  "channel": "proxy-a",
  "model": "gemini-3.1-flash-image-preview",
  "total": 3,
  "ok": 3,
  "results": [ {task_index: 0, status: "ok", ...}, ... ]
}
```

部分失败返回 **exit 0** + `status: "partial"`,让调用方看 `results` 数组决定下一步。全部失败才 exit 1。

## History (0.3.0+)

q-imgen 自动把每次 `generate` / `batch` task 追加到 `~/.q-imgen/history/YYYY-MM-DD.jsonl`(按本地日期分文件)。失败和成功都记录,内部用 `fcntl.flock` 保证并发写入安全。

```bash
q-imgen history                                # 打印今天的 log 文件路径
cat $(q-imgen history) | jq -r .prompt         # 今天我画了什么
tail -f $(q-imgen history) | jq -c             # 实时盯
grep '"status":"error"' ~/.q-imgen/history/*.jsonl   # 跨天查所有失败
```

`q-imgen history` 只有这一个用法 —— 打印今天的路径。**所有真实查询是 shell + jq 的工作**,详细命令模板和字段 schema 见 [history-queries.md](../skills/q-imgen/references/history-queries.md)。

写日志失败不会让 `generate` 失败,会在 stderr 输出一行 `[q-imgen] warning: history append failed: ...` 然后继续。

## Channel commands

```bash
q-imgen channel list              # 列出所有渠道,* 标记默认
q-imgen channel show              # 显示默认渠道细节(api_key 脱敏)
q-imgen channel show <name>       # 显示指定渠道
q-imgen channel use <name>        # 把某渠道设为默认
q-imgen channel rm <name>         # 删除渠道;如果删的是默认,自动选另一个
q-imgen channel add <name> --protocol ... --base-url ... --api-key ... --model ...
q-imgen channel add <name> ... --force   # 覆盖同名渠道
```

## Storage

`~/.q-imgen/channels.json`(权限 600,目录权限 700)。人类可读,紧急情况下可以手编:

```json
{
  "default": "proxy-a",
  "channels": {
    "proxy-a": {
      "protocol": "openai",
      "base_url": "https://sd.rnglg2.top:30000/v1",
      "api_key": "sk-...",
      "model": "gemini-3.1-flash-image-preview"
    }
  }
}
```

## I/O contract

| 来源 | 内容 |
|---|---|
| `generate` / `batch` stdout | 纯 JSON 结果(单次调用一个对象,batch 一个带 `results` 的对象) |
| `channel show` stdout | JSON 对象 |
| 其他 `channel` 子命令 stdout | 人类可读文本 |
| stderr | `[q-imgen] ...` 诊断行 |
| exit 0 | 成功(包括 batch 部分失败) |
| exit 1 | 任何硬失败(API 错、文件不存在、渠道不存在、batch 全挂) |

所有错误消息都会 **脱敏 api_key** 和 `Bearer <token>`,不会泄漏到日志。

## Testing

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

**零 API 成本**,所有 HTTP 调用被 mock。没有 live smoke test —— 如果需要真实验证,自己加 `tests/live_*.py` 并用环境变量 gate。
