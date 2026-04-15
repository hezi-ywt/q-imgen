"""q-imgen unified wrapper over atomic image engines."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("q-imgen")
except PackageNotFoundError:
    # Running from a source checkout without an installed distribution.
    __version__ = "0.0.0+source"
