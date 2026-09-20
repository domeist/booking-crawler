"""Standard path: read review cards from the DOM, clicking through the pages.

Slower than the API path but independent of the GraphQL schema, so it is also
the fallback when the ReviewList request cannot be intercepted.
"""

import asyncio
import re
from datetime import datetime

from playwright.async_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeout,
)

from .browser import human_delay
from .models import deduplicate, empty_review

# Selectors verified against booking.com on 2026-09-19.
REVIEW_CARD_SELECTOR = '[data-testid="review-card"]'
_CARDS_READY_TIMEOUT_MS = 15_000
_PAGE_CHANGE_TIMEOUT_S = 12.0
MAX_REVIEW_PAGES = 500

_FIELD_SELECTORS = {
    "title": '[data-testid="review-title"]',
    "pros": '[data-testid="review-positive-text"]',
    "cons": '[data-testid="review-negative-text"]',
    "traveller_type": '[data-testid="review-traveler-type"]',
    "room": '[data-testid="review-room-name"]',
    "reply": '[data-testid="review-partner-reply"]',
}

# Labels the card wraps around its text, which should not reach the report.
_REPLY_PREFIX = re.compile(r"^\s*(?:Hotel|Property)\s+response:\s*", re.IGNORECASE)
_EXPANDER_SUFFIX = re.compile(r"\s*(?:\.\.\.|…)?\s*Continue reading\s*$", re.IGNORECASE)

# Clicks the review list's own "Next" control. The button carries no stable
# test id, so it is found by accessible label inside the review containers.
_CLICK_NEXT_JS = """() => {
    const containers = [
        document.querySelector('div[role="dialog"]'),
        document.querySelector('[data-testid="review-list-container"]'),
        document.querySelector('[data-testid="PropertyReviewsRegionBlock"]'),
        document.body,
    ].filter(Boolean);

    for (const container of containers) {
        const button = Array.from(container.querySelectorAll('button')).find((candidate) => {
            const label = (candidate.getAttribute('aria-label') || '').toLowerCase();
            return (label === 'next' || label === 'next page')
                && candidate.offsetParent !== null
                && !candidate.disabled;
        });
        if (button) {
            button.scrollIntoView({block: 'center'});
            button.click();
            return true;
        }
    }
    return false;
}"""


def parse_avatar(text: str) -> tuple[str, str]:
    """Split the avatar block into (reviewer, country)."""
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]
    if not lines:
        return "", ""
    if len(lines) == 1:
        return lines[0], ""
    return lines[0], lines[1]


def parse_review_date(text: str) -> str:
    """Turn "Reviewed: 3 October 2025" into 2025-10-03."""
    cleaned = re.sub(r"^\s*Reviewed:\s*", "", text or "").strip()
    if not cleaned:
        return ""
    for pattern in ("%d %B %Y", "%B %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cleaned, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return cleaned


def parse_score(text: str) -> str:
    """Pull the number out of "Scored 10\\n10"."""
    match = re.search(r"\d+(?:[.,]\d+)?", text or "")
    return match.group(0).replace(",", ".") if match else ""


def parse_stay_info(text: str) -> str:
    """Normalise "1 night · October 2025" into "1 night, October 2025"."""
    parts = [
        part.strip()
        for line in (text or "").replace("\xa0", " ").split("\n")
        for part in line.split("·")
        if part.strip()
    ]
    return ", ".join(dict.fromkeys(parts))


def strip_ui_text(text: str) -> str:
    """Remove the card's own labels from a field's text."""
    cleaned = _EXPANDER_SUFFIX.sub("", text or "").strip()
    return _REPLY_PREFIX.sub("", cleaned).strip()


def compose_stay(stay_info: str, room: str, traveller_type: str) -> str:
    """Build the stay summary, dropping details already reported on their own.

    The stay-info block repeats the room name and traveller type, which are
    also exposed as their own fields.
    """
    already_reported = {room.strip(), traveller_type.strip()} - {""}
    parts = [
        part
        for part in parse_stay_info(stay_info).split(", ")
        if part and part not in already_reported
    ]
    return ", ".join(parts)


async def _card_text(card, selector: str, timeout: int = 1500) -> str:
    try:
        return (await card.locator(selector).first.inner_text(timeout=timeout)).strip()
    except PlaywrightError:
        return ""


async def _parse_card(card) -> dict | None:
    review = empty_review()
    review["reviewer"], review["country"] = parse_avatar(
        await _card_text(card, '[data-testid="review-avatar"]')
    )
    review["date"] = parse_review_date(await _card_text(card, '[data-testid="review-date"]'))
    review["score"] = parse_score(await _card_text(card, '[data-testid="review-score"]'))

    for field, selector in _FIELD_SELECTORS.items():
        text = re.sub(r"\s+", " ", await _card_text(card, selector))
        review[field] = strip_ui_text(text)

    review["stay"] = compose_stay(
        await _card_text(card, '[data-testid="review-stay-info"]'),
        review["room"],
        review["traveller_type"],
    )

    if not (review["pros"] or review["cons"] or review["score"]):
        return None
    return review


async def extract_cards_on_page(page: Page) -> list[dict]:
    """Parse every review card currently rendered."""
    cards = await page.locator(REVIEW_CARD_SELECTOR).all()
    reviews = []
    for card in cards:
        review = await _parse_card(card)
        if review:
            reviews.append(review)
    return reviews


async def _wait_for_new_page(page: Page, previous_first_card: str) -> None:
    """Poll until the first card changes, meaning the next page has rendered."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _PAGE_CHANGE_TIMEOUT_S
    while loop.time() < deadline:
        await asyncio.sleep(0.4)
        try:
            current = await page.locator(REVIEW_CARD_SELECTOR).first.inner_text(timeout=1000)
        except PlaywrightError:
            return
        if current != previous_first_card:
            return


async def paginate_reviews(page: Page, *, on_progress=None) -> list[dict]:
    """Walk the review list page by page, collecting every review.

    The review list renders asynchronously after the section is opened, so
    this waits for the first card rather than assuming it is already there.
    """
    try:
        await page.wait_for_selector(REVIEW_CARD_SELECTOR, timeout=_CARDS_READY_TIMEOUT_MS)
    except PlaywrightTimeout:
        return []
    await human_delay(0.5, 1.0)

    reviews: list[dict] = []
    seen: set[str] = set()

    for _ in range(MAX_REVIEW_PAGES):
        if on_progress:
            on_progress(len(reviews), None)

        fresh = deduplicate(await extract_cards_on_page(page), seen)
        if not fresh:
            break
        reviews.extend(fresh)

        try:
            first_card = await page.locator(REVIEW_CARD_SELECTOR).first.inner_text(timeout=2000)
        except PlaywrightError:
            first_card = ""

        if not await page.evaluate(_CLICK_NEXT_JS):
            break
        if first_card:
            await _wait_for_new_page(page, first_card)
        else:
            await human_delay(2.0, 3.0)

    if on_progress:
        on_progress(len(reviews), None)
    return reviews
