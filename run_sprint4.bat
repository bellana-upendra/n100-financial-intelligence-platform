@echo off
cd /d "%~dp0"
call .venv\Scripts\activate
set PYTHONPATH=%CD%
python -m src.analytics.valuation
if errorlevel 1 pause & exit /b 1
python -m streamlit run src\dashboard\app.py
