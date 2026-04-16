"""q-imgen — atomic image generation primitive.

Public API::

    from q_imgen import generate

    images = generate("a cute cat", channel="my-proxy")
    images[0].save("cat.png")
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("q-imgen")
except PackageNotFoundError:
    # Running from a source checkout without an installed distribution.
    __version__ = "0.0.0+source"

from .api import generate
from .channels import Channel, ChannelError
from .gemini_client import GeminiError
from .openai_client import OpenAIError

__all__ = [
    "__version__",
    "generate",
    "Channel",
    "ChannelError",
    "GeminiError",
    "OpenAIError",
]
