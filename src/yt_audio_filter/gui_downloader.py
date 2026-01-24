"""Automate YoutubeDownloader.exe GUI for downloading videos.

This module uses pywinauto to control the YoutubeDownloader GUI application,
enter YouTube URLs, and wait for downloads to complete.
"""

import time
import subprocess
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .logger import get_logger
from .exceptions import YouTubeDownloadError

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

logger = get_logger()

try:
    from pywinauto import Application
    from pywinauto.findwindows import ElementNotFoundError
    PYWINAUTO_AVAILABLE = True
except ImportError:
    PYWINAUTO_AVAILABLE = False
    logger.warning("pywinauto not available - GUI automation will not work")


@dataclass
class GuiDownloadResult:
    """Result of GUI-based download."""
    video_path: Path
    title: str


def find_youtube_downloader_exe() -> Optional[Path]:
    """
    Find YoutubeDownloader.exe in common locations.

    Returns:
        Path to YoutubeDownloader.exe or None if not found
    """
    # Common installation locations
    possible_paths = [
        Path.home() / "Downloads" / "YoutubeDownloader.exe",
        Path.home() / "Desktop" / "YoutubeDownloader.exe",
        Path("C:/Program Files/YoutubeDownloader/YoutubeDownloader.exe"),
        Path("C:/Program Files (x86)/YoutubeDownloader/YoutubeDownloader.exe"),
        # Current directory
        Path("YoutubeDownloader.exe"),
        Path("../YoutubeDownloader.exe"),
    ]

    for path in possible_paths:
        if path.exists():
            logger.info(f"Found YoutubeDownloader.exe at: {path}")
            return path

    return None


def download_with_gui(
    url: str,
    output_dir: Path,
    exe_path: Optional[Path] = None,
    timeout: int = 600,
) -> GuiDownloadResult:
    """
    Download a YouTube video by automating YoutubeDownloader.exe GUI.

    Args:
        url: YouTube video URL
        output_dir: Directory where downloaded videos should be saved
        exe_path: Path to YoutubeDownloader.exe (auto-detected if None)
        timeout: Maximum time to wait for download in seconds

    Returns:
        GuiDownloadResult with path to downloaded video

    Raises:
        YouTubeDownloadError: If download fails or GUI automation fails
    """
    if not PYWINAUTO_AVAILABLE:
        raise YouTubeDownloadError(
            "pywinauto not installed",
            "Install with: pip install pywinauto"
        )

    # Find the executable
    if exe_path is None:
        exe_path = find_youtube_downloader_exe()

    if exe_path is None or not exe_path.exists():
        raise YouTubeDownloadError(
            "YoutubeDownloader.exe not found",
            "Please provide the path to YoutubeDownloader.exe or place it in a common location"
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Starting YoutubeDownloader.exe from: {exe_path}")
    logger.info(f"Downloading: {url}")

    try:
        # Check if app is already running by process name
        existing_process = None
        if PSUTIL_AVAILABLE:
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    if proc.info['name'] and 'YoutubeDownloader' in proc.info['name']:
                        existing_process = proc.info['pid']
                        logger.info(f"Found existing YoutubeDownloader instance (PID: {existing_process})")
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        if existing_process:
            # Connect to existing process
            app = Application(backend="uia").connect(process=existing_process)
        else:
            # Launch the application
            logger.info("Launching YoutubeDownloader.exe...")
            proc = subprocess.Popen([str(exe_path)])
            time.sleep(5)  # Wait for app to start (increased for full initialization)

            # Connect to the launched process
            app = Application(backend="uia").connect(process=proc.pid)

        # Get the main window (top-level window, not a child)
        try:
            # Try exact title first
            main_window = app.window(title="YoutubeDownloader v1.15.2")
        except:
            # Fallback to regex
            main_window = app.top_window()

        main_window.set_focus()

        logger.info("Connected to YoutubeDownloader GUI")

        # Find and clear the URL input field
        # The exact control names depend on the app's UI structure
        # We'll try common patterns
        try:
            # Try to find text box by automation ID or class name
            url_input = None

            # Method 1: Find by control type (Edit control)
            for ctrl in main_window.descendants():
                if ctrl.element_info.control_type == "Edit":
                    url_input = ctrl
                    break

            if url_input is None:
                raise ElementNotFoundError("Could not find URL input field")

            logger.info("Found URL input field")

            # Clear and enter URL
            url_input.set_focus()
            url_input.set_edit_text("")
            time.sleep(0.5)
            url_input.type_keys(url, with_spaces=True)
            logger.info(f"Entered URL: {url}")

        except Exception as e:
            raise YouTubeDownloadError(
                "Failed to enter URL in GUI",
                f"Could not find or interact with URL input: {e}"
            )

        # Press Enter or click arrow button to fetch video info
        try:
            logger.info("Submitting URL to fetch video info...")
            url_input.type_keys("{ENTER}")

            # Wait for the app to fetch video info from YouTube and show popup
            # Poll for the DOWNLOAD button to appear
            logger.info("Waiting for video info popup with DOWNLOAD button...")

            download_button = None
            max_wait_time = 10  # Maximum 10 seconds to wait for popup
            start_time = time.time()

            while time.time() - start_time < max_wait_time:
                # Re-scan windows on each iteration
                for window in app.windows():
                    try:
                        # Re-get descendants each time (UI might have updated)
                        descendants = window.descendants()
                        for ctrl in descendants:
                            try:
                                ctrl_type = ctrl.element_info.control_type
                                ctrl_text = ctrl.window_text()
                                ctrl_name = ""
                                try:
                                    ctrl_name = ctrl.element_info.name
                                except:
                                    pass

                                # Check if this control is exactly "DOWNLOAD" button
                                # Match exactly, not partial matches like "Downloads"
                                if (ctrl_text and ctrl_text.strip().upper() == "DOWNLOAD") or \
                                   (ctrl_name and ctrl_name.strip().upper() == "DOWNLOAD"):
                                    download_button = ctrl
                                    logger.info(f"Found DOWNLOAD control: type={ctrl_type}, text='{ctrl_text}', name='{ctrl_name}'")
                                    break
                            except:
                                continue
                        if download_button:
                            break
                    except:
                        continue

                if download_button:
                    break

                # Wait a bit before checking again
                time.sleep(0.5)

            # If still not found, do detailed logging
            if download_button is None:
                logger.error("DOWNLOAD button not found after waiting. Collecting debug info...")
                all_controls_info = []

                for window in app.windows():
                    try:
                        window_title = window.window_text()
                        logger.info(f"=== Window: {window_title} ===")
                        descendants = window.descendants()
                        for ctrl in descendants:
                            try:
                                ctrl_type = ctrl.element_info.control_type
                                ctrl_text = ctrl.window_text()
                                ctrl_name = ""
                                try:
                                    ctrl_name = ctrl.element_info.name
                                except:
                                    pass
                                ctrl_automation_id = ""
                                try:
                                    ctrl_automation_id = ctrl.element_info.automation_id
                                except:
                                    pass

                                # Log ALL controls with their full info
                                info = f"{ctrl_type}"
                                if ctrl_text:
                                    info += f" text='{ctrl_text}'"
                                if ctrl_name and ctrl_name != ctrl_text:
                                    info += f" name='{ctrl_name}'"
                                if ctrl_automation_id:
                                    info += f" id='{ctrl_automation_id}'"

                                if ctrl_text or ctrl_name or ctrl_automation_id:
                                    all_controls_info.append(info)
                                    logger.info(f"  {info}")

                                # Try alternative matching for DOWNLOAD
                                if (ctrl_text and "DOWNLOAD" in ctrl_text.upper()) or \
                                   (ctrl_name and "DOWNLOAD" in ctrl_name.upper()):
                                    logger.warning(f"Found potential download control: {info}")
                            except Exception as e:
                                logger.debug(f"Error reading control: {e}")
                                continue
                    except Exception as e:
                        logger.debug(f"Error with window: {e}")
                        continue

                raise YouTubeDownloadError(
                    "Could not find DOWNLOAD button",
                    f"The popup dialog with DOWNLOAD button did not appear after {max_wait_time}s. See logs for all controls found."
                )

            # Click the DOWNLOAD button
            download_button.click()
            logger.info("Clicked DOWNLOAD button")
            time.sleep(1)  # Wait for Save As dialog

            # Look for and click the Save button in the Save As dialog
            logger.info("Looking for Save button in Save As dialog...")
            save_button = None
            for window in app.windows():
                for ctrl in window.descendants():
                    if ctrl.element_info.control_type == "Button":
                        try:
                            button_text = ctrl.window_text()
                            if button_text and button_text.strip().upper() == "SAVE":
                                save_button = ctrl
                                logger.info(f"Found Save button: {button_text}")
                                break
                        except:
                            continue
                if save_button:
                    break

            if save_button:
                save_button.click()
                logger.info("Clicked Save button")
                time.sleep(1)  # Wait for potential replacement confirmation dialog

                # Check for file replacement confirmation dialog
                logger.info("Checking for file replacement confirmation...")
                no_button = None
                for window in app.windows():
                    for ctrl in window.descendants():
                        if ctrl.element_info.control_type == "Button":
                            try:
                                button_text = ctrl.window_text()
                                # Look for No button (to skip redownload)
                                if button_text and button_text.strip().upper() == "NO":
                                    no_button = ctrl
                                    logger.info(f"Found No button: {button_text}")
                                    break
                            except:
                                continue
                    if no_button:
                        break

                if no_button:
                    # File already exists! Click No to cancel download and use existing file
                    no_button.click()
                    logger.info("File already exists - clicked No to use existing file instead of redownloading")

                    # Find the existing file from the filename in the Save As dialog
                    # The filename should be visible in an Edit control
                    existing_filename = None
                    for window in app.windows():
                        for ctrl in window.descendants():
                            try:
                                if ctrl.element_info.control_type == "Edit":
                                    ctrl_text = ctrl.window_text()
                                    if ctrl_text and ".mp4" in ctrl_text.lower():
                                        existing_filename = ctrl_text.strip()
                                        logger.info(f"Found existing filename: {existing_filename}")
                                        break
                            except:
                                continue
                        if existing_filename:
                            break

                    # Find the file in output_dir
                    if existing_filename:
                        existing_path = output_dir / existing_filename
                        if existing_path.exists():
                            logger.info(f"Using existing file: {existing_path.name} ({existing_path.stat().st_size / 1024 / 1024:.1f} MB)")
                            title = existing_path.stem
                            return GuiDownloadResult(
                                video_path=existing_path,
                                title=title
                            )

                    # Fallback: find most recently modified .mp4 in output_dir
                    logger.info("Looking for most recently modified .mp4 file in output directory...")
                    mp4_files = list(output_dir.glob("*.mp4"))
                    if mp4_files:
                        most_recent = max(mp4_files, key=lambda p: p.stat().st_mtime)
                        logger.info(f"Using most recent file: {most_recent.name} ({most_recent.stat().st_size / 1024 / 1024:.1f} MB)")
                        title = most_recent.stem
                        return GuiDownloadResult(
                            video_path=most_recent,
                            title=title
                        )
                    else:
                        raise YouTubeDownloadError(
                            "Could not find existing file",
                            "File replacement dialog appeared but could not locate the existing file"
                        )
                else:
                    logger.info("No replacement dialog found, download starting...")
            else:
                logger.warning("Could not find Save button, assuming download started automatically")

        except Exception as e:
            raise YouTubeDownloadError(
                "Failed to start download",
                f"Could not click download button: {e}"
            )

        # Wait for download to complete
        logger.info(f"Waiting for download to complete (timeout: {timeout}s)...")

        # Check multiple possible download locations
        # YoutubeDownloader often downloads to user's Downloads folder by default
        possible_download_dirs = [
            output_dir,  # Our preferred cache directory
            Path.home() / "Downloads",  # Windows default Downloads folder
        ]

        # Get list of files before download from all locations
        existing_files = {}
        for download_dir in possible_download_dirs:
            if download_dir.exists():
                existing_files[download_dir] = set(download_dir.glob("*.mp4"))
            else:
                existing_files[download_dir] = set()

        start_time = time.time()
        new_file = None
        source_dir = None
        download_start_time = start_time  # Track when download started for file time comparison

        while time.time() - start_time < timeout:
            # Check filesystem for new or updated files
            for download_dir in possible_download_dirs:
                if not download_dir.exists():
                    continue

                current_files = set(download_dir.glob("*.mp4"))
                new_files = current_files - existing_files[download_dir]

                if new_files:
                    # Check if file is still being written (size changing)
                    potential_file = list(new_files)[0]
                    size1 = potential_file.stat().st_size
                    time.sleep(1)
                    size2 = potential_file.stat().st_size

                    if size1 == size2 and size1 > 0:
                        # File size stable, download complete
                        new_file = potential_file
                        source_dir = download_dir
                        logger.info(f"Found completed download: {new_file.name}")
                        break
                    else:
                        logger.debug(f"Download in progress: {potential_file.name} ({size2 / 1024 / 1024:.1f} MB)")
                else:
                    # No new files - check if existing file was updated (replacement case)
                    for mp4_file in download_dir.glob("*.mp4"):
                        try:
                            mtime = mp4_file.stat().st_mtime
                            # Check if file was modified AFTER download started
                            if mtime > download_start_time:
                                # Check if file is still being written
                                size1 = mp4_file.stat().st_size
                                time.sleep(1)
                                size2 = mp4_file.stat().st_size

                                if size1 == size2 and size1 > 0:
                                    # File size stable, download complete
                                    new_file = mp4_file
                                    source_dir = download_dir
                                    logger.info(f"Found completed download (replacement): {new_file.name}")
                                    break
                                else:
                                    logger.debug(f"Download in progress (replacement): {mp4_file.name} ({size2 / 1024 / 1024:.1f} MB)")
                        except:
                            continue

                    if new_file:
                        break

            # If file found, exit the waiting loop
            if new_file:
                break

            # Check if app shows error
            try:
                for window in app.windows():
                    window_text = window.window_text()
                    if "error" in window_text.lower() or "failed" in window_text.lower():
                        raise YouTubeDownloadError(
                            "Download failed",
                            f"GUI shows error: {window_text}"
                        )
            except YouTubeDownloadError:
                raise
            except:
                pass

            # Wait before next check (reduced from 5s to 2s for faster response)
            time.sleep(2)

        if new_file is None:
            raise YouTubeDownloadError(
                "Download timeout",
                f"No new video file appeared in any download location within {timeout}s"
            )

        logger.info(f"Download complete: {new_file.name} ({new_file.stat().st_size / 1024 / 1024:.1f} MB)")
        logger.info(f"Downloaded to: {source_dir}")

        # If file was downloaded to a different location, move it to output_dir
        final_path = new_file
        if source_dir != output_dir:
            logger.info(f"Moving file from {source_dir} to {output_dir}")
            import shutil
            final_path = output_dir / new_file.name
            shutil.move(str(new_file), str(final_path))
            logger.info(f"File moved to: {final_path}")

        # Extract title from filename (remove extension)
        title = final_path.stem

        return GuiDownloadResult(
            video_path=final_path,
            title=title
        )

    except YouTubeDownloadError:
        raise
    except Exception as e:
        raise YouTubeDownloadError(
            "GUI automation failed",
            f"Unexpected error: {e}"
        )


def check_gui_downloader_available() -> bool:
    """
    Check if GUI downloader can be used.

    Returns:
        True if pywinauto is installed and YoutubeDownloader.exe is found
    """
    if not PYWINAUTO_AVAILABLE:
        return False

    return find_youtube_downloader_exe() is not None
