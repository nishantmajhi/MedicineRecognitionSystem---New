"""
Compile ``levenshtein.c`` into a shared library the ctypes binding can load.

Run it directly, or let ``setup.bat`` / ``setup.sh`` / application startup call
it -- all three funnel through :func:`ensure_built`, which is idempotent and
cheap to call repeatedly.

    python -m src.clevenshtein.build            # build if out of date
    python -m src.clevenshtein.build --force    # rebuild unconditionally
    python -m src.clevenshtein.build --clean    # delete the artefact

Design rules
------------
**Never fatal.** If no compiler is installed, this reports what it looked for
and exits 0. ``src/Levenshtein.py`` falls back to the pure-Python
implementations, so the project runs everywhere -- just slower. Making the build
mandatory would trade a working install for a faster one, which is the wrong way
round for a project that has to be cloned and demonstrated.

**Idempotent.** The artefact is rebuilt only when it is missing or older than
the source, so putting this in the startup path costs a single ``stat`` on every
run after the first.

**No setuptools, no compiler flags from the environment.** The build is one
``cc`` invocation with a fixed flag list, which is the whole reason a raw shared
library was chosen over a CPython extension module: no ``Python.h``, no ABI tag,
no wheel, and the same command works on all three platforms.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "levenshtein.c"

# The artefact carries the platform and architecture in its name so a repository
# shared between machines -- or a Windows checkout opened under WSL -- cannot
# load a library built for the wrong target.
_SUFFIX = {"Windows": ".dll", "Darwin": ".dylib"}.get(platform.system(), ".so")
_TAG = f"{platform.system().lower()}-{platform.machine().lower()}"
LIBRARY = HERE / f"liblevenshtein-{_TAG}{_SUFFIX}"


# ---------------------------------------------------------------------------
# Compiler detection
# ---------------------------------------------------------------------------

def _msvc_command() -> Optional[List[str]]:
    """
    MSVC, if ``cl.exe`` is already on PATH.

    Only detected, never bootstrapped: locating a Visual Studio installation and
    sourcing ``vcvarsall.bat`` is a large amount of fragile machinery for a
    single ``.c`` file. On Windows the far more common outcome is that MinGW
    ``gcc`` is present, or that nothing is and the Python fallback runs.
    """
    if os.name != "nt" or shutil.which("cl") is None:
        return None
    return [
        "cl", "/nologo", "/O2", "/W3", "/LD",
        str(SOURCE),
        f"/Fe:{LIBRARY}",
        # Keep the intermediates out of the source tree.
        f"/Fo:{HERE / 'build' / ''}",
    ]


def _unix_command(compiler: str) -> List[str]:
    """A gcc/clang-compatible shared-library build."""
    flags = [compiler, "-O2", "-std=c99", "-Wall", "-Wextra", "-shared"]
    if os.name != "nt":
        # Position-independent code is required for a shared object on ELF and
        # Mach-O targets; MinGW rejects the flag with a warning, so skip it.
        flags.append("-fPIC")
        flags.append("-fvisibility=hidden")  # MRS_API re-exports what we want
    flags += [str(SOURCE), "-o", str(LIBRARY), "-lm"]
    if os.name != "nt":
        # The batch matcher spawns pthreads.
        flags.insert(1, "-pthread")
    return flags


def find_compiler() -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Return ``(command, compiler_name)`` for the first usable compiler.

    Order is deliberate: the platform-native toolchain first, then the portable
    ones. Returns ``(None, None)`` when nothing is available.
    """
    msvc = _msvc_command()
    if msvc is not None:
        return msvc, "cl (MSVC)"

    for candidate in ("cc", "gcc", "clang"):
        if shutil.which(candidate):
            return _unix_command(candidate), candidate

    return None, None


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def is_stale() -> bool:
    """True when the library is missing or older than the C source."""
    if not LIBRARY.exists():
        return True
    return SOURCE.stat().st_mtime > LIBRARY.stat().st_mtime


def ensure_built(force: bool = False, quiet: bool = False) -> bool:
    """
    Build the shared library if needed.

    Args:
        force: Rebuild even when the artefact looks current.
        quiet: Suppress progress output. Startup paths pass True; the CLI
            does not.

    Returns:
        True if a usable library exists afterwards, False otherwise. Callers
        treat False as "use the Python fallback", never as an error.
    """
    def say(message: str) -> None:
        if not quiet:
            print(message)

    if not SOURCE.exists():
        say(f"[clevenshtein] source missing: {SOURCE}")
        return False

    if not force and not is_stale():
        say(f"[clevenshtein] up to date: {LIBRARY.name}")
        return True

    command, name = find_compiler()
    if command is None:
        say(
            "[clevenshtein] no C compiler found (looked for cl, cc, gcc, clang).\n"
            "               Falling back to the pure-Python implementation --\n"
            "               everything still works, matching is just slower.\n"
            "               Install build-essential (Linux), Xcode command line\n"
            "               tools (macOS), or MSYS2/MinGW or Visual Studio Build\n"
            "               Tools (Windows) to enable it."
        )
        return False

    (HERE / "build").mkdir(exist_ok=True)
    say(f"[clevenshtein] compiling levenshtein.c with {name} ...")

    try:
        completed = subprocess.run(
            command,
            cwd=str(HERE),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as error:
        say(f"[clevenshtein] compiler could not be run: {error}")
        return False

    if completed.returncode != 0 or not LIBRARY.exists():
        say(f"[clevenshtein] build failed (exit {completed.returncode}):")
        say((completed.stderr or completed.stdout or "").strip()[:2000])
        say("[clevenshtein] continuing with the pure-Python implementation.")
        return False

    say(f"[clevenshtein] built {LIBRARY.name}")
    return True


def clean() -> None:
    """Remove the artefact and any intermediates."""
    for path in HERE.glob("liblevenshtein-*"):
        path.unlink()
        print(f"[clevenshtein] removed {path.name}")
    shutil.rmtree(HERE / "build", ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="rebuild even if the library looks current")
    parser.add_argument("--clean", action="store_true",
                        help="delete the compiled library and exit")
    arguments = parser.parse_args()

    if arguments.clean:
        clean()
        return 0

    ensure_built(force=arguments.force)
    # Always 0: a missing compiler is a supported configuration, not a failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
