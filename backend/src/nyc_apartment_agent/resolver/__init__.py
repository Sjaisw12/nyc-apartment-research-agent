"""Address resolution: turn pasted text into a verified, ambiguous or rejected building match."""

from .models import Outcome, ResolutionLevel, ResolutionResult
from .resolve import resolve_address

__all__ = ["Outcome", "ResolutionLevel", "ResolutionResult", "resolve_address"]
