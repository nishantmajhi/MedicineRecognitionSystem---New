"""
The compiled edit-distance kernel.

``levenshtein.c`` holds the implementations; ``build.py`` turns it into a shared
library. Nothing here is imported by the application directly --
``src.Levenshtein`` is the public entry point and decides which backend to use.

``build`` is imported lazily so that ``python -m src.clevenshtein.build`` does
not import the module twice.
"""

__all__ = ["LIBRARY", "ensure_built", "is_stale"]


def __getattr__(name):
    if name in __all__:
        from . import build
        return getattr(build, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
