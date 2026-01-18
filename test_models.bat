@echo off
echo ============================================
echo Testing Different Demucs Models
echo ============================================
echo.
echo This will test three models on a short video:
echo 1. htdemucs (default, best quality)
echo 2. mdx_extra (alternative)
echo 3. mdx_extra_q (quantized, fastest)
echo.
echo Press Ctrl+C to cancel, or
pause

set PATH=%PATH%;C:\Users\hasan\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin
call "%~dp0.venv\Scripts\activate.bat"

set TEST_URL=%1
if "%TEST_URL%"=="" set TEST_URL=https://www.youtube.com/watch?v=euADpFHHVng

echo.
echo ============================================
echo Testing htdemucs model...
echo ============================================
yt-audio-filter "%TEST_URL%" --model htdemucs --output-dir output/htdemucs
echo.

echo ============================================
echo Testing mdx_extra model...
echo ============================================
yt-audio-filter "%TEST_URL%" --model mdx_extra --output-dir output/mdx_extra
echo.

echo ============================================
echo Testing mdx_extra_q model...
echo ============================================
yt-audio-filter "%TEST_URL%" --model mdx_extra_q --output-dir output/mdx_extra_q
echo.

echo ============================================
echo All tests complete!
echo Check output/ folder for results
echo ============================================
pause
