import re


def slug(name: str) -> str:
    """Convert a property name to a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "property"


def format_report(data: dict) -> str:
    """Format scraped data as a human-readable text report."""
    m = data["metadata"]
    reviews = data["reviews"]
    lines = []

    lines.append(f"PROPERTY: {m.get('name', 'Unknown')}")
    lines.append(f"URL: {data.get('url', '')}")
    lines.append(f"Scraped: {data.get('scraped_at', '')[:10]}")
    lines.append("")

    if m.get("address"):
        lines.append(f"Address: {m['address']}")
    if m.get("property_type"):
        lines.append(f"Type: {m['property_type']}")
    if m.get("overall_score"):
        label = f" ({m['score_label']})" if m.get("score_label") else ""
        lines.append(f"Overall score: {m['overall_score']}{label}")

    if m.get("category_scores"):
        lines.append("\nScore breakdown:")
        for cat, score in m["category_scores"].items():
            lines.append(f"  {cat}: {score}")

    if m.get("amenities"):
        lines.append(f"\nAmenities: {', '.join(m['amenities'])}")

    if m.get("description"):
        lines.append(f"\nDescription:\n{m['description']}")

    lines.append(f"\n{'='*60}")
    lines.append(f"REVIEWS ({len(reviews)} total)")
    lines.append(f"{'='*60}")

    for i, r in enumerate(reviews, 1):
        lines.append(f"\n--- Review {i} ---")
        parts = [p for p in [r.get("reviewer"), r.get("country"), r.get("date")] if p]
        if parts:
            lines.append(", ".join(parts))
        if r.get("info_tags"):
            lines.append(f"Tags: {' | '.join(r['info_tags'])}")
        if r.get("score"):
            lines.append(f"Score: {r['score']}")
        if r.get("pros"):
            lines.append(f"Liked: {r['pros']}")
        if r.get("cons"):
            lines.append(f"Disliked: {r['cons']}")

    return "\n".join(lines)
