# 更新检查

q-imgen 通过本地 git 仓库安装，不走 PyPI。仓库里包含三样东西，更新方式不同：

| 组件 | 内容 | 更新方式 |
|------|------|----------|
| **Skill** | `skills/q-imgen/` 下的 SKILL.md 和 references | `git pull` 即生效 |
| **Python 包** | `src/q_imgen/` 下的库代码（`from q_imgen import generate`） | `git pull` 即生效（editable install 直接指向源码） |
| **CLI 元数据** | 版本号、依赖、entry point（`pyproject.toml`） | `git pull` 后需要 `pip install -e .` |

## 检查是否有更新

```bash
cd <q-imgen 所在目录>
git fetch origin main
git log HEAD..origin/main --oneline
```

无输出 = 已是最新。有输出 = 列出的就是待更新的 commit。

## 执行更新

```bash
git pull origin main
```

大多数情况到这就够了 — skill 文件和库代码立即生效。

如果 `git diff HEAD~..HEAD -- pyproject.toml` 有改动（版本号、依赖、entry point 变了），还需要：

```bash
pip install -e .
```

## 验证

```bash
q-imgen --version
```

## 注意

- 如果用户本地有未提交的修改，`git pull` 前先确认是否需要 stash
