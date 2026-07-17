@echo off
set COMMAND=%1

if "%COMMAND%"=="load" (
    python -m src.etl.run_pipeline
    goto :eof
)
if "%COMMAND%"=="ratios" (
    python -m src.ratios
    goto :eof
)
if "%COMMAND%"=="test" (
    python -m pytest -q
    goto :eof
)
if "%COMMAND%"=="report" (
    python -m src.report
    goto :eof
)
if "%COMMAND%"=="dashboard" (
    python -m src.dashboard
    goto :eof
)
if "%COMMAND%"=="api" (
    python -m src.api
    goto :eof
)
if "%COMMAND%"=="verify" (
    python scripts\verify_database.py
    goto :eof
)
if "%COMMAND%"=="clean" (
    python scripts\clean.py
    goto :eof
)

echo Usage: tasks.bat [load^|ratios^|test^|report^|dashboard^|api^|verify^|clean]
