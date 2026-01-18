"""Automatically refresh YouTube cookies using Playwright.

This script logs into YouTube using stored credentials and exports
fresh cookies in Netscape format for use with yt-dlp.
"""

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

# Cookie file path
COOKIE_FILE = Path("cookies.txt")


async def refresh_youtube_cookies(
    email: str,
    password: str,
    output_path: Path = COOKIE_FILE,
    headless: bool = True,
) -> bool:
    """
    Log into YouTube and export fresh cookies.

    Args:
        email: YouTube/Google account email
        password: Account password
        output_path: Path to save cookies.txt
        headless: Run browser in headless mode

    Returns:
        True if successful, False otherwise
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        return False

    print(f"Starting YouTube login for {email[:3]}***@***")

    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            # Go to YouTube login
            print("Navigating to YouTube...")
            await page.goto("https://accounts.google.com/signin/v2/identifier?service=youtube")
            await page.wait_for_load_state("networkidle")

            # Enter email
            print("Entering email...")
            await page.fill('input[type="email"]', email)
            await page.click('button:has-text("Next")')
            await page.wait_for_timeout(3000)

            # Enter password
            print("Entering password...")
            await page.wait_for_selector('input[type="password"]', timeout=10000)
            await page.fill('input[type="password"]', password)
            await page.click('button:has-text("Next")')
            await page.wait_for_timeout(5000)

            # Check if we need to handle 2FA or other challenges
            current_url = page.url
            if "challenge" in current_url or "signin" in current_url:
                print("WARNING: Additional authentication required (2FA?)")
                print(f"Current URL: {current_url}")
                # Wait a bit more in case it's just loading
                await page.wait_for_timeout(5000)

            # Navigate to YouTube to ensure cookies are set
            print("Navigating to YouTube...")
            await page.goto("https://www.youtube.com")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(3000)

            # Check if logged in by looking for avatar or sign-in button
            is_logged_in = await page.query_selector('button[aria-label*="Account"]') is not None
            if not is_logged_in:
                # Alternative check
                is_logged_in = await page.query_selector('#avatar-btn') is not None

            if not is_logged_in:
                print("WARNING: May not be fully logged in, but will try to export cookies anyway")
            else:
                print("Successfully logged into YouTube!")

            # Get all cookies
            cookies = await context.cookies()

            # Filter to YouTube/Google cookies
            youtube_cookies = [c for c in cookies if 'youtube' in c['domain'] or 'google' in c['domain']]

            if not youtube_cookies:
                print("ERROR: No YouTube cookies found!")
                return False

            print(f"Exporting {len(youtube_cookies)} cookies...")

            # Convert to Netscape format
            netscape_cookies = cookies_to_netscape(youtube_cookies)

            # Write to file
            output_path.write_text(netscape_cookies)
            print(f"Cookies saved to {output_path}")

            return True

        except Exception as e:
            print(f"ERROR during login: {e}")
            # Take screenshot for debugging
            try:
                await page.screenshot(path="login_error.png")
                print("Screenshot saved to login_error.png")
            except:
                pass
            return False

        finally:
            await browser.close()


def cookies_to_netscape(cookies: list) -> str:
    """Convert Playwright cookies to Netscape format."""
    lines = ["# Netscape HTTP Cookie File"]
    lines.append("# This file was generated automatically by cookie_refresh.py")
    lines.append("")

    for cookie in cookies:
        domain = cookie.get('domain', '')
        # Netscape format: include_subdomains is TRUE if domain starts with .
        include_subdomains = "TRUE" if domain.startswith('.') else "FALSE"
        path = cookie.get('path', '/')
        secure = "TRUE" if cookie.get('secure', False) else "FALSE"
        # Expiry: use a far future date if not set
        expires = cookie.get('expires', -1)
        if expires == -1:
            # Session cookie - use 1 year from now
            expires = int(datetime.now(timezone.utc).timestamp()) + 365 * 24 * 60 * 60
        else:
            expires = int(expires)
        name = cookie.get('name', '')
        value = cookie.get('value', '')

        # Format: domain, include_subdomains, path, secure, expires, name, value
        line = f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expires}\t{name}\t{value}"
        lines.append(line)

    return "\n".join(lines)


def main():
    """CLI entry point."""
    email = os.environ.get("YOUTUBE_EMAIL")
    password = os.environ.get("YOUTUBE_PASSWORD")

    if not email or not password:
        print("ERROR: YOUTUBE_EMAIL and YOUTUBE_PASSWORD environment variables required")
        sys.exit(1)

    headless = os.environ.get("HEADLESS", "true").lower() == "true"
    output = Path(os.environ.get("COOKIE_OUTPUT", "cookies.txt"))

    success = asyncio.run(refresh_youtube_cookies(email, password, output, headless))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
