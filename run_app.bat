@echo off
REM YT Audio Filter - Streamlit Web App Launcher
REM Usage: run_app.bat

set PATH=%PATH%;c:\Users\hasaat\Documents\Personal\YT filter\yt-audio-filter\ffmpeg-8.0.1-essentials_build\bin
call "%~dp0venv\Scripts\activate.bat"

echo.
echo ========================================
echo   YT Audio Filter Web App
echo ========================================
echo.
echo Starting Streamlit server...
echo.

streamlit run "%~dp0src\yt_audio_filter\app\main.py" --server.headless true --browser.gatherUsageStats false

pause
