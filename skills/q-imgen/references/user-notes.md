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

- **不确定 OpenAI 协议参数是否生效就不要用,默认走 Gemini 原生协议** (2026-04-19)
  任何渠道的 OpenAI 端点,在没有明确实测确认它支持 `aspect_ratio` / `image_size` 等参数前,不要假设它能正常工作。**默认选 Gemini 原生协议**,只有明确验证过 OpenAI 协议对该参数有效才用。

- **真实联调优先用 gateway 渠道,不要先用其它默认代理** (2026-04-16)
  如果只是要验证 q-imgen 真实生图链路,优先显式指定 `gateway-gemini` 或 `gateway-openai`;不要先拿名为 `default` 的自定义代理渠道做联调假设。

## Patterns

- **proxy-gateway.example 两个协议都能工作,但 base_url 不同** (2026-04-15)
  - Gemini 原生:`base_url = https://proxy-gateway.example/v1beta`,认证 `Bearer sk-xxx`,模型 ID 直接用 Google 官方的(`gemini-3-pro-image-preview` / `gemini-3.1-flash-image-preview`)
  - OpenAI 兼容:`base_url = https://proxy-gateway.example/v1`,同一把 key,同样的模型 ID
  - 同一把 `sk-` 开头的 key **两个协议都能用**,不需要分两把

- **gateway 的 OpenAI 端点同时忽略 `aspect_ratio` 和 `image_size`** (2026-04-17 复测)
  同一个 gateway、同一把 key、同一个模型 ID,只换协议:
  - **aspect_ratio**:`--aspect-ratio 3:4` / `1:1` 在 OpenAI 端点一律被忽略,固定吐 ≈16:9。Gemini 协议正确返回对应比例。
  - **image_size**:`--image-size 1K/2K/4K` 在 OpenAI 端点也一律被忽略,固定吐 1408×768(≈1K)。Gemini 协议下 `2K` 正确返回 2048×2048。

  原因在代理侧:gateway 的 OpenAI 兼容层没把这两个参数映射到下游 Gemini 的 `imageConfig`。**需要控制分辨率或长宽比时只能用 Gemini 协议渠道**,不要用 gateway 的 OpenAI 渠道(它现在只能当 1K + ≈16:9 出图器)。

- **yunwu.ai 的 OpenAI 端点参数行为不稳定,不建议依赖** (2026-04-19)
  yunwu 的 OpenAI 端点实测结果:
  - 基础出图可用,图片格式正确
  - `image_config.aspect_ratio` / `image_config.image_size` 端点本身接受但**实际不生效**,还可能引发超时
  - Gemini 原生端点(`base_url = https://yunwu.ai/v1beta`)完全可靠,尊重所有参数

  **原则:不确定 OpenAI 协议参数是否生效就不要用,默认走 Gemini 原生协议。**

## Observations

<!-- 不确定的新观察,待验证后决定是升为 Pattern 还是删除。 -->

_(暂无记录)_

## Lessons

<!-- 踩过的坑和对策。"做 X 会导致 Y,应该 Z"。 -->

_(暂无记录)_

## Workflows

_(暂无记录)_
