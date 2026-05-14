"""RevHive - AI code review tool with 10 parallel agents."""

try:
    from importlib.metadata import version as _version
    __version__ = _version("revhive-ai")
except Exception:
    __version__ = "0.3.5"
