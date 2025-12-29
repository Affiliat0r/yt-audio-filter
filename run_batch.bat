@echo off
REM Process multiple YouTube videos from a URL list file
REM Usage: run_batch.bat urls_file.txt
REM Each video is downloaded, processed (music removed), and uploaded to YouTube

setlocal enabledelayedexpansion

set PATH=%PATH%;c:\Users\hasaat\Documents\Personal\YT filter\yt-audio-filter\ffmpeg-8.0.1-essentials_build\bin
call "%~dp0venv\Scripts\activate.bat"

if "%~1"=="" (
    echo Usage: run_batch.bat urls_file.txt
    echo.
    echo The file should contain one YouTube URL per line.
    echo Each video will be processed and uploaded to YouTube.
    echo.
    echo Example:
    echo   scrape_channel.bat @Niloya niloya_urls.txt
    echo   run_batch.bat niloya_urls.txt
    exit /b 1
)

set URLS_FILE=%~1

if not exist "%URLS_FILE%" (
    echo Error: File not found: %URLS_FILE%
    exit /b 1
)

echo ========================================
echo Batch Processing Started
echo URLs file: %URLS_FILE%
echo ========================================
echo.

set /a COUNT=0
set /a SUCCESS=0
set /a FAILED=0

for /f "usebackq tokens=*" %%u in ("%URLS_FILE%") do (
    set /a COUNT+=1
    echo.
    echo ----------------------------------------
    echo [!COUNT!] Processing: %%u
    echo ----------------------------------------

    yt-audio-filter "%%u" --upload --privacy public

    if !errorlevel! equ 0 (
        set /a SUCCESS+=1
        echo [!COUNT!] SUCCESS
    ) else (
        set /a FAILED+=1
        echo [!COUNT!] FAILED
    )
)

echo.
echo ========================================
echo Batch Processing Complete
echo ========================================
echo Total:   %COUNT%
echo Success: %SUCCESS%
echo Failed:  %FAILED%
echo ========================================
