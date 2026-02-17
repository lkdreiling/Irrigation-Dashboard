@echo off
:: This moves the command window to your F:\Weather folder
cd /d F:\Weather

:: This tells Windows where to find your Anaconda tools
call %USERPROFILE%\anaconda3\Scripts\activate.bat

echo Starting Irrigation Dashboard...
streamlit run et_dashboard.py

pause
