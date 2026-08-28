@echo off
REM Quran Studio render worker (Windows convenience wrapper).
REM
REM Runs from the repository root so relative paths in the pipeline
REM (cache\, config\channels.json, examples\*.json) resolve correctly.
REM
REM Usage:
REM   run_worker.bat                 start the polling loop
REM   run_worker.bat --once -v       run a single job with debug logging

setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo Create the venv first:  python -m venv .venv ^&^& .venv\Scripts\pip install -e .
    exit /b 1
)

call ".venv\Scripts\activate.bat"
".venv\Scripts\python.exe" -m worker.worker %*
set EXITCODE=%ERRORLEVEL%

endlocal & exit /b %EXITCODE%
