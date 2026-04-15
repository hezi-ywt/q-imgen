# User Notes

**Agent-maintained memory.** SKILL.md 是稳定规则,这里记录会影响下次决策的真实经验。**每次在本轮里第一次使用 q-imgen skill 时先读;任务结束后如果出现新的稳定偏好、渠道规律或重复性坑,立刻更新。**

只记:
- 用户或项目的明确偏好
- 会影响模型 / 渠道 / 参数选择的稳定规律
- 反复出现、但还不值得提升到 SKILL.md 的经验

不记:
- 临时调试过程
- 代码实现细节 / 函数名 / parser 结构
- 一次性现象或容易过时的运行数据

## Preferences

- **模型别名不要用缩写,统一写全称** (2026-04-16)
  面向用户展示和 skill 文档里,统一用 `Nano Banana 2` / `Nano Banana Pro`,不要写 `nanobanana2` / `nanobanana pro`。

- **一般不要频繁回看官方 API 文档** (2026-04-16)
  默认先用 skill 里的既有规则和 `references/models.md` 判断;只有在模型能力、参数支持或官方行为确实可疑时,才去核对 Gemini Image Generation 官方文档。

- **真实联调优先用 yunwu 渠道,不要先用其它默认代理** (2026-04-16)
  如果只是要验证 q-imgen 真实生图链路,优先显式指定 `yunwu-gemini` 或 `yunwu-openai`;不要先拿名为 `default` 的自定义代理渠道做联调假设。

## Patterns

- **yunwu.ai 两个协议都能工作,但 base_url 不同** (2026-04-15)
  - Gemini 原生:`base_url = https://yunwu.ai/v1beta`,认证 `Bearer sk-xxx`,模型 ID 直接用 Google 官方的(`gemini-3-pro-image-preview` / `gemini-3.1-flash-image-preview`)
  - OpenAI 兼容:`base_url = https://yunwu.ai/v1`,同一把 key,同样的模型 ID
  - 同一把 `sk-` 开头的 key **两个协议都能用**,不需要分两把

- **yunwu 的 OpenAI 端点不尊重 `image_config.aspect_ratio`** (2026-04-15)
  同样的 prompt、同样的 `--aspect-ratio 3:4`:Gemini 协议返回 896×1200(3:4 正确),OpenAI 协议返回 1408×768(≈16:9,被忽略了)。**需要严格控制 aspect ratio 时用 Gemini 协议渠道**,不要用 yunwu 的 OpenAI 渠道。

## Observations

<!-- 不确定的新观察,待验证后决定是升为 Pattern 还是删除。 -->

_(暂无记录)_

## Lessons

<!-- 踩过的坑和对策。"做 X 会导致 Y,应该 Z"。 -->

_(暂无记录)_

## Workflows

_(暂无记录)_
