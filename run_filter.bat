@echo off
set PATH=%PATH%;c:\Users\hasaat\Documents\Personal\YT filter\yt-audio-filter\ffmpeg-8.0.1-essentials_build\bin
call "%~dp0venv\Scripts\activate.bat"
yt-audio-filter %* --upload --privacy public
