# q-imgen

Atomic nanobanana CLI with channel-based endpoint routing.

[中文说明](README.zh-CN.md)

One primitive, many channels. Each channel is a complete route to an image generation endpoint — protocol + base_url + api_key + model. Switch between Google-native Gemini and OpenAI-compatible gateways without changing code or env vars.

```
q-imgen generate "prompt" [--image ref.png ...] [--channel name] [-o ./out]
q-imgen batch tasks.json [--channel name]
q-imgen channel add <name> --protocol {gemini|openai} --base-url URL --api-key KEY --model M
q-imgen channel list | show [name] | use <name> | rm <name>
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

## Two protocols, one CLI

q-imgen ships two clients under the hood:

| Protocol | Endpoint | Use it for |
|---|---|---|
| `gemini` | `POST {base_url}/models/{model}:generateContent` | Google's native Gemini API (auto-detects `googleapis.com` and uses `?key=` auth) or any proxy that speaks the Gemini payload format (uses Bearer auth) |
| `openai` | `POST {base_url}/chat/completions` | OpenAI-compatible gateways (one-api / new-api / litellm / yunwu / etc.) that expose image generation under the chat schema |

Pick the right one when adding a channel; q-imgen dispatches automatically.

The OpenAI client tolerates the **three different response shapes** real gateways use for inline images (`message.images[]`, markdown in `message.content`, OpenAI vision-style content parts) and dedupes by URL — so most public OpenAI-compat image gateways work without per-gateway tweaking. See [docs/design-rationale.md](docs/design-rationale.md#why-the-openai-client-accepts-multiple-response-shapes) for the full list and how to add a fourth shape if you find one.

## Quickstart

```bash
# Add your first channel (becomes the default automatically)
q-imgen channel add proxy-a \
  --protocol openai \
  --base-url https://sd.rnglg2.top:30000/v1 \
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

# Switch default
q-imgen channel use google-native

# Or pick a channel per call without switching default
q-imgen generate "..." --channel proxy-a

# Image edit / multi-image fusion
q-imgen generate "change kimono to blue" --image input.png
q-imgen generate "merge A's hair with B's style" --image a.png --image b.png

# Batch
q-imgen batch tasks.json -o ./output --delay 1.0
```

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
      "base_url": "https://sd.rnglg2.top:30000/v1",
      "api_key": "sk-...",
      "model": "gemini-3.1-flash-image-preview"
    },
    "google-native": {
      "protocol": "gemini",
      "base_url": "https://generativelanguage.googleapis.com/v1beta",
      "api_key": "AIza...",
      "model": "gemini-3.1-flash-image-preview"
    }
  }
}
```

## Batch task format

A JSON array of task objects, each inheriting channel/model from the CLI call:

```json
[
  { "prompt": "silver elf archer, magic forest", "aspect_ratio": "2:3", "image_size": "2K" },
  { "prompt": "cat-ear boy stargazing", "aspect_ratio": "16:9" },
  { "prompt": "change kimono to blue", "images": ["input.png"], "aspect_ratio": "3:4" }
]
```

Per-task fields override CLI defaults for that task only. See `skills/q-imgen/references/nanobanana-batch-format.md`.
`prompt` is required for every task; a task missing `prompt` fails locally and is reported in the batch `results` array without making an API call.

## Testing

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

All tests are offline — HTTP calls are mocked. There is no live smoke test in this version; if you need one, add a `tests/live_*.py` and gate it on an env var.

## Design philosophy

- **Atomic primitive**, not a framework. `generate` and `batch` do one thing each; higher-level orchestration (prompt engineering, character consistency, workflow composition) belongs to the caller.
- **No heuristic routing.** The caller picks a channel explicitly. "Use A for quality, B for speed" logic lives in the calling skill or script, never in q-imgen.
- **Agent-safe I/O where it matters**: `generate` / `batch` stdout = data, stderr = diagnostics, exit code = 0/1. Channel-management commands stay human-readable except `channel show`, which returns JSON.
- **API key safety**: all error messages scrub live keys and `Bearer` tokens before surfacing them.

## Project layout

```
q-imgen/
├── src/q_imgen/
│   ├── cli.py              # argparse entry, subcommand handlers
│   ├── channels.py         # channels.json CRUD
│   ├── gemini_client.py    # Gemini-native protocol
│   └── openai_client.py    # OpenAI-compat protocol
├── tests/
└── skills/q-imgen/         # agent-facing skill
```

## License

MIT
