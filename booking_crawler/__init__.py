"""Scrape booking.com property pages into readable text reports."""

from .errors import ScrapeError
from .scrape import scrape

__all__ = ["ScrapeError", "scrape"]
__version__ = "1.0.0"
