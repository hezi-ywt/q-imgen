# Maintainer Notes

`q-imgen` 通过 GitHub repository source 分发,不发布到 PyPI,不使用 GitHub Releases。

## Release workflow

新版本发布流程:

1. 更新 `pyproject.toml` 中的 `version` 字段(版本号从这里读,`src/q_imgen/__init__.py` 通过 `importlib.metadata.version("q-imgen")` 动态获取)
2. 更新 `CHANGELOG.md`
3. 跑测试:`python -m unittest discover -s tests -p "test_*.py" -v`
4. 重装确认入口:`python -m pip install -e . && q-imgen --version`
5. Push 到 GitHub

## Verification commands

```bash
# 默认测试套件(零 API 成本,所有 HTTP 被 mock)
python -m unittest discover -s tests -p "test_*.py" -v

# 构建验证
python -m build

# 入口验证
python -m pip install -e .
q-imgen --help
q-imgen --version
```

没有 `live_banana_smoke` —— 老版本有一套真实调用的 smoke test,在 0.2.0 重构中删除。如果需要 live 验证,新加 `tests/live_*.py` 并用环境变量 gate,不要让它进默认 discover 命中(文件名用 `live_*.py` 不是 `test_*.py` 即可)。

## Install model

Users and agents 的使用路径:

1. `git clone <repo>`
2. `cd q-imgen`
3. `python -m pip install -e .`
4. `q-imgen channel add ...`(CLI 会用 stderr 引导)
5. `q-imgen generate "..."`

不依赖任何外部包、不需要激活虚拟环境之外的任何东西。

## Adding a new protocol

如果未来要加第三种协议(比如 Anthropic Vision / Replicate / 其他自定义 API):

1. 新建 `src/q_imgen/<name>_client.py`,提供一个 `generate(...)` 函数和一个 `<Name>Error` 异常
2. 在 `channels.py` 的 `VALID_PROTOCOLS` 里加新 protocol 名
3. 在 `cli.py` 的 `_run_single` 里加一个 `elif channel.protocol == "<name>":` 分支
4. 加对应的测试(参考 `test_clients.py` 里两个既有 client 的测试形状)

不需要碰 CLI 的参数解析 —— `--channel` 已经可以承载任意 protocol,用户加渠道时 `--protocol <name>` 即可。

## 不要引入的东西

- **Engine 抽象层**:两个协议的 payload 形状真的不兼容,尝试统一只会造成沉默失真。保持两个独立的 client 文件。
- **启发式路由**:调用方比 q-imgen 更了解自己要什么,让它显式传 `--channel`。
- **Env var 覆盖链**:一个 `channels.json` 是全部真相。不要加"进程 env > 文件 > 默认值"这种三层优先级。
- **Batch 并发/重试/断点续传**:batch 只做最简单的顺序循环。复杂编排是调用方的事。
