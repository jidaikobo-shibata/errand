"""Compatibility alias; implementation lives in chat_foundation."""
import sys
from chat_foundation.core import math as _implementation

sys.modules[__name__] = _implementation
