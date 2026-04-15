# Maintainer Notes

`q-imgen` is intended to be shipped via GitHub repository source, not via GitHub Releases or PyPI.

## Release workflow

When preparing a new version:

1. Update `src/q_imgen/__init__.py` version if needed
2. Update `CHANGELOG.md`
3. Run tests
4. Run package build verification
5. Push to GitHub

## Verification commands

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m unittest tests.live_banana_smoke -v
python -m build
python -m pip install -e .
q-imgen --help
```

## Install model

Users and agents are expected to:

1. clone the repository
2. enter `q-imgen/`
3. run `python -m pip install -e .`

No external release channel is required.
