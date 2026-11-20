"""Review agents for RevHive.

Imports are resolved lazily (PEP 562). Importing every agent class eagerly
here created a cycle that broke the most natural entry point:

    >>> from revhive.utils.dedup import deduplicate_and_sort   # ImportError

``dedup`` needs ``ReviewFinding`` from ``revhive.agents.base``, which runs this
module, which imported ``coordinator``, which imports ``dedup`` again — while it
was still only half defined. The cycle stayed invisible only because every
internal entry point happened to import an agent first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - for type checkers only
    from revhive.agents.coordinator import CoordinatorAgent  # noqa: F401 - re-exported lazily by __getattr__
    from revhive.agents.doc_agent import DocAgent  # noqa: F401 - re-exported lazily by __getattr__
    from revhive.agents.fix_agent import FixAgent  # noqa: F401 - re-exported lazily by __getattr__
    from revhive.agents.logic_agent import LogicAgent  # noqa: F401 - re-exported lazily by __getattr__
    from revhive.agents.performance_agent import PerformanceAgent  # noqa: F401 - re-exported lazily by __getattr__
    from revhive.agents.refactor_agent import RefactorAgent  # noqa: F401 - re-exported lazily by __getattr__
    from revhive.agents.repo_agent import RepoAgent  # noqa: F401 - re-exported lazily by __getattr__
    from revhive.agents.security_agent import SecurityAgent  # noqa: F401 - re-exported lazily by __getattr__
    from revhive.agents.style_agent import StyleAgent  # noqa: F401 - re-exported lazily by __getattr__
    from revhive.agents.test_agent import TestAgent  # noqa: F401 - re-exported lazily by __getattr__

_EXPORTS = {
    "StyleAgent": "revhive.agents.style_agent",
    "SecurityAgent": "revhive.agents.security_agent",
    "PerformanceAgent": "revhive.agents.performance_agent",
    "LogicAgent": "revhive.agents.logic_agent",
    "RepoAgent": "revhive.agents.repo_agent",
    "RefactorAgent": "revhive.agents.refactor_agent",
    "FixAgent": "revhive.agents.fix_agent",
    "TestAgent": "revhive.agents.test_agent",
    "DocAgent": "revhive.agents.doc_agent",
    "CoordinatorAgent": "revhive.agents.coordinator",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    """Import an agent class on first use."""
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
