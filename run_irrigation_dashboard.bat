@echo off
cd /d "%~dp0"
title Irrigation Dashboard Launcher

:: 1. TRY ANACONDA FIRST
if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" (
    call "%USERPROFILE%\anaconda3\Scripts\activate.bat"
) else (
    :: 2. TRY SYSTEM PYTHON
    python --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo ERROR: Neither Anaconda nor System Python was found.
        pause
        exit
    )
)

:: 3. INSTALL LIBRARIES
echo Checking/Installing requirements...
pip install -r requirements.txt --quiet

:: 4. LAUNCH
cls
echo ==========================================
echo       STARTING IRRIGATION DASHBOARD
echo ==========================================
echo.
streamlit run et_dashboard.py
pause
