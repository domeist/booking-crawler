"""Property metadata extraction.

Booking.com embeds a JSON-LD block with the property's name, address, type
and rating. That block is far more stable than the page's CSS classes, so it
is the primary source here and the DOM is only used to fill the gaps.
"""

import json
import re

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

# Selectors verified against booking.com on 2026-09-19.
_JSON_LD_SELECTOR = 'script[type="application/ld+json"]'
_DESCRIPTION_SELECTOR = '[data-testid="property-description"]'
_ADDRESS_SELECTOR = '[data-testid="PropertyHeaderAddressDesktop-wrapper"]'
_SCORE_SELECTOR = '[data-testid="review-score-component"]'
_SUBSCORE_SELECTOR = '[data-testid="review-subscore"]'
_AMENITIES_SELECTOR = (
    '[data-testid="property-most-popular-facilities-wrapper"] li, '
    '[data-testid="facility-list-item"]'
)

# "Guest House Shtaka (Hotel) (Albania) deals" -> "Guest House Shtaka".
# The country half is optional; some headings carry only the property type.
_H1_SUFFIX = re.compile(
    r"\s*\(([^()]+)\)(?:\s*\(([^()]+)\))?\s*deals\s*$", re.IGNORECASE
)

# A property page carries several JSON-LD blocks (Organization, WebSite,
# BreadcrumbList). Picking the wrong one titles the report "Booking.com".
_LODGING_TYPES = frozenset(
    {
        "hotel",
        "lodgingbusiness",
        "bedandbreakfast",
        "apartment",
        "aparthotel",
        "hostel",
        "motel",
        "resort",
        "campground",
        "guesthouse",
        "vacationrental",
        "house",
    }
)

# Lines the score widget renders for screen readers, never a score label.
_SCORE_LABEL_NOISE = re.compile(r"^(?:scored|rated)\b", re.IGNORECASE)


def clean_property_name(heading: str) -> tuple[str, str]:
    """Split a page <h1> into (name, property type).

    The heading is decorated for SEO — "Name (Hotel) (Albania) deals" — and
    that decoration used to end up in report titles and output filenames.
    """
    heading = (heading or "").strip()
    match = _H1_SUFFIX.search(heading)
    if not match:
        return heading, ""
    return heading[: match.start()].strip(), match.group(1).strip()


def parse_score_block(text: str) -> dict:
    """Parse the review score widget: "9.6\\nExceptional · 79 reviews"."""
    result = {"overall_score": "", "score_label": "", "review_count": ""}
    if not text:
        return result

    lines = [line.strip() for line in text.replace("\xa0", " ").split("\n") if line.strip()]
    if not lines:
        return result

    score = re.search(r"\d+(?:[.,]\d+)?", lines[0])
    if score:
        result["overall_score"] = score.group(0).replace(",", ".")

    for line in lines[1:]:
        count = re.search(r"([\d,\s]+)\s+reviews?", line)
        if count and not result["review_count"]:
            result["review_count"] = re.sub(r"[^\d]", "", count.group(1))
        label = line.split("·")[0].strip()
        if (
            label
            and not result["score_label"]
            and not re.search(r"\d", label)
            and not _SCORE_LABEL_NOISE.match(label)
        ):
            result["score_label"] = label

    return result


def parse_subscore(text: str) -> tuple[str, str] | None:
    """Parse one category score block into (category, score).

    Each block renders as three lines: an accessibility label, the category
    name, then the score. Reading the last two lines skips the label.
    """
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]
    if len(lines) < 2:
        return None
    category, score = lines[-2], lines[-1]
    if not re.fullmatch(r"\d+(?:[.,]\d+)?", score):
        return None
    return category, score.replace(",", ".")


def json_ld_types(payload: dict) -> list[str]:
    """The block's @type values. Schema.org allows a single type or a list."""
    raw = payload.get("@type") or []
    values = raw if isinstance(raw, list) else [raw]
    return [str(value).strip() for value in values if str(value).strip()]


def json_ld_address(payload: dict) -> str:
    """The block's address, which may be a PostalAddress object or plain text."""
    address = payload.get("address")
    if isinstance(address, str):
        return address.strip()
    if not isinstance(address, dict):
        return ""

    street = address.get("streetAddress") or ""
    if not street:
        parts = [
            address.get(key, "")
            for key in ("addressLocality", "postalCode", "addressRegion", "addressCountry")
        ]
        street = ", ".join(part for part in parts if part)
    return street.strip()


def metadata_from_json_ld(payload: dict) -> dict:
    """Pull the fields we care about out of a JSON-LD property block."""
    data = {}
    if name := payload.get("name"):
        data["name"] = str(name).strip()
    if description := payload.get("description"):
        data["description"] = str(description).strip()

    types = json_ld_types(payload)
    if types:
        # Report the lodging type when the block lists several.
        lodging = [value for value in types if value.lower() in _LODGING_TYPES]
        data["property_type"] = (lodging or types)[0]

    if address := json_ld_address(payload):
        data["address"] = address

    rating = payload.get("aggregateRating")
    if isinstance(rating, dict):
        if value := rating.get("ratingValue"):
            data["overall_score"] = str(value)
        if count := rating.get("reviewCount"):
            data["review_count"] = str(count)

    return data


# A block must look like the property itself, not merely carry a name: it
# needs a lodging @type or a guest rating. An address alone is not enough —
# Booking.com's own Organization block has one, and picking it would title
# every report "Booking.com".
MIN_JSON_LD_SCORE = 3


def json_ld_score(payload: dict) -> int:
    """Rank a JSON-LD block by how much it looks like the property itself."""
    if not isinstance(payload, dict) or not payload.get("name"):
        return 0
    score = 1
    if any(value.lower() in _LODGING_TYPES for value in json_ld_types(payload)):
        score += 4
    if isinstance(payload.get("aggregateRating"), dict):
        score += 2
    if json_ld_address(payload):
        score += 1
    return score


async def _read_json_ld(page: Page) -> dict:
    """Return the JSON-LD block that best describes the property."""
    try:
        blocks = await page.locator(_JSON_LD_SELECTOR).all()
    except PlaywrightError:
        return {}

    best, best_score = None, MIN_JSON_LD_SCORE - 1
    for block in blocks:
        try:
            payload = json.loads(await block.inner_text(timeout=2000))
        except (PlaywrightError, json.JSONDecodeError):
            continue
        for candidate in payload if isinstance(payload, list) else [payload]:
            score = json_ld_score(candidate)
            if score > best_score:
                best, best_score = candidate, score

    return metadata_from_json_ld(best) if best else {}


async def _text(page: Page, selector: str, timeout: int = 3000) -> str:
    try:
        return (await page.locator(selector).first.inner_text(timeout=timeout)).strip()
    except PlaywrightError:
        return ""


async def _category_scores(page: Page) -> dict:
    """Read the per-category score breakdown (Staff, Cleanliness, ...)."""
    scores = {}
    try:
        blocks = await page.locator(_SUBSCORE_SELECTOR).all()
    except PlaywrightError:
        return scores

    for block in blocks:
        try:
            parsed = parse_subscore(await block.inner_text(timeout=2000))
        except PlaywrightError:
            continue
        if parsed:
            scores[parsed[0]] = parsed[1]
    return scores


async def _amenities(page: Page) -> list[str]:
    amenities = []
    try:
        items = await page.locator(_AMENITIES_SELECTOR).all()
    except PlaywrightError:
        return amenities

    for item in items:
        try:
            text = (await item.inner_text(timeout=1000)).strip()
        except PlaywrightError:
            continue
        if text:
            amenities.append(text)
    return list(dict.fromkeys(amenities))


async def extract_metadata(page: Page) -> dict:
    """Collect everything we report about the property itself."""
    data = {
        "name": "",
        "property_type": "",
        "address": "",
        "description": "",
        "overall_score": "",
        "score_label": "",
        "review_count": "",
        "category_scores": {},
        "amenities": [],
    }
    data.update(await _read_json_ld(page))

    if not data["name"] or not data["property_type"]:
        name, property_type = clean_property_name(await _text(page, "h1"))
        data["name"] = data["name"] or name
        data["property_type"] = data["property_type"] or property_type

    if not data["address"]:
        address = await _text(page, _ADDRESS_SELECTOR)
        data["address"] = address.split("\n")[0].strip()

    # The on-page description is fuller than the JSON-LD one, which is truncated.
    if description := await _text(page, _DESCRIPTION_SELECTOR):
        data["description"] = description

    score_block = parse_score_block(await _text(page, _SCORE_SELECTOR))
    for key, value in score_block.items():
        if value:
            data[key] = value

    data["category_scores"] = await _category_scores(page)
    data["amenities"] = await _amenities(page)
    return data
