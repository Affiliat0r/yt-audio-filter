@echo off
set PATH=%PATH%;C:\Users\hasan\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin
call "%~dp0.venv\Scripts\activate.bat"
yt-audio-filter %* --upload --privacy public --gui-downloader-path "C:\Program Files\YTDownloader\YTDownloader.exe"
