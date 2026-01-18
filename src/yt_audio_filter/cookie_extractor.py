"""Extract YouTube cookies using Playwright headless browser.

This bypasses bot detection by using a real browser session.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from .logger import get_logger

logger = get_logger()


async def extract_youtube_cookies(output_path: Optional[Path] = None) -> Path:
    """
    Launch a headless browser, visit YouTube, and extract cookies.

    Args:
        output_path: Where to save cookies.txt (default: ./cookies.txt)

    Returns:
        Path to the cookies file
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        raise

    if output_path is None:
        output_path = Path.cwd() / "cookies.txt"

    logger.info("Launching headless browser to extract YouTube cookies...")

    async with async_playwright() as p:
        # Launch with realistic browser settings
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ]
        )

        # Create context with realistic fingerprint
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
        )

        page = await context.new_page()

        # Visit YouTube homepage first
        logger.debug("Visiting YouTube homepage...")
        await page.goto("https://www.youtube.com", wait_until="networkidle", timeout=30000)

        # Wait a bit to let cookies settle
        await asyncio.sleep(2)

        # Accept cookies consent if present
        try:
            accept_button = page.locator("button:has-text('Accept all')")
            if await accept_button.count() > 0:
                await accept_button.click()
                await asyncio.sleep(1)
        except Exception:
            pass  # No consent dialog

        # Visit a video page to get more cookies
        logger.debug("Visiting a sample video page...")
        try:
            # Visit a popular/safe video
            await page.goto(
                "https://www.youtube.com/watch?v=jNQXAC9IVRw",  # "Me at the zoo" - first YT video
                wait_until="domcontentloaded",
                timeout=30000
            )
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning(f"Could not visit video page: {e}")

        # Get all cookies
        cookies = await context.cookies()
        logger.info(f"Extracted {len(cookies)} cookies from YouTube")

        await browser.close()

    # Convert to Netscape cookie format for yt-dlp
    write_netscape_cookies(cookies, output_path)

    logger.info(f"Cookies saved to {output_path}")
    return output_path


def write_netscape_cookies(cookies: list, output_path: Path):
    """Write cookies in Netscape format that yt-dlp understands."""
    lines = ["# Netscape HTTP Cookie File", "# https://curl.se/docs/http-cookies.html", ""]

    for cookie in cookies:
        # Filter to YouTube cookies only
        domain = cookie.get("domain", "")
        if "youtube" not in domain and "google" not in domain:
            continue

        # Netscape format: domain, subdomain_access, path, secure, expiry, name, value
        domain_initial_dot = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if cookie.get("secure", False) else "FALSE"
        expiry = int(cookie.get("expires", 0)) if cookie.get("expires") else 0

        line = "\t".join([
            domain,
            domain_initial_dot,
            cookie.get("path", "/"),
            secure,
            str(expiry),
            cookie.get("name", ""),
            cookie.get("value", ""),
        ])
        lines.append(line)

    output_path.write_text("\n".join(lines), encoding="utf-8")


def extract_cookies_sync(output_path: Optional[Path] = None) -> Path:
    """Synchronous wrapper for extract_youtube_cookies."""
    return asyncio.run(extract_youtube_cookies(output_path))


def main():
    """CLI entry point for cookie extraction."""
    import argparse
    from .logger import setup_logger

    parser = argparse.ArgumentParser(description="Extract YouTube cookies using headless browser")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output path for cookies.txt")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    setup_logger(verbose=args.verbose)

    try:
        cookie_path = extract_cookies_sync(args.output)
        print(f"Cookies saved to: {cookie_path}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
