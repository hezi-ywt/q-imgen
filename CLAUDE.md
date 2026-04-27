# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Commands

```bash
# Install (editable)
cd q-imgen && python -m pip install -e .

# No-install fallback
PYTHONPATH=src python -m q_imgen --help

# Run all tests (offline, mocked HTTP, ~2s)
python -m unittest discover -s tests -p "test_*.py" -v

# Run a single test class
python -m unittest tests.test_clients.OpenAIImageEncodingTests -v

# Run a single test method
python -m unittest tests.test_cli.CliGenerateTests.test_generate_dispatches_to_openai_client_for_openai_channel -v
```

Version lives only in `pyproject.toml` (read at runtime via `importlib.metadata`). Do not add a version string to `__init__.py`.

## Architecture

Atomic image-generation CLI with channel-based endpoint routing. Protocols (`gemini`, `openai`, `openai_images`) are **intentionally kept as independent clients** — their payload shapes are genuinely incompatible. Do not unify them.

```
cli.py  ──►  _run_single()  ──┬──►  gemini_client.generate()        POST /models/{m}:generateContent
     │                         ├──►  openai_client.generate()        POST /chat/completions
     │                         └──►  openai_images_client.generate() POST /images/generations
     │
     ├──►  channels.py   ChannelStore (~/.q-imgen/channels.json, chmod 600)
     └──►  history.py    best-effort JSONL append (~/.q-imgen/history/YYYY-MM-DD.jsonl)

api.py  ──►  generate()  ──►  resolve channel  ──►  *_client.generate_images()  ──►  list[PIL.Image]
```

Two entry points: **CLI** (`cli.py`) saves images to disk and outputs JSON; **Python API** (`api.py`) returns `PIL.Image` objects without I/O. Both share the same protocol clients underneath.

**Protocol dispatch** is a simple `if/elif` in both `cli._run_single()` and `api.generate()` — no plugin system, no registry. Adding a new protocol means: new `<name>_client.py` + add to `VALID_PROTOCOLS` in `channels.py` + new `elif` in both dispatchers.

### Key module responsibilities

- **`api.py`**: Public Python API. `generate()` resolves channel, preprocesses images (resize >2048px), dispatches to client `generate_images()`, returns `list[PIL.Image]`. No disk I/O, no history, no printing.
- **`cli.py`**: argparse entry, subcommand handlers (`generate`, `batch`, `channel add/list/show/use/rm`, `history`), output JSON assembly, the `_run_single` dispatcher.
- **`channels.py`**: `Channel` dataclass + `ChannelStore` CRUD. First channel added becomes default. `resolve(name)` returns named or default channel.
- **`gemini_client.py`**: Gemini-native POST. Auth branches on `googleapis.com` in URL (`?key=` vs `Bearer`). Parses both camelCase and snake_case field names from response. `generate_images()` returns `list[PIL.Image]`.
- **`openai_client.py`**: OpenAI-compat chat POST. Scans **3 response shapes in parallel** (explicit `images[]`, markdown `![](url)` in content, vision-style parts array) then dedupes by URL. `generate_images()` returns `list[PIL.Image]`. Has retry logic for 429/5xx.
- **`openai_images_client.py`**: OpenAI Images POST. Sends optional `input_images`, `quality`, `background`, `output_format`, and `n`. Supports b64 and URL responses. `generate_images()` returns `list[PIL.Image]`. Has retry logic for 429/5xx.
- **`history.py`**: Append-only audit log. Uses `fcntl.flock` for concurrent safety. Failures go to stderr only, never raise — the image is already generated.

## I/O Contract (agent-critical)

- **stdout** = one JSON object (data/results)
- **stderr** = `[q-imgen] ...` diagnostics
- **exit 0** on success (batch partial failure = `status: "partial"` + exit 0)
- **exit 1** on error
- All error paths scrub `api_key` and `Bearer <token>` before surfacing

Preserve this contract on every code path. Agents parse stdout as JSON.

## Python Library API

```python
from q_imgen import generate, Channel, ChannelError, GeminiError, OpenAIError, OpenAIImagesError

images = generate("prompt", images=["ref.png", pil_obj], channel="name")
# Returns list[PIL.Image.Image], raises on failure
```

- `images` accepts `str | Path | PIL.Image.Image` mixed — oversized PIL images auto-resized to 2048px
- Uses the same channel config as CLI (`~/.q-imgen/channels.json`)
- `generate()` is a pure function: no file saving, no history, no stderr output
- Each protocol client also exposes `generate_images()` for direct use without channel resolution

## Regression Invariants

These are tested and must not regress:

1. OpenAI: `image_size=None` is **omitted** from payload, not serialized as null
2. OpenAI: HTTP errors sanitize api_key before raising `OpenAIError`
3. OpenAI: 3-shape parallel scan with dedup, not first-wins
4. OpenAI Images: calls `/images/generations`, maps `--image` to `input_images`, normalizes shortcuts like `2K` to explicit pixel sizes, handles b64/URL responses, and forwards Images-specific params only when set
5. Gemini: `googleapis.com` → `?key=` auth, others → `Bearer` auth
6. Gemini: 4xx (except 429) = no retry; 429/5xx = retry
7. Channels: `channels.json` always chmod 600
8. Channels: stale default silently dropped on load
9. CLI: empty channel store error includes `channel add` hint text
10. History: append failures → stderr warning, never raise
11. History: concurrent writes use `fcntl.flock`, no interleaving

## Testing

All tests are offline with mocked HTTP (`unittest.mock.patch` on `urllib.request.urlopen`). Zero API cost. Tests use `tempfile.TemporaryDirectory` for isolation — they never touch real `~/.q-imgen/`.

No live smoke tests exist. If adding one, put it in `tests/live_*.py` and gate on an env var.

## Paired Skill

The `skills/q-imgen/` directory contains the agent-facing skill contract (`SKILL.md` + references). When changing CLI surface or output format, mirror the edit in `skills/q-imgen/SKILL.md`.
