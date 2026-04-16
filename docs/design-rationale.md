# Design Rationale

`q-imgen` 是一个**最小可用的 nanobanana CLI**,设计参考 `qsense` 的"原子原语"哲学:自己做成一个小而清晰的基础设施原语,而不是一个包办一切的系统。

## Core principles

1. **单一职责**:只做图像生成一件事 —— 发一个 prompt(可带参考图),拿回图片文件路径。
2. **渠道是唯一路由抽象**:每个渠道 = `(protocol, base_url, api_key, model)`。调用时由调用方显式选择渠道,q-imgen 不做启发式。
3. **协议在 CLI 内部实现,不再通过 subprocess 派发**:两个 client 模块(`gemini_client` / `openai_client`)各自负责一种协议,结果是 q-imgen 完全自包含。
4. **组合优先**:批量、prompt 工程、角色一致性、评分与挑选 —— 全部是调用方的职责,不在 q-imgen 里做。
5. **Agent I/O 契约不可协商**:`generate` / `batch` 的 stdout 纯结果 JSON,stderr `[q-imgen] ...` 诊断,exit 0/1。部分失败的 batch 返回 `status: "partial"` + exit 0,让调用方看 `results` 决定。`channel` 管理命令保持人类可读,`channel show` 例外返回 JSON。

## Why two protocols, not a unified abstraction

用户实际用到的两种 API 协议在 payload 形状和端点上**根本不兼容**:

| 协议 | 端点 | 请求体 |
|---|---|---|
| `gemini` | `/models/{model}:generateContent` | `{contents: [{parts: [...]}], generationConfig: {...}}` |
| `openai` | `/chat/completions` | `{model, messages: [...], image_config: {...}}` |

试图统一成"一种抽象"会把字段强行映射到不存在的对应关系,造成沉默失真(比如 `aspect_ratio` vs `aspectRatio` vs Google 内部的 `imageConfig.aspectRatio`)。**不统一反而是诚实的做法**。

两个 client 在代码层面独立演化,共同点只有"返回成功路径或抛一个 `*Error` 异常"这一个接口。

## Why the OpenAI client accepts multiple response shapes

更头疼的是:**OpenAI `/chat/completions` 端点返回生成图根本没有约定俗成的形状**。我们实际遇到的中转网关里,每家都不一样:

| 形状 | 谁这样 | 字段位置 |
|---|---|---|
| 1 | proxy.example.com 等少数代理 | `choices[0].message.images[].image_url.url` |
| 2 | **大多数 one-api / new-api / litellm / various proxies fork** | `choices[0].message.content` 字符串里的 `![...](url)` markdown |
| 3 | 部分对称使用 vision 输入格式的代理 | `choices[0].message.content` 数组里的 `{"type": "image_url", ...}` parts |

`openai_client._extract_images_from_response` **同时扫描三个位置并 dedup by URL**。不做"先 try shape 1,失败再 try shape 2"的分支,因为:

1. **形状 2 是事实主流**(various proxies 实测就是这样,litellm 和大多数 one-api 派生也是)。把它作为 fallback 是优先级搞反。
2. **同一响应里可能两种位置都有图**(罕见但合法)。merge + dedup 比"first wins"更正确。
3. **新增一种形状的成本很低**:在 `_extract_images_from_response` 里加一行调用 `_add(url)` 即可,不影响其它路径。

新中转返回的形状如果还不在已知三种之内,加入流程:抓 `q-imgen generate` 失败时的原始响应,看图片实际在哪个 JSON 路径,在 `_extract_images_from_response` 里补一个分支 + 一个回归测试。**不要**为新形状重写整个解析器。

## Why channels instead of config fields

早期版本有一张 `FIELDS` 表,10 个字段(`MJ_API_KEY` / `NANOBANANA_OPENAI_BASE_URL` / ...),分散在 7 处代码里手写。要加一个 "profile B 用不同的 base_url" 就得加 7 个新字段、改 7 处代码。

现在的模型是:**channel 记录是一个 dataclass**,字段是字段,数量不重要。加一个新渠道 = `q-imgen channel add`,没有代码改动。10 个字段 → 1 个 dataclass。

这也是为什么没有 `env var 优先级覆盖` 这层逻辑 —— 一个 channels.json 就是全部真相,不再有"进程 env vs 文件 vs 默认值"的三层优先级。

## Why the Gemini client absorbs the original nanobanana code

老版本通过 `subprocess.run([python, -m, nanobanana, ...])` 派发到一个本地包,这个本地包不在 pip 管理里,q-imgen 的"可安装"性实际上是假的 —— 任何没有那个本地包的环境都跑不起来。

现在 `gemini_client.py` 从那个本地包吸收了约 120 行核心代码(HTTP + Gemini payload + 认证分支),q-imgen 彻底不依赖外部包。原始的 nanobanana 包保留不动,两者独立演化。

## Why no batch orchestration beyond sequential+delay

`q-imgen batch` 只做最简单的事:顺序跑每个 task,task 之间 `sleep(delay)`,收集结果,整体报告 ok / partial / error。task 必须是对象且必须带 `prompt`;缺失字段的 task 在本地直接记成错误,不会发 API。

没有并发、没有限流、没有重试队列、没有断点续传。这些都是"上一层"的职责 —— 如果调用方要并发,用 `xargs -P` 或自己写几行 `asyncio`;要断点续传,读 `results` 数组跳过 `status: "ok"` 的。

**把最简单的 batch 放在 CLI 里**是为了让"一次跑 20 张图"这种常见场景不必让调用方自己手写 for 循环;**不放复杂 batch**是为了避免 CLI 变成半残的工作流引擎。

## Why no heuristic routing

早期有过 `choose_engine(operation, priority, style)` 这种启发式路由函数(54 行,根据 `priority="speed"` / `"quality"` 和 `"anime"` 风格选引擎)。**最终删掉了**,因为:

- 启发式会在"勉强够用"和"完全错位"之间漂移,没有明确的失败信号
- 调用方比 q-imgen 更了解自己要什么 —— 它应该直接说 `--channel proxy-a`
- 一旦渠道本身变成可命名实体,调用方就有了表达意图的地方

q-imgen 保持"哑"是故意的。策略属于 skill / agent / 业务脚本,不属于基础设施原语。

## Testing strategy

图像生成与 `qsense` 不同:真实调用会产生实际费用,不能在默认测试里跑。

当前策略:

- **所有默认测试都 mock `urllib.request.urlopen`**,验证 payload shape、auth header 分支、错误路径的 stderr 契约、api_key 脱敏。零 API 成本。
- **没有 live smoke test**。之前的 `tests/live_banana_smoke.py` 已删除,原因:当时它依赖已经删掉的 subprocess 派发路径,修复它的成本 ≈ 重写。如果之后需要 live 验证,新加一个 `tests/live_*.py` 并用环境变量 gate。

测试覆盖的核心不变式(regression 雷区):

1. `openai_client`:`image_size=None` 不能出现在 payload(不能序列化成 `"image_size": null`)
2. `openai_client`:HTTP 错误必须走 `OpenAIError`,错误消息必须脱敏 api_key
3. `openai_client`:必须**同时**扫描三种响应形状(`message.images[]` / markdown content / vision parts),不能 first-wins 短路
4. `gemini_client`:`googleapis.com` 走 `?key=`,其他走 `Bearer`,两条路径都要覆盖
5. `gemini_client`:permanent 4xx 不重试,仅 429/5xx 重试
6. `channels`:`channels.json` 必须 chmod 600
7. `channels`:stale default(指向不存在的渠道)必须在 load 时 silent drop,不 crash
8. `cli`:空 store resolve 的报错必须包含 "channel add" 引导
9. `history`:append 失败必须 stderr warn 不抛(用户的图已生成,日志是次要)
10. `history`:并发 append 必须 flock 保护,多线程 8×25 写入不能少行不能交错

上 10 条都有对应的回归测试。

## Why a history log

(0.3.0 起)q-imgen 把每次 `generate` / `batch` task 追加到 `~/.q-imgen/history/YYYY-MM-DD.jsonl`。这看起来给一个"原子原语"加了状态,但仔细看其实没破坏哲学:

**为什么允许这个状态**:
- **观察 ≠ 编排**。我们之前拒绝的 batch 并发、retry 队列、断点续传 全是**编排**(决定下一步做什么)。日志只是**观察**(记录已经做过什么)。两者在哲学上不冲突。
- **代价极低**:每次 append 几百字节 + 一次 flock,毫秒级,对生成本身的延迟和契约毫无影响。
- **本地、离线、纯文件**:不引入网络、不引入数据库、不引入新依赖(`fcntl` 是 stdlib)。

**为什么这样设计**:

| 决策 | 理由 |
|---|---|
| **JSONL,不是 SQLite** | append-only 天生原子;`tail` / `grep` / `jq` 即用;人能读;一个原子 CLI 引入数据库太重 |
| **按本地日期分文件**(`YYYY-MM-DD.jsonl`) | "今天的"和"昨天的"自然分离;一年 365 个文件,平铺好找;不会有"一个文件几 GB"的烦恼;早期方案是按 workdir 镜像目录树,被否决了——目录嵌套看着乱,平铺日期更整洁 |
| **`workdir` 字段而非目录分区** | "按项目划分"是真需求,但目录嵌套 vs 字段过滤,后者用 `jq 'select(.workdir == "...")'` 一行解决,目录树太重 |
| **`workdir = git root if available, else cwd`** | 项目通常 = git 仓库;同一仓库不同子目录的调用聚拢到同一 workdir 值,过滤更聚拢 |
| **fcntl.flock 而非 atomic-rename** | flock 简单且对 append-only 场景正确;atomic-rename 会丢失跨进程时间序 |
| **best-effort 写入(失败只 stderr warn 不抛)** | 调用 history.append 时图已经生成完了,日志是次要状态;让日志失败把生成失败纯属本末倒置 |
| **不写 pre-flight 错误**(渠道不存在 / batch 文件不存在等) | 没有合法的 `channel` / `model` / `protocol` 字段可以填,记一条无意义 |
| **不记录 api_key / base_url / 原始响应 body** | 渠道名足以反查;原始 body 是 debug 用的,不是审计用的;参考图 base64 体积会让日志膨胀 100×,只记路径足够 |
| **`q-imgen history` 只打印今天路径,不做查询** | shell + jq 已经是更好的查询工具;q-imgen 不重新发明轮子;CLI 表面只多 1 个命令 |
| **不做日志轮转 / TTL** | 典型用量每天 < 100KB,一年 < 30MB,jq 处理无压力;真要清理 `find ~/.q-imgen/history -mtime +90 -delete` |

**Spec 文档先写,再实现**:0.3.0 的 history 功能是 doc-first 完成的 —— 先写 [skills/q-imgen/references/history-queries.md](../skills/q-imgen/references/history-queries.md) 当查询手册 + 字段 schema,实现时代码必须匹配它的承诺。这种顺序强制把"用户最终会怎么用"想清楚再敲第一行代码。

## Why a Python library API (0.4.0+)

实际项目中,很多工作流最终变成批量生图脚本:角色 × 场景的笛卡尔积、golden anchor 风格对齐、带条件分支的生图流程。这些脚本每次都在重新封装 HTTP 调用、base64 编码、图片保存 —— 本质上是在重写 q-imgen 已经有的东西。

`api.generate()` 就是把这个重复劳动消除:脚本直接 `from q_imgen import generate`,拿回 `list[PIL.Image.Image]`,自己决定怎么处理。

### 两层入口,不是两个工具

```
api.py   → generate()       → list[PIL.Image]     (脚本用)
cli.py   → _run_single()    → JSON stdout + 文件   (agent/shell 用)
```

两层共享同一套协议 client(`gemini_client` / `openai_client`),只是最后一步不同:库返回内存对象,CLI 存盘 + 写 history + 输出 JSON。

### 为什么返回 PIL.Image 而不是路径或 bytes

- `PIL.Image` 可以直接 `.save()`、`.resize()`、`.paste()` —— 脚本里最常见的操作都是一行
- q-imgen 已经依赖 Pillow,不引入新依赖
- 返回路径会强制做 I/O(违背"纯函数"目标),返回 bytes 还要用户自己 `Image.open(BytesIO(...))`

### 为什么在 api.py 做图片预处理

`api._prepare_image()` 把超过 2048px 的 PIL.Image 自动缩小。这是因为:
- OpenAI client 对文件路径输入已经有 resize 逻辑,但 Gemini client 没有
- 在 api 层统一做,两个协议路径都受益
- 2048px 上限和 openai_client 一致,不会比 CLI 路径更激进
