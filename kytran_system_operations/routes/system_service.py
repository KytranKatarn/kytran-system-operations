"""Shim: re-export from package root so copied platform routes resolve."""
from ..system_service import *  # noqa: F401,F403
from ..system_service import get_system_service  # explicit for IDE support
