# GUI Automation for YouTube Downloads

This document explains how to use GUI automation as a fallback method for downloading YouTube videos when bot detection blocks automated downloads.

## Overview

When YouTube's bot detection blocks all automated download methods (yt-dlp, Invidious, Piped, Cobalt), the tool can automatically control a GUI application to download videos.

**Download Fallback Chain:**
1. **yt-dlp** with Android client + browser cookies
2. **Invidious API** (GitHub: iv-org/invidious)
3. **Piped API** (GitHub: TeamPiped/Piped)
4. **Cobalt API** (GitHub: imputnet/cobalt)
5. **GUI Automation** (GitHub: Tyrrrz/YoutubeDownloader) ← Final fallback

## Requirements

### 1. Install pywinauto

GUI automation requires the `pywinauto` library:

```bash
pip install pywinauto
```

**Note:** This feature is Windows-only.

### 2. Get YoutubeDownloader.exe

Download from: https://github.com/Tyrrrz/YoutubeDownloader/releases

**Installation:**
- Download the latest `.exe` file
- Place it in one of these locations (auto-detected):
  - `C:\Program Files\YoutubeDownloader\YoutubeDownloader.exe`
  - `%USERPROFILE%\Downloads\YoutubeDownloader.exe`
  - `%USERPROFILE%\Desktop\YoutubeDownloader.exe`
  - Project directory

Or specify a custom path with `--gui-downloader-path`

## Usage

### Basic Usage

The GUI automation is **automatic** - it activates when all other methods fail:

```bash
yt-audio-filter "https://youtube.com/watch?v=VIDEO_ID" --upload
```

### Specify Custom Path

If YoutubeDownloader.exe is in a custom location:

```bash
yt-audio-filter "https://youtube.com/watch?v=VIDEO_ID" \
  --upload \
  --gui-downloader-path "C:\path\to\YoutubeDownloader.exe"
```

### Example Script

Use the provided example script:

```bash
python process_with_gui_fallback.py
```

## How It Works

When GUI automation is triggered:

1. **Launch App**: Opens YoutubeDownloader.exe (or connects to existing instance)
2. **Find Controls**: Locates the URL input field and download button
3. **Enter URL**: Types the YouTube URL into the input field
4. **Start Download**: Clicks the download button (or presses Enter)
5. **Wait for Completion**: Monitors the output directory for new MP4 files
6. **Verify Download**: Checks that file size is stable (not still downloading)
7. **Continue Pipeline**: Processes the downloaded video normally

## What to Expect

### During Download

```
INFO: YouTube download blocked (bot detection), trying alternative methods...
INFO: Trying Invidious API (GitHub: iv-org/invidious)...
WARNING: Invidious API failed: 401 Unauthorized
INFO: Trying Piped API (GitHub: TeamPiped/Piped)...
WARNING: Piped API also failed: 502 Bad Gateway
INFO: Trying Cobalt API (GitHub: imputnet/cobalt)...
WARNING: Cobalt API also failed: JWT authentication required
INFO: Trying GUI automation (GitHub: Tyrrrz/YoutubeDownloader)...
INFO: This will launch YoutubeDownloader.exe and automate the download
INFO: Starting YoutubeDownloader.exe from: C:\...\YoutubeDownloader.exe
INFO: Downloading: https://youtube.com/watch?v=VIDEO_ID
INFO: Launching YoutubeDownloader.exe...
INFO: Connected to YoutubeDownloader GUI
INFO: Found URL input field
INFO: Entered URL: https://youtube.com/watch?v=VIDEO_ID
INFO: Found download button: Download
INFO: Initiated download
INFO: Waiting for download to complete (timeout: 600s)...
INFO: Download complete: VIDEO_ID.mp4 (45.2 MB)
INFO: Successfully downloaded via GUI automation
```

### GUI Window

You will see the YoutubeDownloader.exe window appear and the download progress. **Do not close this window** while downloading.

## Configuration

### Download Timeout

Default timeout is 600 seconds (10 minutes). For longer videos, modify `gui_downloader.py`:

```python
download_with_gui(
    url,
    output_dir,
    timeout=1200  # 20 minutes
)
```

### Output Directory

GUI downloads are saved to the same cache directory as other downloads:
```
cache/youtube/VIDEO_ID.mp4
```

## Troubleshooting

### Error: pywinauto not installed

**Solution:**
```bash
pip install pywinauto
```

### Error: YoutubeDownloader.exe not found

**Solutions:**
1. Download from: https://github.com/Tyrrrz/YoutubeDownloader/releases
2. Place in a common location (Downloads, Desktop, Program Files)
3. Or specify path: `--gui-downloader-path "C:\path\to\YoutubeDownloader.exe"`

### Error: Could not find URL input field

**Possible causes:**
- YoutubeDownloader.exe version changed its UI
- Window not focused properly

**Solution:**
- Update to latest version of YoutubeDownloader.exe
- Ensure no other windows are blocking the YoutubeDownloader window

### Error: Download timeout

**Possible causes:**
- Video is very long (>10 minutes timeout default)
- Slow internet connection
- YoutubeDownloader.exe encountered an error

**Solutions:**
- Increase timeout in `gui_downloader.py`
- Check YoutubeDownloader.exe window for error messages
- Try downloading manually first to verify URL works

### GUI window appears but nothing happens

**Solution:**
- Check that YoutubeDownloader.exe is the currently focused window
- Try clicking on the window manually
- Restart the script

## Advanced: Manual Testing

Test the GUI automation module directly:

```python
from pathlib import Path
from yt_audio_filter.gui_downloader import download_with_gui

result = download_with_gui(
    url="https://youtube.com/watch?v=VIDEO_ID",
    output_dir=Path("cache/youtube"),
    exe_path=Path("C:/path/to/YoutubeDownloader.exe"),
    timeout=600
)

print(f"Downloaded: {result.video_path}")
print(f"Title: {result.title}")
```

## Advantages

- **Bypasses Bot Detection**: GUI apps are harder for YouTube to detect/block
- **Automatic**: No manual intervention required
- **Reliable**: Uses official app from trusted GitHub project
- **Fallback**: Only used when other methods fail

## Disadvantages

- **Windows Only**: pywinauto is Windows-specific
- **Slower**: GUI automation has more overhead than API calls
- **Visible**: GUI window will appear during download
- **Dependency**: Requires external YoutubeDownloader.exe application

## Alternative: Manual Download

If GUI automation doesn't work, you can still manually download and process:

1. Download videos manually using YoutubeDownloader.exe
2. Save to `cache/youtube/` directory
3. Use `process_latest.py` to process the latest downloaded video:

```bash
python process_latest.py
```

## Contributing

If you encounter issues with GUI automation:
1. Check YoutubeDownloader.exe version
2. Update `gui_downloader.py` control detection logic
3. Submit issue with error details and YoutubeDownloader version

## See Also

- [YoutubeDownloader GitHub](https://github.com/Tyrrrz/YoutubeDownloader)
- [pywinauto Documentation](https://pywinauto.readthedocs.io/)
- [Main README](README.md)
