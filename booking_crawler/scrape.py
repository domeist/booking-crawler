"""Orchestration: drive a browser to a property page and collect its reviews."""

from datetime import datetime
from pathlib import Path
from typing import Protocol

from . import reviews_api, reviews_dom
from .browser import (
    browser_page,
    open_property_page,
    open_reviews_section,
    raise_if_bot_check,
    save_debug_snapshot,
    wait_until,
)
from .errors import ScrapeError
from .metadata import extract_metadata
from .report import clean_url

MODE_FAST = "fast"
MODE_STANDARD = "standard"
MODES = (MODE_FAST, MODE_STANDARD)

# How long to keep watching for the review API request after the click. The
# response is what carries the reviews, and a throttled connection can take
# several seconds to produce it.
CAPTURE_TIMEOUT_S = 10.0


class Reporter(Protocol):
    """Where progress goes. The CLI prints it; the library ignores it."""

    def status(self, message: str) -> None: ...

    def progress(self, collected: int, total: int | None) -> None: ...

    def warn(self, message: str) -> None: ...


class _NullReporter:
    """Default reporter, so the library is silent unless a caller asks for output."""

    def status(self, message: str) -> None:
        pass

    def progress(self, collected: int, total: int | None) -> None:
        pass

    def warn(self, message: str) -> None:
        pass


def _warn_if_incomplete(reporter: Reporter, reviews: list, metadata: dict) -> None:
    """Say so when fewer reviews came back than the property claims to have."""
    try:
        expected = int(metadata.get("review_count") or 0)
    except ValueError:
        return
    if expected and len(reviews) < expected:
        reporter.warn(
            f"Collected {len(reviews)} of {expected} reviews — booking.com stopped "
            "returning results. Re-run to try again."
        )


async def scrape(
    url: str,
    *,
    mode: str = MODE_FAST,
    headless: bool = False,
    debug: bool = False,
    debug_dir: Path | None = None,
    reporter: Reporter | None = None,
) -> dict:
    """Scrape a property page and return its metadata and reviews.

    In fast mode the review API request is intercepted and replayed directly;
    if that interception fails, or the API's schema has moved, the reviews are
    read from the page instead.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    reporter = reporter or _NullReporter()
    reporter.status("Launching browser")

    async with browser_page(headless) as (page, context):
        reporter.status("Loading property page")
        await open_property_page(page, url)

        reporter.status("Reading property details")
        metadata = await extract_metadata(page)
        reporter.status(f"Property: {metadata.get('name') or 'unknown'}")

        reporter.status("Opening reviews")
        captured = []
        if mode == MODE_FAST:
            async with reviews_api.intercept_review_list(page) as captured:
                found_button = await open_reviews_section(page)
                await wait_until(lambda: bool(captured), CAPTURE_TIMEOUT_S)
        else:
            found_button = await open_reviews_section(page)

        if not found_button:
            reporter.warn(
                "Could not find the 'Read all reviews' button — its selectors may be stale."
            )
        await raise_if_bot_check(page)

        if debug:
            written = await save_debug_snapshot(page, debug_dir or Path("results"))
            reporter.status("Debug snapshot: " + ", ".join(str(path) for path in written))

        reviews = []
        if captured:
            reporter.status("Fetching reviews from the review API")
            cookies = {cookie["name"]: cookie["value"] for cookie in await context.cookies()}
            reviews = await reviews_api.fetch_all_reviews(
                captured[0],
                cookies,
                on_progress=reporter.progress,
                on_error=lambda exc: reporter.warn(f"Review API stopped responding: {exc}"),
                on_drift=lambda field: reporter.warn(
                    f"The review API no longer returns '{field}' — reading the page instead."
                ),
            )
        elif mode == MODE_FAST:
            reporter.warn("Could not intercept the review API — reading the page instead")

        if not reviews:
            reporter.status("Reading reviews from the page")
            reviews = await reviews_dom.paginate_reviews(page, on_progress=reporter.progress)

    if not reviews:
        raise ScrapeError(
            "No reviews found. The property may have none, or booking.com's "
            "page structure changed — re-run with --debug and check the snapshot."
        )
    _warn_if_incomplete(reporter, reviews, metadata)

    return {
        # Stripped here as well as in the report: the query string carries a
        # session id, and library callers write their own output.
        "url": clean_url(url),
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "metadata": metadata,
        "reviews": reviews,
    }
