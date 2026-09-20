"""Property metadata extraction.

Booking.com embeds a JSON-LD block with the property's name, address, type
and rating. That block is far more stable than the page's CSS classes, so it
is the primary source here and the DOM is only used to fill the gaps.
"""

import json
import re

from playwright.async_api import Page

_JSON_LD_SELECTOR = 'script[type="application/ld+json"]'
_DESCRIPTION_SELECTOR = '[data-testid="property-description"]'
_ADDRESS_SELECTOR = '[data-testid="PropertyHeaderAddressDesktop-wrapper"]'
_SCORE_SELECTOR = '[data-testid="review-score-component"]'
_SUBSCORE_SELECTOR = '[data-testid="review-subscore"]'
_AMENITIES_SELECTOR = (
    '[data-testid="property-most-popular-facilities-wrapper"] li, '
    '[data-testid="facility-list-item"]'
)

# "Guest House Shtaka (Hotel) (Albania) deals" -> "Guest House Shtaka"
_H1_SUFFIX = re.compile(r"\s*\(([^()]+)\)\s*\(([^()]+)\)\s*deals\s*$", re.IGNORECASE)


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
        if label and not result["score_label"] and not re.search(r"\d", label):
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


def metadata_from_json_ld(payload: dict) -> dict:
    """Pull the fields we care about out of a JSON-LD property block."""
    data = {}
    if name := payload.get("name"):
        data["name"] = str(name).strip()
    if property_type := payload.get("@type"):
        data["property_type"] = str(property_type).strip()
    if description := payload.get("description"):
        data["description"] = str(description).strip()

    address = payload.get("address")
    if isinstance(address, dict):
        street = address.get("streetAddress") or ""
        if not street:
            parts = [
                address.get(key, "")
                for key in ("addressLocality", "postalCode", "addressRegion", "addressCountry")
            ]
            street = ", ".join(part for part in parts if part)
        if street:
            data["address"] = street.strip()

    rating = payload.get("aggregateRating")
    if isinstance(rating, dict):
        if value := rating.get("ratingValue"):
            data["overall_score"] = str(value)
        if count := rating.get("reviewCount"):
            data["review_count"] = str(count)

    return data


async def _read_json_ld(page: Page) -> dict:
    """Return the first JSON-LD block that describes the property."""
    try:
        blocks = await page.locator(_JSON_LD_SELECTOR).all()
    except Exception:
        return {}

    for block in blocks:
        try:
            payload = json.loads(await block.inner_text(timeout=2000))
        except Exception:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("name"):
                return metadata_from_json_ld(candidate)
    return {}


async def _text(page: Page, selector: str, timeout: int = 3000) -> str:
    try:
        return (await page.locator(selector).first.inner_text(timeout=timeout)).strip()
    except Exception:
        return ""


async def _category_scores(page: Page) -> dict:
    """Read the per-category score breakdown (Staff, Cleanliness, ...)."""
    scores = {}
    try:
        blocks = await page.locator(_SUBSCORE_SELECTOR).all()
    except Exception:
        return scores

    for block in blocks:
        try:
            parsed = parse_subscore(await block.inner_text(timeout=2000))
        except Exception:
            continue
        if parsed:
            scores[parsed[0]] = parsed[1]
    return scores


async def _amenities(page: Page) -> list[str]:
    amenities = []
    try:
        items = await page.locator(_AMENITIES_SELECTOR).all()
    except Exception:
        return amenities

    for item in items:
        try:
            text = (await item.inner_text(timeout=1000)).strip()
        except Exception:
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
