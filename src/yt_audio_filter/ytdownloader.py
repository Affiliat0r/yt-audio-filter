"""YTDownloader GUI automation for downloading YouTube videos."""

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .exceptions import YouTubeDownloadError
from .logger import get_logger

logger = get_logger()

# Check for pywinauto
try:
    from pywinauto import Application
    from pywinauto.keyboard import send_keys
    import pyperclip
    PYWINAUTO_AVAILABLE = True
except ImportError:
    PYWINAUTO_AVAILABLE = False

# Check for psutil
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Check for pyautogui (needed for clicking in Electron apps)
try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False


@dataclass
class YTDownloadResult:
    """Result of a YTDownloader download."""
    video_path: Path
    title: str


def download_with_ytdownloader(
    url: str,
    output_dir: Path,
    exe_path: Optional[Path] = None,
    timeout: int = 600,
) -> YTDownloadResult:
    """
    Download a YouTube video using YTDownloader GUI automation.

    Args:
        url: YouTube video URL
        output_dir: Directory to save the downloaded video
        exe_path: Path to YTDownloader.exe (default: C:\\Program Files\\YTDownloader\\YTDownloader.exe)
        timeout: Maximum time to wait for download in seconds

    Returns:
        YTDownloadResult with video path and title

    Raises:
        YouTubeDownloadError: If download fails
    """
    if not PYWINAUTO_AVAILABLE:
        raise YouTubeDownloadError(
            "pywinauto not installed",
            "Install with: pip install pywinauto pyperclip"
        )

    # Default exe path
    if exe_path is None:
        exe_path = Path(r"C:\Program Files\YTDownloader\YTDownloader.exe")

    if not exe_path.exists():
        raise YouTubeDownloadError(
            "YTDownloader not found",
            f"Expected at: {exe_path}"
        )

    logger.info(f"Downloading: {url}")

    # YTDownloader saves to Downloads folder
    downloads_dir = Path.home() / "Downloads"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check if video was already downloaded by checking YTDownloader history
    history_file = Path.home() / "AppData" / "Roaming" / "ytdownloader" / "download_history.json"
    if history_file.exists():
        try:
            import json
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
            for entry in history:
                if entry.get('url') == url:
                    file_path = Path(entry.get('filePath', ''))
                    if file_path.exists() and file_path.stat().st_size > 1000000:
                        logger.info(f"Video already downloaded: {file_path.name}")
                        return YTDownloadResult(
                            video_path=file_path,
                            title=file_path.stem
                        )
        except Exception as e:
            logger.debug(f"Could not check download history: {e}")

    logger.info(f"Starting YTDownloader from: {exe_path}")

    # Get existing mp4 files before download
    possible_dirs = [output_dir, downloads_dir]
    existing_files = {}
    for d in possible_dirs:
        if d.exists():
            existing_files[d] = set(d.glob("*.mp4"))

    # Copy URL to clipboard
    pyperclip.copy(url)
    logger.info("Copied URL to clipboard")

    # Check if YTDownloader is already running
    existing_process = None
    if PSUTIL_AVAILABLE:
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['name'] and 'YTDownloader' in proc.info['name']:
                    existing_process = proc.info['pid']
                    logger.info(f"Found existing YTDownloader instance (PID: {existing_process})")
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    # Possible window titles for YTDownloader (Electron app)
    possible_titles = ['YtDownloader', 'ytDownloader', 'YTDownloader', 'yt Downloader']

    def try_connect():
        """Try to connect to an existing YTDownloader window."""
        for title in possible_titles:
            try:
                app = Application(backend="uia").connect(title=title, timeout=3)
                main_window = app.window(title=title)
                return app, main_window
            except Exception:
                continue
        # Also try title_re pattern for partial match
        try:
            app = Application(backend="uia").connect(title_re='.*[Yy]t.*[Dd]ownload.*', timeout=3)
            windows = app.windows()
            if windows:
                return app, windows[0]
        except Exception:
            pass
        return None, None

    app, main_window = None, None

    if existing_process:
        # Try to connect to existing process
        app, main_window = try_connect()
        if main_window:
            main_window.set_focus()
            time.sleep(0.5)

    if not main_window:
        # Launch YTDownloader using explorer.exe (simulates double-click, works for Electron apps)
        logger.info("Launching YTDownloader.exe...")
        subprocess.Popen(['explorer.exe', str(exe_path)])
        time.sleep(8)  # Wait for Electron app to start

        # Try to connect
        app, main_window = try_connect()

        if not main_window:
            # Wait a bit more and try again
            time.sleep(5)
            app, main_window = try_connect()

        if not main_window:
            raise YouTubeDownloadError(
                "Could not connect to YTDownloader",
                "YTDownloader failed to start. Please check if the app is installed correctly."
            )

    logger.info("Connected to YTDownloader GUI")

    try:
        # Focus the window and paste URL with Ctrl+V
        main_window.set_focus()
        time.sleep(0.5)

        logger.info("Pasting URL with Ctrl+V...")
        send_keys('^v')  # Ctrl+V

        # Wait for video info to load
        logger.info("Waiting for video info to load...")
        time.sleep(8)  # Give time for the video info dialog to appear

        # Click the Download button using pyautogui (Electron apps don't expose internal controls via UIA)
        logger.info("Clicking Download button...")
        if PYAUTOGUI_AVAILABLE:
            rect = main_window.rectangle()
            center_x = (rect.left + rect.right) // 2

            # Grid search for Download button (green button left of center, near bottom)
            # The button position varies slightly based on window size
            x_offsets = [-150, -100, -50]  # Left of center
            y_positions = [rect.bottom - 200, rect.bottom - 170, rect.bottom - 140]

            for x_off in x_offsets:
                for y_pos in y_positions:
                    btn_x = center_x + x_off
                    pyautogui.click(btn_x, y_pos)
                    time.sleep(0.2)

            logger.info("Clicked Download button area")
        else:
            # Fallback to keyboard navigation
            send_keys('{TAB}{TAB}{ENTER}')

        time.sleep(2)

        # Wait for download to complete
        logger.info(f"Waiting for download to complete (timeout: {timeout}s)...")

        download_start = time.time()
        new_file = None

        while time.time() - download_start < timeout:
            # Check for new mp4 files
            for d in possible_dirs:
                if not d.exists():
                    continue
                current_files = set(d.glob("*.mp4"))
                new_files = current_files - existing_files.get(d, set())

                if new_files:
                    # Check if file is still being written
                    potential_file = list(new_files)[0]
                    try:
                        size1 = potential_file.stat().st_size
                        time.sleep(2)
                        size2 = potential_file.stat().st_size

                        if size1 == size2 and size1 > 1000000:  # At least 1MB and stable
                            new_file = potential_file
                            logger.info(f"Download complete: {new_file.name}")
                            break
                        else:
                            logger.debug(f"Download in progress: {potential_file.name} ({size2/1024/1024:.1f} MB)")
                    except:
                        pass

                # Also check for recently modified files
                for f in d.glob("*.mp4"):
                    try:
                        if time.time() - f.stat().st_mtime < 30:  # Modified in last 30 seconds
                            size1 = f.stat().st_size
                            time.sleep(2)
                            size2 = f.stat().st_size
                            if size1 == size2 and size1 > 1000000:
                                new_file = f
                                logger.info(f"Download complete: {new_file.name}")
                                break
                    except:
                        pass

                if new_file:
                    break

            if new_file:
                break

            time.sleep(3)

        if new_file is None:
            raise YouTubeDownloadError(
                "Download timeout",
                f"No new video file found within {timeout}s"
            )

        # Move file to output_dir if not already there
        if new_file.parent != output_dir:
            dest = output_dir / new_file.name
            if not dest.exists():
                import shutil
                shutil.move(str(new_file), str(dest))
                new_file = dest
                logger.info(f"Moved to: {dest}")

        logger.info(f"Downloaded: {new_file.name} ({new_file.stat().st_size/1024/1024:.1f} MB)")

        return YTDownloadResult(
            video_path=new_file,
            title=new_file.stem
        )

    except Exception as e:
        if isinstance(e, YouTubeDownloadError):
            raise
        raise YouTubeDownloadError(
            "YTDownloader automation failed",
            str(e)
        )
