"""Browser setup and the page interactions both scrape modes share."""

import asyncio
import os
import random
import re
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    Page,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeout,
)
from playwright_stealth import Stealth

from .errors import ScrapeError

# Claiming a Chrome version the bundled Chromium does not have is itself a
# bot signal, so the real version is filled in at launch.
USER_AGENT_TEMPLATE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/{version} Safari/537.36"
)
FALLBACK_CHROME_VERSION = "124.0.0.0"
VIEWPORT = {"width": 1440, "height": 900}
LOCALE = "en-GB"
NAVIGATION_TIMEOUT_MS = 45_000

# Selectors verified against booking.com on 2026-09-19.
_COOKIE_BANNER_SELECTORS = (
    '[id*="onetrust-accept"]',
    "button[data-gdpr-consent]",
    "#cookie_warning button",
    'button:has-text("Accept")',
    'button:has-text("Okay")',
)

_READ_ALL_REVIEWS_SELECTORS = (
    '[data-testid="fr-read-all-reviews"]',
    '[data-testid="review-score-read-all-actionable"]',
    'button:has-text("Read all reviews")',
    'a:has-text("Read all reviews")',
)

_BOT_CHECK_SELECTORS = (
    'iframe[src*="captcha"]',
    "#captcha",
    ".g-recaptcha",
    'h1:has-text("JavaScript is disabled")',
    'h1:has-text("not a robot")',
)

BOT_CHECK_MESSAGE = (
    "Booking.com served a bot check instead of the property page. "
    "Re-run without --headless, solve it in the browser window, then try again."
)


def chrome_user_agent(browser_version: str) -> str:
    """Build a desktop Chrome user agent matching the running browser."""
    match = re.match(r"\d+(?:\.\d+)*", browser_version or "")
    return USER_AGENT_TEMPLATE.format(version=match.group(0) if match else FALLBACK_CHROME_VERSION)


def launch_args() -> list[str]:
    """Chromium flags. The sandbox is only disabled where it cannot work."""
    args = ["--disable-blink-features=AutomationControlled"]
    # Running as root (containers, CI) is the case where Chromium's sandbox
    # refuses to start; as a normal user it works and should stay on.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")
    return args


async def human_delay(low: float = 0.5, high: float = 2.0) -> None:
    """Pause for a random interval, so interactions are not perfectly timed."""
    await asyncio.sleep(random.uniform(low, high))


@asynccontextmanager
async def browser_page(headless: bool):
    """Yield a stealth-patched page and its context, closing the browser after."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless, args=launch_args())
        try:
            context = await browser.new_context(
                user_agent=chrome_user_agent(browser.version),
                viewport=VIEWPORT,
                locale=LOCALE,
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
            yield page, context
        finally:
            try:
                await browser.close()
            except PlaywrightError:  # the browser may already be gone
                pass


async def open_property_page(page: Page, url: str) -> None:
    """Load a property page, clear the cookie banner and fail loudly on bot checks."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    except PlaywrightTimeout as exc:
        raise ScrapeError(f"Timed out loading {url}") from exc

    await human_delay(2.0, 3.0)
    await dismiss_cookie_banner(page)
    await raise_if_bot_check(page)


async def dismiss_cookie_banner(page: Page) -> None:
    """Accept the cookie banner if one is showing."""
    for selector in _COOKIE_BANNER_SELECTORS:
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=2000):
                await button.click()
                await human_delay(0.5, 1.0)
                return
        except PlaywrightError:
            continue


async def raise_if_bot_check(page: Page) -> None:
    """Raise ScrapeError when the page is a CAPTCHA or challenge page."""
    for selector in _BOT_CHECK_SELECTORS:
        try:
            if await page.locator(selector).count() > 0:
                raise ScrapeError(BOT_CHECK_MESSAGE)
        except PlaywrightError:
            continue


async def open_reviews_section(page: Page) -> bool:
    """Click "Read all reviews" to load the full review list.

    This click is what triggers the ReviewList GraphQL request the fast mode
    intercepts, so it runs in both modes. Returns whether a button was found;
    a False means every known selector is stale.
    """
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
    await human_delay(1.0, 2.0)

    for selector in _READ_ALL_REVIEWS_SELECTORS:
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=3000):
                await button.click()
                await human_delay(2.0, 3.0)
                return True
        except PlaywrightError:
            continue
    return False


async def wait_until(condition, timeout_s: float, interval_s: float = 0.25) -> bool:
    """Poll `condition` until it is true or the timeout expires."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if condition():
            return True
        await asyncio.sleep(interval_s)
    return bool(condition())


async def save_debug_snapshot(page: Page, directory: Path) -> list[Path]:
    """Write a screenshot and HTML dump for diagnosing selector breakage."""
    directory.mkdir(parents=True, exist_ok=True)
    screenshot = directory / "debug_reviews.png"
    html = directory / "debug_reviews.html"
    await page.screenshot(path=str(screenshot), full_page=True)
    html.write_text(await page.content(), encoding="utf-8")
    return [screenshot, html]
