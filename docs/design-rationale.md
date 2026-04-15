# Design Rationale

`q-imgen` 参考 `qsense` 的设计哲学：把自己做成一个小而清晰的基础设施原语，而不是一个包办一切的系统。

## Core principles

1. **单一职责**：只负责统一入口与引擎路由。
2. **原子引擎保留**：`midjourney`、`nanobanana` 继续作为独立 CLI 存在。
3. **组合优先**：由上层 agent 或脚本组合能力，而不是把所有逻辑塞进 wrapper。
4. **边界清晰**：运行时代码、开发文档、agent skill 分层存放。
5. **配置属于 wrapper，不属于引擎抽象层**：wrapper 可以提供持久配置与 env 注入，但不统一底层引擎协议。

## Why not a monolithic CLI

如果一开始就把所有引擎参数揉成统一 DSL，会带来两个问题：

- 丢失底层引擎的真实能力边界
- 让 wrapper 变成高耦合翻译层，后续维护成本变高

所以当前版本选择更朴素的策略：**先统一入口，再逐步抽象共性**。

## Why config lives here

`q-imgen` 参考 `qsense`，提供 `~/.q-imgen/.env` 作为稳定的用户级配置入口。

这个配置层的职责非常有限：

- 给 `midjourney` / `nanobanana` 提供默认环境变量
- 支持 `init` / `config` 这类 first-run 体验
- 不试图统一两个引擎的参数模型

也就是说，配置层提升的是**可用性**，不是**抽象层级**。

## Why testing is layered

图像生成和 `qsense` 不一样：真实验证通常意味着真实调用模型，而这会直接带来成本。

所以 `q-imgen` 的测试策略不能把“真实生图”当成默认验证手段，而应该分层：

1. **默认测试**：验证 config、dispatch、routing，这些是 wrapper 的核心职责
2. **手动 smoke test**：只对当前主用引擎做少量真实调用，确认链路没断

当前项目里，这个策略具体落成：

- Nano Banana 是主 smoke test 对象
- Midjourney 当前只做非 live 测试

这样做的原因不是偷懒，而是为了让测试成本和 wrapper 的真实职责匹配。
