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

**重要：更新前务必保护 `user-notes.md`**。这是 agent 自维护的记忆层（用户偏好、经验、工作流），每个用户本地都不一样，不应该被远端的版本覆盖。

安全的更新流程：

```bash
# 1. 先检查 user-notes.md 是否有本地修改
git status skills/q-imgen/references/user-notes.md

# 2. 如果有本地修改，先 stash 或显式保留
git stash push skills/q-imgen/references/user-notes.md -m "protect user notes"

# 3. 拉取远端更新
git pull origin main

# 4. 恢复本地的 user-notes
git stash pop    # 如果冲突，手动合并（通常保留本地内容）
```

如果 `user-notes.md` 远端也有新增的通用条目（不是某个具体用户的），需要手动 merge — 通常把远端的新条目追加到本地版本里，不要反过来。

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
