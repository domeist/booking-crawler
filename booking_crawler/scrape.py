"""Orchestration: drive a browser to a property page and collect its reviews."""

from datetime import datetime
from pathlib import Path

from . import reviews_api, reviews_dom
from .browser import (
    browser_page,
    open_property_page,
    open_reviews_section,
    raise_if_bot_check,
    save_debug_snapshot,
)
from .errors import ScrapeError
from .metadata import extract_metadata

MODE_FAST = "fast"
MODE_STANDARD = "standard"
MODES = (MODE_FAST, MODE_STANDARD)


class _NullReporter:
    """Default reporter, so the library is silent unless a caller asks for output."""

    def status(self, message: str) -> None:
        pass

    def progress(self, collected: int, total: int | None) -> None:
        pass

    def warn(self, message: str) -> None:
        pass


def _warn_if_incomplete(reporter, reviews: list, metadata: dict) -> None:
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
    reporter=None,
) -> dict:
    """Scrape a property page and return its metadata and reviews.

    In fast mode the review API request is intercepted and replayed directly;
    if that interception fails, the DOM click-loop runs instead.
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
        if mode == MODE_FAST:
            async with reviews_api.intercept_review_list(page) as captured:
                await open_reviews_section(page)
        else:
            captured = []
            await open_reviews_section(page)

        await raise_if_bot_check(page)

        if debug:
            snapshot = await save_debug_snapshot(page, debug_dir or Path("results"))
            reporter.status(f"Debug snapshot written to {snapshot}")

        reviews = []
        if captured:
            reporter.status("Fetching reviews from the review API")
            cookies = {cookie["name"]: cookie["value"] for cookie in await context.cookies()}
            reviews = await reviews_api.fetch_all_reviews(
                captured[0],
                cookies,
                on_progress=reporter.progress,
                on_error=lambda exc: reporter.warn(f"Review API stopped responding: {exc}"),
            )
            _warn_if_incomplete(reporter, reviews, metadata)
        elif mode == MODE_FAST:
            reporter.warn("Could not intercept the review API — falling back to the page")

        if not reviews:
            reporter.status("Reading reviews from the page")
            reviews = await reviews_dom.paginate_reviews(page, on_progress=reporter.progress)

    if not reviews:
        raise ScrapeError(
            "No reviews found. The property may have none, or booking.com's "
            "page structure changed — re-run with --debug and check the snapshot."
        )

    return {
        "url": url,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "metadata": metadata,
        "reviews": reviews,
    }
