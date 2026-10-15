"""
Custom algorithm implementations for the Medicine Recognition System.

Names are re-exported lazily via ``__getattr__``. Importing them eagerly here
would pull in ``src.Levenshtein`` -- and through it the ctypes binding and the
compiled-library check -- merely because something touched the ``src`` package,
including ``python -m src.clevenshtein.build``, which is what builds that
library in the first place.
"""

__all__ = [
    'levenshtein_distance',
    'levenshtein_ratio',
    'indel_distance',
    'indel_ratio',
    'lcs_length',
    'ratio',
    'get_scorer',
    'edit_operations',
]


def __getattr__(name):
    if name in __all__:
        from . import Levenshtein
        return getattr(Levenshtein, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
