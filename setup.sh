#!/usr/bin/env bash
#
# Medicine Recognition System - setup for Linux and macOS.
# The POSIX counterpart of setup.bat; the two do the same things in the same
# order, so a problem reproduced on one platform is diagnosable on the other.
#
#   ./setup.sh
#
set -euo pipefail

echo "========================================"
echo "Medicine Recognition System - Setup"
echo "========================================"
echo

# ---------------------------------------------------------------------------
# Step 0: Python version.
#
# easyocr pulls in scikit-image and torch. Neither ships wheels for Python 3.14
# yet, so pip tries to compile scikit-image from source and dies inside pythran
# with a C++17 error that says nothing about the real cause. Check first.
# ---------------------------------------------------------------------------
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "ERROR: $PYTHON not found. Install Python 3.12."
    exit 1
fi

read -r PYMAJOR PYMINOR <<<"$("$PYTHON" -c 'import sys; print(sys.version_info[0], sys.version_info[1])')"

if [ "$PYMAJOR" -ne 3 ] || [ "$PYMINOR" -lt 10 ] || [ "$PYMINOR" -gt 13 ]; then
    cat <<EOF
ERROR: Python ${PYMAJOR}.${PYMINOR} is not supported by this project.

  Supported: Python 3.10 - 3.13   (3.12 recommended)

Why: easyocr depends on scikit-image and torch, and neither publishes wheels
for Python 3.14 yet. pip falls back to building scikit-image from source, which
fails inside pythran with "fold expressions require at least /std:c++17".
That error is a symptom of the Python version, not of your setup.

Fix: install Python 3.12, then re-run with
  PYTHON=python3.12 ./setup.sh
EOF
    exit 1
fi

echo "Found Python ${PYMAJOR}.${PYMINOR} - OK"
echo

# ---------------------------------------------------------------------------
# Step 1: directories
# ---------------------------------------------------------------------------
echo "Creating project directories..."
mkdir -p MRS/temp MRS/media MRS/cache MRS/database MRS/model logs
echo "Done!"
echo

# ---------------------------------------------------------------------------
# Step 2: virtual environment
# ---------------------------------------------------------------------------
if [ -d .venv ]; then
    echo ".venv already exists, reusing it."
else
    echo "Creating virtual environment..."
    "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "Done!"
echo

echo "Upgrading pip..."
python -m pip install --upgrade pip --quiet
echo "Done!"
echo

# ---------------------------------------------------------------------------
# Step 3: dependencies
# ---------------------------------------------------------------------------
echo "Installing required packages (this downloads torch; give it a few minutes)..."
if ! pip install -r requirements.txt; then
    echo
    echo "ERROR: Failed to install requirements."
    echo "If the log mentions scikit-image or pythran, check the Python version."
    exit 1
fi
echo "Done!"
echo

# ---------------------------------------------------------------------------
# Step 4: compile the Levenshtein kernel
#
# Not fatal by design. Without a compiler the project falls back to the
# pure-Python implementation and still runs, just more slowly.
# ---------------------------------------------------------------------------
echo "Compiling the C Levenshtein kernel..."
python -m src.clevenshtein.build
echo

# ---------------------------------------------------------------------------
# Step 5: drug-name list placeholder
# ---------------------------------------------------------------------------
if [ ! -f MRS/database/ndc_generic-names.txt ]; then
    printf 'ibuprofen\nacetaminophen\naspirin\nparacetamol\namoxicillin\n' \
        > MRS/database/ndc_generic-names.txt
    echo "Created a 5-name placeholder. Build the real list with:"
    echo "  python dataset/generic_name-extractor.py"
    echo "  python tools/expand_name_list.py"
else
    echo "Drug name list already exists, skipping."
fi
echo

echo "========================================"
echo "Setup completed successfully!"
echo "========================================"
echo
echo "Verify the install:"
echo "  python -c 'from src.Levenshtein import backend_report; print(backend_report())'"
echo "  pytest"
echo "  python benchmark_levenshtein.py"
echo
echo "Run the application:"
echo "  source .venv/bin/activate"
echo "  python main.py"
echo
echo "Then open http://localhost:8000"
