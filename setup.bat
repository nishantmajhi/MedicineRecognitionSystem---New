@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Medicine Recognition System - Setup
echo ========================================
echo.

REM ---------------------------------------------------------------------------
REM  Step 0: check the Python version BEFORE doing anything else.
REM
REM  This guard exists because of a real failure. On Python 3.14 there are no
REM  wheels yet for scikit-image (an easyocr dependency) or for torch, so pip
REM  falls back to building scikit-image from source. That build runs pythran,
REM  whose headers need /std:c++17 while the build passes /std:c++14, and the
REM  result is roughly eight thousand lines of MSVC template errors ending in
REM  "metadata-generation-failed". None of it points at the actual cause.
REM
REM  Failing here in one line is worth the twenty lines of batch it takes.
REM ---------------------------------------------------------------------------
echo Checking Python version...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python was not found on PATH.
    echo Install Python 3.12 from https://www.python.org/downloads/
    echo and tick "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set PYMAJOR=%%a
    set PYMINOR=%%b
)

if !PYMAJOR! neq 3 goto :badversion
if !PYMINOR! lss 10 goto :badversion
if !PYMINOR! gtr 13 goto :badversion

echo Found Python !PYVER! - OK
echo.
goto :versionok

:badversion
echo ERROR: Python !PYVER! is not supported by this project.
echo.
echo   Supported: Python 3.10 - 3.13   ^(3.12 recommended^)
echo.
echo Why: easyocr depends on scikit-image and torch. Neither publishes
echo Windows wheels for Python 3.14 yet, so pip tries to compile
echo scikit-image from source and fails inside pythran with
echo   "error C7518: fold expressions require at least /std:c++17".
echo That error is a symptom of the Python version, not of your setup.
echo.
echo Fix: install Python 3.12 from https://www.python.org/downloads/
echo then delete the .venv folder and run setup.bat again.
echo.
pause
exit /b 1

:versionok

REM ---------------------------------------------------------------------------
REM  Step 1: directories
REM ---------------------------------------------------------------------------
echo Creating project directories...
mkdir MRS\temp 2>nul
mkdir MRS\media 2>nul
mkdir MRS\cache 2>nul
mkdir MRS\database 2>nul
mkdir MRS\model 2>nul
mkdir logs 2>nul
echo Done!
echo.

REM ---------------------------------------------------------------------------
REM  Step 2: virtual environment
REM ---------------------------------------------------------------------------
echo Creating virtual environment...
if exist .venv\Scripts\python.exe (
    echo .venv already exists, reusing it.
) else (
    python -m venv .venv
    if !errorlevel! neq 0 (
        echo ERROR: Failed to create virtual environment!
        pause
        exit /b 1
    )
)
echo Done!
echo.

echo Activating virtual environment...
call .venv\Scripts\activate.bat
echo Done!
echo.

echo Upgrading pip...
python -m pip install --upgrade pip
echo Done!
echo.

REM ---------------------------------------------------------------------------
REM  Step 3: dependencies
REM ---------------------------------------------------------------------------
echo Installing required packages...
echo This downloads torch and may take several minutes.
pip install -r requirements.txt
if !errorlevel! neq 0 (
    echo.
    echo ERROR: Failed to install requirements!
    echo.
    echo If the log above mentions scikit-image, pythran or "C7518",
    echo check your Python version - see the note at the top of this script.
    pause
    exit /b 1
)
echo Done!
echo.

REM ---------------------------------------------------------------------------
REM  Step 4: compile the Levenshtein kernel
REM
REM  Deliberately NOT fatal. If no C compiler is installed, the build script
REM  says so and the project falls back to the pure-Python implementation in
REM  src/_levenshtein_python.py. Everything works; matching is slower.
REM ---------------------------------------------------------------------------
echo Compiling the C Levenshtein kernel...
python -m src.clevenshtein.build
echo.

REM ---------------------------------------------------------------------------
REM  Step 5: drug-name list placeholder
REM ---------------------------------------------------------------------------
echo Checking the drug names database...
if not exist MRS\database\ndc_generic-names.txt (
    (
        echo ibuprofen
        echo acetaminophen
        echo aspirin
        echo paracetamol
        echo amoxicillin
    ) > MRS\database\ndc_generic-names.txt
    echo Created a 5-name placeholder. Build the real list with:
    echo   python test\generic_name-extractor.py
    echo   python tools\expand_name_list.py
) else (
    echo File already exists, skipping...
)
echo Done!
echo.

REM ---------------------------------------------------------------------------
REM  NOTE: this script no longer writes the package __init__.py files.
REM
REM  The previous version generated them on every run with "echo ... >" and
REM  overwrote the real ones. Its generated utils\helper\__init__.py imported
REM  DrugInformationService from .drug_information_service - a module that does
REM  not exist; the real one is drug_information.py exporting DrugInformation.
REM  So running setup.bat on a fresh clone left the project unable to start.
REM
REM  Those files are committed to the repository and are source, not build
REM  output. Generating source from a setup script is how they drifted.
REM ---------------------------------------------------------------------------

echo ========================================
echo Setup completed successfully!
echo ========================================
echo.
echo Verify the install:
echo   python -c "from src.Levenshtein import backend_report; print(backend_report())"
echo   pytest
echo   python benchmark_levenshtein.py
echo.
echo Run the application:
echo   .venv\Scripts\activate
echo   python main.py
echo.
echo Then open http://localhost:8000
echo.
pause
