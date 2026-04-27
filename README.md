# q-imgen

Atomic nanobanana CLI with channel-based endpoint routing.

[中文说明](README.zh-CN.md)

One primitive, many channels. Each channel is a complete route to an image generation endpoint — protocol + base_url + api_key + model. Switch between Google-native Gemini and OpenAI-compatible gateways without changing code or env vars.

```
q-imgen generate "prompt" [--image ref.png ...] [--channel name] [-o ./out]
q-imgen batch tasks.json [--channel name]
q-imgen channel add <name> --protocol {gemini|openai|openai_images} --base-url URL --api-key KEY --model M
q-imgen channel list | show [name] | use <name> | rm <name>
q-imgen status
```

## Install

```bash
git clone <your-repo-url>
cd q-imgen
python -m pip install -e .
q-imgen --help
```

No-install fallback:

```bash
PYTHONPATH=src python -m q_imgen --help
```

## Protocols

q-imgen ships independent protocol clients under the hood:

| Protocol | Endpoint | Use it for |
|---|---|---|
| `gemini` | `POST {base_url}/models/{model}:generateContent` | Google's native Gemini API (auto-detects `googleapis.com` and uses `?key=` auth) or any proxy that speaks the Gemini payload format (uses Bearer auth) |
| `openai` | `POST {base_url}/chat/completions` | OpenAI-compatible gateways (one-api / new-api / litellm / various proxies / etc.) that expose image generation under the chat schema |
| `openai_images` | `POST {base_url}/images/generations` | OpenAI Images-compatible gateways such as Yunwu `gpt-image-2`, with `input_images`, `quality`, `background`, `output_format`, `n`, and b64/URL response support |

Pick the right one when adding a channel; q-imgen dispatches automatically.

The OpenAI client tolerates the **three different response shapes** real gateways use for inline images (`message.images[]`, markdown in `message.content`, OpenAI vision-style content parts) and dedupes by URL — so most public OpenAI-compat image gateways work without per-gateway tweaking. See [docs/design-rationale.md](docs/design-rationale.md#why-the-openai-client-accepts-multiple-response-shapes) for the full list and how to add a fourth shape if you find one.

## Quickstart

```bash
# Add your first channel (becomes the default automatically)
q-imgen channel add proxy-a \
  --protocol openai \
  --base-url https://your-proxy.example.com/v1 \
  --api-key sk-xxx \
  --model gemini-3.1-flash-image-preview

# Generate
q-imgen generate "anime girl in shrine" -o ./output

# Add a second channel (Google native)
q-imgen channel add google-native \
  --protocol gemini \
  --base-url https://generativelanguage.googleapis.com/v1beta \
  --api-key AIzaSy... \
  --model gemini-3.1-flash-image-preview

# Add an OpenAI Images channel (Yunwu gpt-image-2)
q-imgen channel add yunwu-gpt-image \
  --protocol openai_images \
  --base-url https://yunwu.ai/v1 \
  --api-key sk-xxx \
  --model gpt-image-2

# Switch default
q-imgen channel use google-native

# Or pick a channel per call without switching default
q-imgen generate "..." --channel proxy-a

# Image edit / multi-image fusion
q-imgen generate "change kimono to blue" --image input.png
q-imgen generate "merge A's hair with B's style" --image a.png --image b.png

# OpenAI Images-specific controls
q-imgen generate "poster concept" --channel yunwu-gpt-image \
  --image-size 1024x1536 --quality high --background transparent --output-format webp --num-images 2

# 2K shortcut expands to explicit pixels for openai_images (1:1 -> 2048x2048)
q-imgen generate "poster concept" --channel yunwu-gpt-image --aspect-ratio 1:1 --image-size 2K

# Batch
q-imgen batch tasks.json -o ./output --delay 1.0
```

OpenAI Images size reference for agents:

| Size | Use |
|---|---|
| `1024x1024` | square |
| `1536x1024` | landscape |
| `1024x1536` | portrait |
| `2048x2048` | 2K square |
| `2048x1152` | 2K landscape |
| `3840x2160` | 4K landscape |
| `2160x3840` | 4K portrait |
| `auto` | provider default |

Strict OpenAI Images size rules: max edge <= 3840px; both dimensions must be multiples of 16px; long edge / short edge <= 3:1; total pixels must be between 655360 and 8294400.

## Output contract

- **`generate` / `batch` stdout**: one JSON object per call (parseable by agents)
- **`channel add/list/use/rm` stdout**: human-readable status lines
- **`channel show` stdout**: one JSON object
- **stderr**: `[q-imgen] ...` diagnostic lines
- **exit code**: `0` success, `1` any failure; batch partial failure is `status: "partial"` with exit 0 so callers can inspect individual results

Example success output:

```json
{
  "status": "ok",
  "channel": "proxy-a",
  "model": "gemini-3.1-flash-image-preview",
  "prompt": "anime girl in shrine",
  "images": ["./output/img_000.png"],
  "ref_images": []
}
```

Example error (exit 1):

```json
{
  "status": "error",
  "channel": "proxy-a",
  "model": "gemini-3.1-flash-image-preview",
  "prompt": "anime girl in shrine",
  "error": "API request failed with HTTP 401: ..."
}
```

## Config storage

`~/.q-imgen/channels.json` (chmod 600). Human-editable if needed:

```json
{
  "default": "proxy-a",
  "channels": {
    "proxy-a": {
      "protocol": "openai",
      "base_url": "https://your-proxy.example.com/v1",
      "api_key": "sk-...",
      "model": "gemini-3.1-flash-image-preview"
    },
    "google-native": {
      "protocol": "gemini",
      "base_url": "https://generativelanguage.googleapis.com/v1beta",
      "api_key": "AIza...",
      "model": "gemini-3.1-flash-image-preview"
    },
    "yunwu-gpt-image": {
      "protocol": "openai_images",
      "base_url": "https://yunwu.ai/v1",
      "api_key": "sk-...",
      "model": "gpt-image-2"
    }
  }
}
```

## Local shared-key limiter

`q-imgen` now applies a lightweight **local** concurrency cap per API key on the current machine.

- Scope: only local `q-imgen` processes on the same machine
- Keying: by API key hash, so different channels using the same real key share one cap
- Default cap: `10` concurrent local requests per shared key
- Storage: `~/.q-imgen/state.db`
- Visibility: `q-imgen status`

This is intentionally not a remote task system. It does not inspect provider-side queue state or outputs from other machines.

## Batch task format

A JSON array of task objects, each inheriting channel/model from the CLI call:

```json
[
  { "prompt": "silver elf archer, magic forest", "aspect_ratio": "2:3", "image_size": "2K" },
  { "prompt": "cat-ear boy stargazing", "aspect_ratio": "16:9" },
  { "prompt": "change kimono to blue", "images": ["input.png"], "aspect_ratio": "3:4" },
  { "prompt": "gpt-image-2 poster", "image_size": "1024x1536", "quality": "high", "output_format": "webp", "num_images": 2 }
]
```

Per-task fields override CLI defaults for that task only. See `skills/q-imgen/references/nanobanana-batch-format.md`.
`prompt` is required for every task; a task missing `prompt` fails locally and is reported in the batch `results` array without making an API call.

## Testing

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

All tests are offline — HTTP calls are mocked. There is no live smoke test in this version; if you need one, add a `tests/live_*.py` and gate it on an env var.

## Python library API

q-imgen can also be used as a Python library in scripts that need custom loops, image pre/post-processing, or inter-task dependencies:

```python
from q_imgen import generate

images = generate("a cute fox", images=["style_ref.png"], channel="my-proxy",
                  aspect_ratio="1:1", image_size="1K")
images[0].save("output.png")
```

- Returns `list[PIL.Image.Image]` — no files saved, no history, no printing
- `images` accepts file paths (`str`/`Path`) and `PIL.Image` objects mixed
- Reuses CLI channel config (`~/.q-imgen/channels.json`)
- Raises `ChannelError` / `GeminiError` / `OpenAIError` / `OpenAIImagesError` on failure

**When to use CLI vs library:** CLI for agent calls, one-shot generation, batch jobs, shell pipelines. Library for Python scripts that need to process images before/after generation, chain outputs between tasks, or control loop logic.

## Design philosophy

q-imgen chooses to be a reliable building block, leaving the freedom of assembly to whoever uses it.

- **Atomic primitive, not a framework.** One job: send a prompt with reference images, get back pictures. Batching, loops, prompt optimization, style strategy, and workflow orchestration all belong to the caller — agent or script — not to q-imgen.
- **Two entry points, one core.** CLI faces agents and shell (stdout JSON, exit 0/1). Python library faces scripts (returns `PIL.Image`, raises on failure). The caller decides which fits; both share the same protocol clients underneath.
- **Channels are the only routing abstraction.** No heuristic selection, no env-var priority chains. One `channels.json` is the single source of truth; the caller passes `--channel` explicitly.
- **Protocols are intentionally not unified.** Gemini, OpenAI chat, and OpenAI Images payload shapes are genuinely incompatible. Forcing an abstraction layer would cause silent data loss. The clients evolve independently; their only shared contract is "return results or raise an exception."
- **Observation is allowed, orchestration is not.** The history log is the only "state" q-imgen keeps — it records what happened, never decides what to do next. And it's best-effort: a logging failure never blocks image generation.
- **Agent-safe I/O where it matters.** `generate` / `batch` stdout = data, stderr = diagnostics, exit code = 0/1. Channel-management commands stay human-readable except `channel show`, which returns JSON.
- **API key safety.** All error messages scrub live keys and `Bearer` tokens before surfacing them.
- **Git-native updates.** No PyPI publishing. Skill files, library code, and CLI metadata all live in the same repo. `git pull` updates skill and code immediately; `pip install -e .` is only needed when `pyproject.toml` changes.

## Project layout

```
q-imgen/
├── src/q_imgen/
│   ├── api.py              # public Python API: generate() → list[PIL.Image]
│   ├── cli.py              # argparse entry, subcommand handlers
│   ├── channels.py         # channels.json CRUD
│   ├── gemini_client.py    # Gemini-native protocol
│   ├── openai_client.py    # OpenAI-compat chat protocol
│   ├── openai_images_client.py # OpenAI Images protocol
│   ├── history.py          # audit log (JSONL)
│   └── limiter.py          # local shared-key concurrency limiter
├── tests/
└── skills/q-imgen/         # agent-facing skill
```

## License

MIT
