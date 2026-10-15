"""
Shared pytest fixtures and test bootstrapping.

Two environment problems are handled here so the suite runs on a bare checkout:

* The project root must be importable (``src``, ``utils``, ``routes``, ...).
* ``easyocr`` pulls in Torch and several hundred MB of model weights. The OCR
  reader is lazily constructed, so tests never need the real package -- if it is
  absent we install a stub module. Any test that genuinely needs OCR should
  build its own fake reader rather than relying on the stub.
"""

import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _install_easyocr_stub() -> None:
    """Register a placeholder ``easyocr`` module when the real one is missing."""
    if "easyocr" in sys.modules:
        return
    try:
        import easyocr  # noqa: F401
    except ImportError:
        stub = types.ModuleType("easyocr")

        class _UnavailableReader:
            def __init__(self, *args, **kwargs):
                raise RuntimeError(
                    "easyocr is not installed; tests must inject a fake reader"
                )

        stub.Reader = _UnavailableReader
        sys.modules["easyocr"] = stub


_install_easyocr_stub()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DRUG_NAMES = [
    "ibuprofen",
    "acetaminophen",
    "paracetamol",
    "amoxicillin",
    "aspirin",
    "cetirizine",
    "omeprazole",
    "metformin",
    "azithromycin",
    "loratadine",
    "dextromethorphan hydrobromide",
]


@pytest.fixture
def ndc_file(tmp_path):
    """A small stand-in for the NDC generic-name list."""
    path = tmp_path / "ndc_generic-names.txt"
    path.write_text("\n".join(SAMPLE_DRUG_NAMES) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def drug_matcher(ndc_file):
    """
    A DrugMatcher backed by the sample name list, using the shipped defaults:
    classic Levenshtein scoring at the calibrated threshold of 75.
    """
    from utils.helper.drug_matcher import DrugMatcher

    return DrugMatcher(str(ndc_file), similarity_threshold=75)


@pytest.fixture
def sample_image():
    """A small blank BGR image, valid input for OpenCV encoding."""
    import numpy as np

    return np.full((40, 60, 3), 255, dtype=np.uint8)


@pytest.fixture
def fda_payload():
    """A trimmed openFDA label response."""
    return {
        "results": [
            {
                "purpose": ["Pain reliever/fever reducer"],
                "do_not_use": ["Do not use with other drugs containing NSAIDs"],
            }
        ]
    }
