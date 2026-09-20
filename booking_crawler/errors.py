class ScrapeError(RuntimeError):
    """Raised when a scrape cannot continue (bot check, blocked page, no reviews)."""
