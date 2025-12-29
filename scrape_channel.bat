@echo off
REM Scrape video URLs from a YouTube channel
REM Usage: scrape_channel.bat @ChannelName [output_file.txt]

set PATH=%PATH%;c:\Users\hasaat\Documents\Personal\YT filter\yt-audio-filter\ffmpeg-8.0.1-essentials_build\bin
call "%~dp0venv\Scripts\activate.bat"

if "%~1"=="" (
    echo Usage: scrape_channel.bat @ChannelName [output_file.txt]
    echo.
    echo Examples:
    echo   scrape_channel.bat @Niloya
    echo   scrape_channel.bat @Niloya niloya_videos.txt
    echo   scrape_channel.bat @Niloya niloya_videos.txt 50
    exit /b 1
)

set CHANNEL=%~1
set OUTPUT=%~2
set MAX=%~3

if "%OUTPUT%"=="" (
    REM Print to stdout
    yt-channel-scrape %CHANNEL%
) else if "%MAX%"=="" (
    REM Save all to file
    yt-channel-scrape %CHANNEL% -o %OUTPUT%
) else (
    REM Save limited to file
    yt-channel-scrape %CHANNEL% -o %OUTPUT% -n %MAX%
)
