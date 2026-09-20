"""Turn scraped data into the plain-text report users upload to an AI."""

import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

SEPARATOR = "=" * 60


def clean_url(url: str) -> str:
    """Drop the query string from a booking.com URL.

    Booking.com URLs carry a `sid` session identifier and affiliate tracking
    parameters. The report is meant to be shared, so they must not travel
    with it.
    """
    parts = urlsplit(url or "")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def slug(name: str) -> str:
    """Convert a property name into a filesystem-safe slug."""
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_name = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "property"


def _metadata_lines(metadata: dict) -> list[str]:
    lines = []
    if address := metadata.get("address"):
        lines.append(f"Address: {address}")
    if property_type := metadata.get("property_type"):
        lines.append(f"Type: {property_type}")

    if score := metadata.get("overall_score"):
        label = f" ({metadata['score_label']})" if metadata.get("score_label") else ""
        count = f" from {metadata['review_count']} reviews" if metadata.get("review_count") else ""
        lines.append(f"Overall score: {score}{label}{count}")

    if categories := metadata.get("category_scores"):
        lines.append("")
        lines.append("Score breakdown:")
        lines.extend(f"  {category}: {value}" for category, value in categories.items())

    if amenities := metadata.get("amenities"):
        lines.append("")
        lines.append(f"Amenities: {', '.join(amenities)}")

    if description := metadata.get("description"):
        lines.append("")
        lines.append("Description:")
        lines.append(description)

    return lines


def _review_lines(review: dict, number: int) -> list[str]:
    lines = ["", f"--- Review {number} ---"]

    byline = [review.get(field) for field in ("reviewer", "country", "date")]
    if byline := [part for part in byline if part]:
        lines.append(", ".join(byline))

    context = [review.get(field) for field in ("traveller_type", "room", "stay")]
    if context := [part for part in context if part]:
        lines.append(f"Stay: {' | '.join(context)}")

    if score := review.get("score"):
        lines.append(f"Score: {score}")
    if title := review.get("title"):
        lines.append(f"Title: {title}")
    if pros := review.get("pros"):
        lines.append(f"Liked: {pros}")
    if cons := review.get("cons"):
        lines.append(f"Disliked: {cons}")
    if reply := review.get("reply"):
        lines.append(f"Property replied: {reply}")

    return lines


def format_report(data: dict) -> str:
    """Render scraped data as the human-readable report."""
    metadata = data.get("metadata") or {}
    reviews = data.get("reviews") or []

    lines = [
        f"PROPERTY: {metadata.get('name') or 'Unknown'}",
        f"URL: {clean_url(data.get('url', ''))}",
        f"Scraped: {str(data.get('scraped_at', ''))[:10]}",
        "",
    ]
    lines.extend(_metadata_lines(metadata))
    lines.extend(["", SEPARATOR, f"REVIEWS ({len(reviews)} total)", SEPARATOR])

    for number, review in enumerate(reviews, start=1):
        lines.extend(_review_lines(review, number))

    return "\n".join(lines) + "\n"
