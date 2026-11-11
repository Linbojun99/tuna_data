"""One library, on a local disk, mounted NAS, or private Drive sync directory."""
from .library import Library
from .sources import discover

__all__ = ["Library", "discover"]
__version__ = "0.1.0"
