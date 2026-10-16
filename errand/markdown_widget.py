"""Compatibility alias; implementation lives in chat_foundation."""
import sys
from chat_foundation.gtk import markdown_widget as _implementation

sys.modules[__name__] = _implementation
