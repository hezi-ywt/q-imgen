# Usage

`q-imgen` 提供统一入口，但不重写底层引擎的参数语义。

## Install

推荐在项目目录里先执行：

```bash
python -m pip install -e .
```

之后直接使用：

```bash
q-imgen --help
```

## Basic shape

```bash
q-imgen midjourney <args...>
q-imgen nanobanana <args...>
q-imgen mj <args...>
q-imgen banana <args...>
```

## Config commands

```bash
q-imgen init
q-imgen config
q-imgen config --mj-api-key sk-xxx
q-imgen config --banana-model gemini-3.1-flash-image-preview
q-imgen config --banana-provider gemini
q-imgen config --banana-provider openai
```

持久配置文件位置：`~/.q-imgen/.env`

优先级：`process env > ~/.q-imgen/.env > engine defaults`

### Provider examples

```bash
# Gemini native
q-imgen config --banana-provider gemini \
  --banana-api-key <KEY> \
  --banana-model gemini-3.1-flash-image-preview

# OpenAI-style image backend
q-imgen config --banana-provider openai \
  --banana-openai-base-url https://sd.rnglg2.top:30000/v1 \
  --banana-openai-api-key <KEY> \
  --banana-openai-model gemini-3.1-flash-image-preview
```

## Examples

```bash
q-imgen mj imagine "anime girl in shrine" --no-upscale --output-dir ./output
q-imgen banana generate "anime girl in shrine" --output-dir ./output
q-imgen banana batch tasks.json --output-dir ./output --delay 1.0
```

## Contract

- wrapper 负责 engine 选择
- wrapper 负责把 `~/.q-imgen/.env` 中的持久配置注入子进程环境
- engine 自己负责参数解析、网络调用和结果输出
- wrapper 不做 prompt 改写、不做统一参数抽象、不做结果二次封装

## Testing workflow

### Default test suite

默认开发时运行：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

它覆盖的是：

- config
- CLI / dispatch
- routing

这组测试应该保持**零或极低 API 成本**。

### Manual Banana smoke test

手动验证真实生图链路时运行：

```bash
python -m unittest tests.live_banana_smoke -v
```

说明：

- 只测 Nano Banana
- 没有可用 API key 时自动 skip
- 不应该进默认测试或 CI
