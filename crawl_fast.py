#!/usr/bin/env python3
import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console
from rich.rule import Rule

console = Console()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "property"


def _format_report(data: dict) -> str:
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


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Fast booking.com scraper — intercepts the review API after one click "
            "and fetches all remaining pages directly (10-50x faster than the standard crawler)."
        )
    )
    parser.add_argument("url", help="Full booking.com property URL")
    parser.add_argument(
        "--output", "-o",
        help="Path for the output file (default: results/<name>.txt)",
        default=None,
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save a screenshot and HTML dump to results/debug_*",
    )
    args = parser.parse_args()

    if "booking.com" not in args.url:
        console.print("[bold red]Error:[/bold red] URL must be a booking.com property page.")
        sys.exit(1)

    console.print(Rule("[bold]Booking.com Fast Crawler[/bold]"))

    from scraper_fast import scrape_fast
    try:
        data = asyncio.run(scrape_fast(args.url, headless=args.headless, debug=args.debug))
    except RuntimeError as e:
        console.print(f"[bold red]Scrape failed:[/bold red] {e}")
        sys.exit(1)

    name = data["metadata"].get("name") or "property"
    slug = _slug(name)

    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)

    report = _format_report(data)

    out_path = Path(args.output) if args.output else results_dir / f"{slug}.txt"
    out_path.write_text(report, encoding="utf-8")

    console.print(Rule())
    console.print(f"[bold green]OK Done![/bold green] {len(data['reviews'])} reviews scraped.")
    console.print(f"[bold green]OK Report saved ->[/bold green] {out_path}")
    console.print("\nUpload that file to Claude.ai and ask it to summarise.")


if __name__ == "__main__":
    main()
