"""Fast path: intercept booking.com's review API, then replay it directly.

Clicking "Next" in the review list makes no network request — booking.com
pre-loads reviews into an Apollo cache. The request worth capturing is the
*initial* ReviewList call fired when the review section opens. It carries a
session-bound challenge token that cannot be forged, so the browser is still
needed for the first page load; after that, httpx does the paging.
"""

import asyncio
import json
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from playwright.async_api import Error as PlaywrightError

from .models import deduplicate, empty_review

GRAPHQL_OPERATION = "ReviewList"
GRAPHQL_ROUTE_PATTERN = "**/graphql**"
REQUEST_TIMEOUT_S = 20.0
MAX_API_PAGES = 2_000

# The page asks for 10 reviews at a time; the API serves 25 just as happily,
# which cuts the number of requests a large property needs by more than half.
# `skip` still advances by the number of cards actually returned, so a server
# that clamps the limit costs extra requests rather than losing reviews.
PAGE_SIZE = 25

# An occasional page comes back empty or 502s even though more reviews exist.
# Retrying clears it — treating it as the end silently truncates the report.
PAGE_ATTEMPTS = 3
RETRY_BACKOFF_S = 1.5

# Rebuilt per request by httpx, or meaningless outside the browser.
_DROPPED_HEADERS = frozenset({"host", "content-length", "accept-encoding", "cookie"})

# The response fields parse_review_card maps. If a whole page lacks one, the
# schema has moved and the reviews would parse into blanks.
_REQUIRED_CARD_FIELDS = ("textDetails", "guestDetails", "reviewUrl", "reviewScore")


@dataclass
class ReviewListRequest:
    """The intercepted GraphQL call, everything needed to replay it."""

    url: str
    body: dict
    headers: dict = field(default_factory=dict)

    @property
    def input_variables(self) -> dict:
        return self.body.get("variables", {}).get("input", {})

    @property
    def page_size(self) -> int:
        return int(self.input_variables.get("limit") or 10)

    def body_for_page(self, skip: int, limit: int | None = None) -> dict:
        variables = dict(self.body.get("variables", {}))
        variables["input"] = {
            **self.input_variables,
            "skip": skip,
            "limit": limit or self.page_size,
        }
        return {**self.body, "variables": variables}

    def replay_headers(self) -> dict:
        """The captured headers, minus the ones httpx must set itself."""
        return {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in _DROPPED_HEADERS and not name.startswith(":")
        }


@asynccontextmanager
async def intercept_review_list(page):
    """Capture the ReviewList GraphQL request made while inside this block."""
    captured: list[ReviewListRequest] = []

    async def handle(route):
        request = route.request
        if not captured and "graphql" in request.url.lower():
            try:
                body = json.loads(request.post_data or "")
                if body.get("operationName") == GRAPHQL_OPERATION:
                    captured.append(
                        ReviewListRequest(
                            url=request.url, body=body, headers=dict(request.headers)
                        )
                    )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        await route.continue_()

    await page.route(GRAPHQL_ROUTE_PATTERN, handle)
    try:
        yield captured
    finally:
        try:
            await page.unroute(GRAPHQL_ROUTE_PATTERN, handle)
        except PlaywrightError:  # the page may already be closing
            pass


def format_review_date(raw) -> str:
    """Render a review date as YYYY-MM-DD.

    The API returns a Unix timestamp, which reads as a meaningless integer if
    passed through untouched; older responses used an ISO string.
    """
    if raw in (None, ""):
        return ""
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    return str(raw)[:10]


def format_score(raw) -> str:
    """Render a score without a trailing .0, so 10.0 prints as 10.

    Older responses wrapped the score in an object, so that shape is unwrapped
    rather than stringified — a repr in the report would be worse than a blank.
    """
    if isinstance(raw, dict):
        raw = raw.get("score", "")
    if raw in (None, ""):
        return ""
    try:
        value = float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return ""
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _format_stay(booking: dict) -> str:
    """Summarise the stay as "2 nights, October 2025"."""
    parts = []
    try:
        nights = int(booking.get("numNights") or 0)
    except (TypeError, ValueError):
        nights = 0
    if nights:
        parts.append(f"{nights} night" if nights == 1 else f"{nights} nights")

    stay_date = booking.get("checkinDate") or booking.get("checkoutDate") or ""
    try:
        parts.append(datetime.strptime(str(stay_date)[:10], "%Y-%m-%d").strftime("%B %Y"))
    except ValueError:
        pass
    return ", ".join(parts)


def parse_review_card(card: dict) -> dict | None:
    """Map one GraphQL reviewCard onto our review shape, or None if empty."""
    if not isinstance(card, dict):
        return None

    guest = card.get("guestDetails") or {}
    booking = card.get("bookingDetails") or {}
    text = card.get("textDetails") or {}
    reply = card.get("partnerReply") or {}
    room = booking.get("roomType") or {}

    review = empty_review()
    # reviewUrl is booking.com's own per-review identifier, not a link.
    review["review_id"] = str(card.get("reviewUrl") or "")
    review["reviewer"] = guest.get("username") or ("Anonymous" if guest.get("anonymous") else "")
    review["country"] = guest.get("countryName") or guest.get("countryCode") or ""
    review["date"] = format_review_date(card.get("reviewedDate"))
    review["title"] = (text.get("title") or "").strip()
    review["score"] = format_score(card.get("reviewScore"))
    review["traveller_type"] = guest.get("guestTypeTranslation") or ""
    review["room"] = (room.get("name") or "").strip()
    review["stay"] = _format_stay(booking)
    review["pros"] = (text.get("positiveText") or "").strip()
    review["cons"] = (text.get("negativeText") or "").strip()
    review["reply"] = (reply.get("reply") or "").strip()

    if not (review["pros"] or review["cons"] or review["score"]):
        return None
    return review


def detect_schema_drift(cards: list[dict]) -> str | None:
    """Name the response field that has moved, if the schema no longer matches.

    Without this a renamed field parses into blank reviews and the run still
    reports success — reviews with no text also dedupe into a handful, because
    only the API's own id distinguishes them.
    """
    if not cards:
        return None
    for name in _REQUIRED_CARD_FIELDS:
        if not any(isinstance(card, dict) and card.get(name) for card in cards):
            return name
    return None


def _cards_from_response(payload: dict) -> tuple[list[dict], int | None]:
    """Return (raw review cards, total review count) from one API response."""
    reviews = (payload.get("data") or {}).get("reviewListFrontend") or {}
    cards = reviews.get("reviewCard") or []
    total = reviews.get("reviewsCount")
    return (cards if isinstance(cards, list) else []), (total if isinstance(total, int) else None)


async def _fetch_page(
    client, request: ReviewListRequest, skip: int, page_size: int, *, retry_empty: bool
):
    """Fetch one page, retrying transient failures.

    Returns (cards, total, error). A failure is returned rather than raised so
    that an error near the end of a long scrape keeps the reviews already
    collected instead of discarding them. `retry_empty` is False once the list
    is known to be exhausted, so the page past the end is asked for once.
    """
    error = None
    for attempt in range(PAGE_ATTEMPTS):
        try:
            response = await client.post(
                request.url, json=request.body_for_page(skip, page_size)
            )
            response.raise_for_status()
            cards, total = _cards_from_response(response.json())
            if cards or not retry_empty:
                return cards, total, None
            error = None
        except (httpx.HTTPError, ValueError) as exc:
            error = exc

        if attempt < PAGE_ATTEMPTS - 1:
            await asyncio.sleep(RETRY_BACKOFF_S * (attempt + 1))

    return [], None, error


async def fetch_all_reviews(
    request: ReviewListRequest,
    cookies: dict,
    *,
    on_progress=None,
    on_error=None,
    on_drift=None,
) -> list[dict]:
    """Page through the review API until it stops returning new reviews.

    Returns an empty list when the response schema has drifted, so the caller
    can fall back to reading the page instead of reporting blank reviews.
    """
    reviews: list[dict] = []
    seen: set[str] = set()
    page_size = max(request.page_size, PAGE_SIZE)
    total = None
    skip = 0
    # A review added mid-scrape shifts the pages, so one page of pure
    # duplicates is not the end of the list — two in a row is.
    duplicate_pages = 0
    last_page_was_full = True

    async with httpx.AsyncClient(
        cookies=cookies,
        headers=request.replay_headers(),
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT_S,
    ) as client:
        for page_number in range(MAX_API_PAGES):
            if on_progress:
                on_progress(len(reviews), total)

            # A short page means the list ran out, so an empty page after it is
            # the end rather than a hiccup worth retrying. Without this the
            # final request is made three times, 4.5s of backoff, on every run
            # where the API does not report a total.
            cards, reported_total, error = await _fetch_page(
                client, request, skip, page_size, retry_empty=last_page_was_full
            )
            if error is not None:
                if on_error:
                    on_error(error)
                break

            if page_number == 0:
                drifted = detect_schema_drift(cards)
                if drifted:
                    if on_drift:
                        on_drift(drifted)
                    return []

            if total is None:
                total = reported_total

            parsed = [review for review in map(parse_review_card, cards) if review]
            fresh = deduplicate(parsed, seen)
            reviews.extend(fresh)

            # Advance by what the server actually sent, not by what was asked
            # for: a clamped limit then costs extra requests, not reviews.
            skip += len(cards)
            last_page_was_full = len(cards) >= page_size

            duplicate_pages = 0 if fresh else duplicate_pages + 1
            if not cards or duplicate_pages >= 2:
                break
            if total is not None and len(reviews) >= total:
                break
            await asyncio.sleep(random.uniform(0.3, 0.6))

    if on_progress:
        on_progress(len(reviews), total)
    return reviews
