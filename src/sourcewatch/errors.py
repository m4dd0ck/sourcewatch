"""Exception hierarchy. Everything sourcewatch raises on purpose derives from SourcewatchError."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sourcewatch.observation import HttpDetail


class SourcewatchError(Exception):
    """Base class for all sourcewatch errors."""


class ConfigError(SourcewatchError):
    """A source definition or schema file is invalid."""


class ProbeError(SourcewatchError):
    """A probe could not observe its source; carries the HTTP detail when there is one."""

    def __init__(self, message: str, detail: HttpDetail | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class StoreError(SourcewatchError):
    """The data directory could not be read or written."""


class UnsafeOutputError(SourcewatchError):
    """Raised instead of deleting a folder sourcewatch did not create."""
