"""RevHive - AI code review tool with 10 parallel agents."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("revhive-ai")
except PackageNotFoundError:
    # Running from a source checkout that was never pip-installed. Reading the
    # version out of pyproject beats a hand-maintained constant: the constant
    # had drifted eighteen releases behind by the time anyone ran --version
    # from the repo.
    try:
        from pathlib import Path

        import tomllib

        _pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        with _pyproject.open("rb") as fh:
            __version__ = tomllib.load(fh)["project"]["version"]
    except (OSError, ImportError, KeyError, ValueError):
        __version__ = "0.0.0+unknown"
