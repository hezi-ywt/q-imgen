# Python 库 API

## 安装

q-imgen 作为 Python 包安装后，CLI 和库 API 同时可用：

```bash
cd q-imgen
pip install -e .
```

## API

```python
from q_imgen import generate
```

### generate()

```python
def generate(
    prompt: str,
    *,
    images: list[str | Path | PIL.Image.Image] | None = None,
    channel: str | None = None,
    aspect_ratio: str = "3:4",
    image_size: str | None = None,
    timeout: float = 300,
    max_retries: int = 3,
) -> list[PIL.Image.Image]:
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt` | `str` | 必填 | 文本提示词 |
| `images` | `list` | `None` | 参考图，接受文件路径（str/Path）和 PIL.Image 对象混合，最多 14 张 |
| `channel` | `str` | `None` | 渠道名称，`None` 使用默认渠道 |
| `aspect_ratio` | `str` | `"3:4"` | 宽高比 |
| `image_size` | `str` | `None` | 尺寸提示：`"512"` / `"1K"` / `"2K"` / `"4K"`，`None` 不传 |
| `timeout` | `float` | `300` | API 超时秒数 |
| `max_retries` | `int` | `3` | 429/5xx 重试次数 |

**返回：** `list[PIL.Image.Image]` — 内存中的 PIL 图片对象

**异常：**

| 异常 | 场景 |
|------|------|
| `ChannelError` | 渠道不存在或未配置 |
| `GeminiError` | Gemini 协议调用失败 |
| `OpenAIError` | OpenAI 协议调用失败 |

异常类都可以从顶层导入：

```python
from q_imgen import generate, ChannelError, GeminiError, OpenAIError
```

## 与 CLI 的区别

| | CLI (`q-imgen generate`) | Python 库 (`generate()`) |
|---|---|---|
| **输出** | JSON 到 stdout + 图片保存到磁盘 | 返回 `list[PIL.Image.Image]`，不保存 |
| **错误** | stderr + exit 1 | 抛异常 |
| **history** | 自动记录 | 不记录 |
| **适用场景** | agent 调用、shell 脚本、单次使用 | Python 脚本工作流、需要预处理/后处理 |

## 什么时候用 CLI，什么时候用脚本

**用 CLI：**

- agent 调用 — agent 解析 stdout JSON 就够了
- 单次生成 — 一句命令搞定
- batch 任务 — 任务之间无依赖，用 `q-imgen batch tasks.json`
- shell 管道 — 和 jq、xargs 等组合

**用 Python 库：**

- 任务之间有依赖 — 比如第一张的输出要作为后续的参考图（golden anchor）
- 需要图片预处理 — 先 resize、裁剪、拼接，再传入生成
- 需要图片后处理 — 拿到结果后加水印、转格式、上传
- 自定义循环逻辑 — 角色 × 场景的笛卡尔积、条件分支、动态 prompt 拼接
- 和其他 Python 代码集成 — 在更大的脚本或服务中调用

简单判断：**如果你在写 Python 脚本并且需要对图片做任何处理，用库；否则用 CLI。**

## 示例

### 基本用法

```python
from q_imgen import generate

images = generate("一只橙色的猫在阳光下打盹")
images[0].save("cat.png")
```

### 带参考图

```python
images = generate(
    "把背景改成星空",
    images=["original.png"],
    aspect_ratio="16:9",
)
```

### PIL.Image 预处理后传入

```python
from PIL import Image
from q_imgen import generate

ref = Image.open("character.png")
ref = ref.resize((512, 512))

images = generate(
    "这个角色穿上宇航服",
    images=[ref, "spacesuit_ref.png"],
)
```

### Golden anchor 工作流

```python
from q_imgen import generate

characters = [
    {"name": "fox", "visual": "assets/fox_main.png", "identity": "a cute orange fox"},
    {"name": "rabbit", "visual": "assets/rabbit_main.png", "identity": "a white rabbit with long ears"},
]
style_ref = "assets/style_sheet.png"
anchor = None

for char in characters:
    refs = [style_ref, char["visual"]]
    if anchor:
        refs.append(anchor)

    images = generate(
        f"Create a chibi mascot illustration of {char['identity']}...",
        images=refs,
        aspect_ratio="1:1",
        image_size="1K",
    )

    images[0].save(f"output/{char['name']}.png")

    if anchor is None:
        anchor = images[0]  # 第一张结果作为后续的风格锚点
```

### 错误处理

```python
from q_imgen import generate, ChannelError, OpenAIError, GeminiError

try:
    images = generate("prompt", channel="my-proxy")
except ChannelError as e:
    print(f"渠道问题: {e}")
except (OpenAIError, GeminiError) as e:
    print(f"生成失败: {e}")
```
