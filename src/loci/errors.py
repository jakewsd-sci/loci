"""Errors raised by loci tools.

Every error carries a message saying what went wrong and, where possible, a hint saying what to
do about it, so an agent can recover without guessing.
"""


class LociError(Exception):
    """Base class for errors reported back to the agent."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint is None:
            return self.message
        return f"{self.message} Hint: {self.hint}"


class UnknownRefError(LociError):
    """A data_ref is malformed or not registered in the workspace."""


class UnsupportedFormatError(LociError):
    """A source's format cannot be determined or is not supported."""


class LoadError(LociError):
    """A source exists in a supported format but cannot be read as requested."""
