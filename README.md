# q-imgen

`q-imgen` is a small infrastructure primitive for image generation workflows. It gives users and agents one stable CLI entrypoint while keeping the underlying engines atomic.

Current scope:

- one public command: `q-imgen`
- atomic engines stay separate: `midjourney` and `nanobanana`
- provider support for Banana: `gemini` and `openai`
- stdout for results, stderr for progress/errors

This project is designed to be installed directly from the GitHub repository. It is **not** currently intended for PyPI or GitHub Releases.

## Quickstart

```bash
git clone <your-repo-url>
cd q-imgen
python -m pip install -e .
q-imgen --help
```

For a quick local verification after cloning:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

## What it is

- a unified wrapper CLI
- a GitHub-installable subproject for image generation infrastructure
- an optional agent skill layer, not the project itself

## What it is not

- not a full image workflow platform
- not a job scheduler
- not a stateful backend service

## Project layout

```text
q-imgen/
├── README.md
├── pyproject.toml
├── docs/
├── src/
│   └── q_imgen/
└── skills/
    └── q-imgen/
```

## Install

```bash
python -m pip install -e .
q-imgen --help
q-imgen init
q-imgen config
q-imgen mj --help
q-imgen banana --help
```

If you only want a no-install local fallback:

```bash
PYTHONPATH=src python -m q_imgen --help
```

The wrapper keeps one strong boundary: it chooses the engine/provider path and forwards the call, but it does not try to become a full workflow system.

## GitHub install model

This project is intended to be consumed by:

- users cloning the repository and running `python -m pip install -e .`
- agents operating inside the repository and installing locally

No GitHub Release artifact is required for normal usage.

## Config

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

Persistent config is stored at `~/.q-imgen/.env`.

Supported Banana provider values:

- `gemini`
- `openai`

The old value `openai_compat` is still accepted as a backward-compatible alias, but `openai` is the only recommended public spelling.

## Examples

```bash
q-imgen mj imagine "anime girl in shrine" --no-upscale --output-dir ./output
q-imgen banana generate "anime girl in shrine" --output-dir ./output
q-imgen banana batch tasks.json --output-dir ./output --delay 1.0
```

## Verification

Default low-cost test suite:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

Manual Banana smoke test:

```bash
python -m unittest tests.live_banana_smoke -v
```

Package build verification:

```bash
python -m build
```

## Testing

`q-imgen` uses a two-layer test strategy.

### 1. Default test suite

This suite is low-cost and should run during normal development. It covers:

- config
- alias / dispatch
- routing
- provider selection behavior

Run it with:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

### 2. Banana live smoke

This suite is manual and intentionally higher-cost. It verifies:

- the API still responds
- the wrapper-to-engine path still works
- output files are saved correctly

Run it with:

```bash
python -m unittest tests.live_banana_smoke -v
```

If no Banana API key is configured, it skips safely.

## Project layout

```text
q-imgen/
├── README.md
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml
├── docs/
├── src/
│   └── q_imgen/
├── tests/
└── skills/
    └── q-imgen/
```

## Development notes

- `src/q_imgen/` contains runtime code only
- `docs/` explains boundaries, usage, and rationale
- `skills/q-imgen/` is optional agent-facing material
- the project root is not itself a skill
