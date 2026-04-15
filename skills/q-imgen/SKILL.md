---
name: q-imgen
description: >
  q-imgen：统一的多引擎图像生成 CLI 封装 Skill。当用户需要调用 Midjourney、Gemini (Nano Banana) 或其他图像生成 API 生成、编辑、批量处理图片时使用。
  触发场景包括：文生图、图生图、图像编辑、批量生成、查询生成任务状态、混合多张图片等任何涉及调用外部图像生成服务的请求。
metadata:
  requires:
    bins: ["q-imgen"]
  cliHelp: "q-imgen --help && q-imgen midjourney --help && q-imgen nanobanana --help"
---

# q-imgen

> `q-imgen` 是统一入口，底层仍复用 `midjourney` 和 `nanobanana` 两个原子 CLI。Skill 只负责帮助 agent 做**引擎选择、命令构造和结果读取**，不把 wrapper 描述成完整工作流系统。

## 设计哲学

1. **原子化 CLI + 统一品牌层**：wrapper 只统一入口，不吞掉底层能力边界。
2. **面向 Agent**：优先依赖 stdout JSON / stderr 进度与错误。
3. **组合优先**：上层 agent 负责决定用哪个引擎，wrapper 不做复杂编排。

## 边界

- `q-imgen` runtime：只做 engine 路由与参数透传
- `q-imgen` runtime：支持 `midjourney` / `nanobanana` 与别名 `mj` / `banana`
- `q-imgen` runtime：支持从 `~/.q-imgen/.env` 读取持久配置并注入子进程环境
- 底层 engine：负责参数解析、网络调用、输出格式与文件落盘
- 上层 agent / script：负责何时生成临时文件、如何组织批量任务、如何继续后处理

## 引擎速查

| 引擎 | CLI | 适用场景 |
|------|-----|----------|
| Midjourney | `q-imgen midjourney ...` / `q-imgen mj ...` | 高质量动漫风格、异步任务、U1-U4 后处理 |
| Gemini (Nano Banana) | `q-imgen nanobanana ...` / `q-imgen banana ...` | 快速生成、图生图、多图融合 |

## 路由决策

1. 用户指定引擎 → 直接使用。
2. 查询状态 / Upscale / Variation / Reroll → Midjourney。
3. 图生图 / 多图融合 / 快速编辑 → Gemini。
4. 纯文生图且强调高质量动漫风格 → 默认 Midjourney。

## 常用调用

```bash
q-imgen midjourney imagine "prompt" --output-dir ./output
q-imgen nanobanana generate "prompt" --output-dir ./output
q-imgen nanobanana batch tasks.json --output-dir ./output --delay 1.0
q-imgen mj --help
q-imgen banana --help
```

如果需要批量任务文件，默认由上层调用方创建 `tasks.json`，`q-imgen` 只负责把命令转发给底层 engine。

## References

- 批量任务 JSON 格式：`references/nanobanana-batch-format.md`
