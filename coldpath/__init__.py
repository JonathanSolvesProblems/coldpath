"""coldpath -- prove whether an Arm binary can actually use the chip's matrix hardware."""

__version__ = "0.1.1"

from .isa import Result, scan_sections
from .binfmt import sections, NotAArch64

__all__ = ["Result", "scan_sections", "sections", "NotAArch64", "__version__"]
