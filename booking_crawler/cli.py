"""Command line entry point."""

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.rule import Rule

from .errors import ScrapeError
from .report import format_report, slug
from .scrape import MODE_FAST, MODE_STANDARD, MODES, scrape

DEFAULT_OUTPUT_DIR = Path("results")


class ConsoleReporter:
    """Prints scrape progress, rewriting the review count in place."""

    def __init__(self, console: Console):
        self._console = console
        self._last_progress = -1

    def status(self, message: str) -> None:
        self._console.print(f"[cyan]{message}[/cyan]")

    def warn(self, message: str) -> None:
        self._console.print(f"[yellow]{message}[/yellow]")

    def progress(self, collected: int, total: int | None) -> None:
        if collected == self._last_progress:
            return
        self._last_progress = collected
        suffix = f" of {total}" if total else ""
        self._console.print(f"  [dim]{collected}{suffix} reviews[/dim]")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="booking-crawler",
        description="Scrape a booking.com property page into a readable text report.",
    )
    parser.add_argument("url", help="Full booking.com property URL")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=f"Output file (default: {DEFAULT_OUTPUT_DIR}/<property-name>.txt)",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=MODE_FAST,
        help=(
            f"'{MODE_FAST}' replays booking.com's review API (default); "
            f"'{MODE_STANDARD}' clicks through the review list in the browser"
        ),
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser headless (faster, but more likely to hit a bot check)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save a screenshot and HTML dump of the review page",
    )
    return parser


def _output_path(explicit: Path | None, property_name: str) -> Path:
    path = explicit or DEFAULT_OUTPUT_DIR / f"{slug(property_name)}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    if "booking.com" not in args.url:
        console.print("[bold red]Error:[/bold red] URL must be a booking.com property page.")
        return 2

    console.print(Rule("[bold]booking-crawler[/bold]"))

    try:
        data = asyncio.run(
            scrape(
                args.url,
                mode=args.mode,
                headless=args.headless,
                debug=args.debug,
                reporter=ConsoleReporter(console),
            )
        )
    except ScrapeError as exc:
        console.print(f"[bold red]Scrape failed:[/bold red] {exc}")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        return 130
    except Exception as exc:  # noqa: BLE001 - a traceback helps nobody here
        console.print(f"[bold red]Unexpected error:[/bold red] {type(exc).__name__}: {exc}")
        console.print("[dim]Re-run with --debug to capture the page for inspection.[/dim]")
        return 1

    output_path = _output_path(args.output, data["metadata"].get("name") or "property")
    output_path.write_text(format_report(data), encoding="utf-8")

    console.print(Rule())
    console.print(f"[bold green]Done.[/bold green] {len(data['reviews'])} reviews scraped.")
    console.print(f"Report saved to [bold]{output_path}[/bold]")
    console.print("\nUpload that file to Claude.ai and ask it to summarise.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
