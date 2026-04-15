# History Queries — 查日志的命令清单

q-imgen 自动把每次 `generate` / `batch` task 追加到 `~/.q-imgen/history/YYYY-MM-DD.jsonl`(按本地日期分文件)。本文档是给**人**用的查询手册:常见问题 → 现成的 shell + jq 命令模板,**复制即用**。

> **前提**:系统装了 `jq`。macOS:`brew install jq`,Linux:`apt install jq` / `dnf install jq`。

q-imgen 自身只提供一个零参数的 helper:

```bash
$ q-imgen history
/Users/ywt/.q-imgen/history/2026-04-15.jsonl
```

它**只打印今天的 log 路径**,不做任何查询 —— 因为 `tail` / `grep` / `jq` 已经是更好的查询工具,q-imgen 不重新发明轮子。

## 文件布局

```
~/.q-imgen/history/
├── 2026-04-13.jsonl     ← 那一天所有调用
├── 2026-04-14.jsonl
├── 2026-04-15.jsonl     ← 今天
└── ...
```

每行 = 一次调用,batch 里每个 task 也是单独一行。文件按**本地日期**切分,如果一次调用 23:59 启动 00:01 完成,落到**新一天**的文件(`ts` 字段记的是写入时刻)。

## 常见查询

> 所有命令假设 `jq` 已装。所有日期变量都是 macOS `date` 语法,Linux 把 `date -v-1d` 换成 `date -d "yesterday"`。

### 我今天生成了什么?

```bash
cat $(q-imgen history) | jq -r '"\(.ts) \(.prompt)"'
```

### 我昨天成功生成了哪些?

```bash
yesterday=$(date -v-1d +%Y-%m-%d)
jq -r 'select(.status == "ok") | "\(.ts) \(.prompt) → \(.outputs[0])"' \
  ~/.q-imgen/history/${yesterday}.jsonl
```

### 本月所有失败,带错误消息

```bash
month=$(date +%Y-%m)
jq -r 'select(.status == "error") | "\(.ts) [\(.channel)] \(.error)"' \
  ~/.q-imgen/history/${month}-*.jsonl
```

### 上一次用某条 prompt 是什么时候?

```bash
jq -r 'select(.prompt == "银发精灵弓箭手,魔法森林") | .ts' \
  ~/.q-imgen/history/*.jsonl | tail -1
```

### 找所有包含某关键词的 prompt(连同 output 路径)

```bash
jq -r 'select(.prompt | contains("精灵")) | "\(.ts)  \(.outputs[0])  \(.prompt)"' \
  ~/.q-imgen/history/*.jsonl
```

### 某项目(workdir)用了多少次 pro 模型?

```bash
jq -r '
  select(.workdir == "/Users/ywt/comic-A"
         and (.model | contains("pro"))
         and .status == "ok")
  | .ts
' ~/.q-imgen/history/*.jsonl | wc -l
```

### 按模型统计本月调用次数

```bash
month=$(date +%Y-%m)
jq -r .model ~/.q-imgen/history/${month}-*.jsonl | sort | uniq -c | sort -rn
```

### 各 workdir 的活跃度(全历史)

```bash
jq -r .workdir ~/.q-imgen/history/*.jsonl | sort | uniq -c | sort -rn
```

### 实时盯今天的活动

```bash
tail -f $(q-imgen history) | jq -c '{ts, prompt, status}'
```

### 平均延迟(本周)

```bash
jq '.latency_ms' ~/.q-imgen/history/2026-04-1[2-8].jsonl |
  awk '{sum+=$1; n+=1} END {printf "%.0f ms over %d calls\n", sum/n, n}'
```

### 失败率(按渠道)

```bash
jq -r '"\(.channel) \(.status)"' ~/.q-imgen/history/*.jsonl |
  sort | uniq -c | sort -rn
```

## 记录字段 schema

| 字段 | 类型 | 说明 |
|---|---|---|
| `ts` | string | 写入时刻的 ISO8601 本地时间(带时区),例 `2026-04-15T19:09:23+08:00` |
| `prompt` | string | 用户传给 `generate` 的原始 prompt |
| `model` | string | 完整 model ID(例 `gemini-3.1-flash-image-preview`),不是别名 |
| `channel` | string | 渠道名 |
| `protocol` | `"gemini" \| "openai"` | 协议 |
| `aspect_ratio` | string | 例 `3:4` |
| `image_size` | string \| null | 例 `2K`,未指定时为 `null` |
| `ref_images` | string[] | 参考图**绝对路径**数组,空数组 = 文生图 |
| `outputs` | string[] | 生成的图片**绝对路径**;失败时空数组 |
| `status` | `"ok" \| "error"` | 调用状态 |
| `error` | string | **仅** `status == "error"` 时存在,错误消息 |
| `latency_ms` | number | 端到端毫秒数(从 q-imgen 发请求到拿到响应) |
| `workdir` | string | 调用时的项目根目录:有 `.git` 的话取 git root,否则取 `cwd` |

**故意不在记录里的字段**:
- `api_key` 任何形式 —— 渠道名足以反查
- `base_url` —— 同上
- 原始 API 请求 / 响应 body —— 那是 debug log,不是审计 log
- 参考图的 base64 内容 —— 只记路径

### 一条完整记录例子

```json
{
  "ts": "2026-04-15T19:09:23+08:00",
  "prompt": "银发精灵弓箭手,魔法森林",
  "model": "gemini-3.1-flash-image-preview",
  "channel": "yunwu-gemini",
  "protocol": "gemini",
  "aspect_ratio": "3:4",
  "image_size": "2K",
  "ref_images": [],
  "outputs": ["/Users/ywt/comic-A/output/elf_000.png"],
  "status": "ok",
  "latency_ms": 17826,
  "workdir": "/Users/ywt/comic-A"
}
```

## 性能小贴士

典型用量下日志文件每天 < 100KB,一年攒下来 < 30MB,`jq` 处理这种规模毫无压力。

如果你跑了几年攒到几百 MB 才需要操心:

- **先 grep 再 jq**:`grep '"workdir":"/Users/ywt/comic-A"' *.jsonl | jq ...` 能少处理几个数量级
- **限定日期范围**:不要无脑 `*.jsonl`,用 `2026-04-*.jsonl` 收窄到月
- **手工归档老文件**:`mkdir archive && mv 2024-*.jsonl archive/`(归档后查询要明确指向 archive 目录)

## 不要用这个 log 做的事

- ❌ **缓存**:不要"如果同 prompt 上周已生成过就跳过这次调用"。模型有随机性,同 prompt 不同输出是正常的,缓存反而是错的优化
- ❌ **手工编辑或删除某条记录**:这是 append-only 审计日志,改了就让"历史"对不上现实
- ❌ **当 portfolio 导出**:`outputs` 字段是绝对路径,只在你的机器上有效;要 portfolio 自己复制图片文件,不要 share 这个 log
- ❌ **把它当 prompt 库**:它是历史,不是模板;真要积累优秀 prompt 应该单独建一个 markdown 文件
