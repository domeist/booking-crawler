#!/usr/bin/env python3
import argparse
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console
from rich.rule import Rule

from report import format_report, slug
from scraper import scrape

console = Console()


def main():
    parser = argparse.ArgumentParser(
        description="Scrape a booking.com property and save a readable report."
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
        help="Run browser in headless mode (less visible, may trigger bot detection)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save a screenshot and HTML dump of the reviews page to results/debug_*",
    )
    args = parser.parse_args()

    if "booking.com" not in args.url:
        console.print("[bold red]Error:[/bold red] URL must be a booking.com property page.")
        sys.exit(1)

    console.print(Rule("[bold]Booking.com Crawler[/bold]"))

    try:
        data = asyncio.run(scrape(args.url, headless=args.headless, debug=args.debug))
    except RuntimeError as e:
        console.print(f"[bold red]Scrape failed:[/bold red] {e}")
        sys.exit(1)

    name = data["metadata"].get("name") or "property"
    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)

    out_path = Path(args.output) if args.output else results_dir / f"{slug(name)}.txt"
    out_path.write_text(format_report(data), encoding="utf-8")

    console.print(Rule())
    console.print(f"[bold green]Done![/bold green] {len(data['reviews'])} reviews scraped.")
    console.print(f"[bold green]Report saved ->[/bold green] {out_path}")
    console.print("\nUpload that file to Claude.ai and ask it to summarise.")


if __name__ == "__main__":
    main()
