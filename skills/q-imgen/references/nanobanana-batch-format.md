# Nano Banana Batch 任务格式

默认通过 `q-imgen nanobanana batch` 调用；底层仍复用 `nanobanana batch`。接受的 JSON 文件格式：

```json
[
  {
    "prompt": "银发精灵弓箭手，魔法森林",
    "aspect_ratio": "2:3",
    "image_size": "2K"
  },
  {
    "prompt": "猫耳男孩看星空",
    "aspect_ratio": "16:9"
  },
  {
    "prompt": "把和服改成蓝色",
    "images": ["input.png"],
    "aspect_ratio": "3:4"
  }
]
```
