# Troubleshooting

q-imgen 的错误消息已经做过脱敏。你的工作不是转述原文,而是把它变成:

1. 人能看懂的一句错误
2. 1-2 条具体下一步

## 常见错误

| 错误消息(stderr 或 JSON.error) | 原因 | 修法 |
|---|---|---|
| `no channels configured` | 首次使用,没配 channel | 问用户要 `base_url / api_key / protocol / model`,按提示做 `q-imgen channel add` |
| `no such channel: 'X'` | `--channel` 名字拼错或未配置 | `q-imgen channel list` 看现有渠道 |
| `HTTP 401` / `invalid api key` | key 失效或配错了 | `q-imgen channel show <name>` 看当前渠道;向用户确认 key;必要时 `channel add --force` 覆盖 |
| `HTTP 429` / rate limit | 打太快或配额打满 | 增大 `--delay`;换 channel;稍后重试;大量任务优先用 Nano Banana 2 |
| `unsupported image type for Gemini` | 参考图不是 PNG/JPEG/WebP | 先转格式 |
| `reference image not found` | 路径错 | 用绝对路径,先确认文件存在 |
| `API returned no images` | 模型拒绝出图、被过滤、或 prompt 太糊 | 改 prompt;必要时换 Nano Banana Pro |
| `failed to reach ...` / DNS / SSL / timeout | base_url 错、网络异常、代理挂了 | 先检查 channel 的 `base_url`;再换 channel |

## 错误压缩规则

- 去掉长堆栈
- 去掉嵌套 JSON
- 去掉重复前缀
- 保留真正能帮用户判断下一步的那一层

例如:

- 不好:`HTTP 429: {"error":{"message":"GenerateContentRequest.contents[0].parts[0].data must have one initialized field"...`
- 好:`HTTP 429: prompt 是空字符串`

## 建议字段怎么写

按错误类型给具体动作:

- `401` → 检查 key / 覆盖 channel
- `429` → 加 `--delay` / 换 channel / 稍后重试
- `API returned no images` → 改 prompt / 换 pro
- 网络错误 → 检查 `base_url` / 换 channel

不要写成万能套话:

- 不好:`请检查配置并稍后重试`
- 好:`检查 \`q-imgen channel show broken\` 里的 \`base_url\`;如果渠道没问题,换 \`--channel my-channel\` 重试`
