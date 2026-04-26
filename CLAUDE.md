# booking-crawler

CLI tool that scrapes a booking.com property page and saves a readable text report. Upload the report to Claude.ai for summarisation.

## Usage

```bash
pip install -r requirements.txt
python -m playwright install chromium

# Standard scraper (reliable, click-loop pagination)
python crawl.py "https://www.booking.com/hotel/gb/example.html"
python crawl.py <url> --output my-report.txt   # custom output path
python crawl.py <url> --headless               # headless browser (less visible)

# Fast scraper (intercepts GraphQL, 10-50x faster)
python crawl_fast.py "https://www.booking.com/hotel/gb/example.html"
python crawl_fast.py <url> --output my-report.txt
```

## Output

- `results/<slug>.txt` — human-readable report with all property info and reviews

Upload this file to Claude.ai and ask it to summarise.

## Architecture

### Standard scraper (`scraper.py` + `crawl.py`)
- Playwright browser automation (non-headless by default to avoid bot detection)
- Clicks "Next" in the review section and waits for DOM to update between pages
- Deduplication by (reviewer, date, score) signature prevents infinite loops
- Falls back to `/reviews/` URL offset pagination if the inline section isn't found

### Fast scraper (`scraper_fast.py` + `crawl_fast.py`)
- Same browser startup and metadata extraction as the standard scraper
- Intercepts the `ReviewList` GraphQL POST request (`/dml/graphql`) that fires when
  "Read all reviews" is clicked
- Extracts cookies, headers, and the GraphQL query/variables from the intercepted request
- Replays the request directly with `httpx`, incrementing `skip` by `limit` each time
- Parses `data.reviewListFrontend.reviewCard[]` from each JSON response
- Falls back to the standard click-loop if interception fails

**Key insight**: clicking "Next" in the review section makes no new network request.
Booking.com pre-fetches reviews into an Apollo cache on first load. The fast scraper
therefore intercepts the *initial* ReviewList call (not Next), then replicates it.

## Anti-bot notes

- Uses `playwright-stealth` to mask automation signals
- Randomised delays between interactions
- Non-headless mode is default — booking.com detects headless more aggressively
- If CAPTCHA appears, the script exits with a clear message; solve manually then retry

## Selectors

Booking.com frequently changes its CSS class names. Selectors in `scraper.py` use both
`data-testid` attributes (more stable) and class-name fallbacks. If scraping breaks, inspect
the page and update the selectors in `_extract_metadata` and `_extract_reviews_on_page`.

## GraphQL schema notes (fast scraper)

The `reviewCard` items returned by `ReviewList` have this structure:
```
guestDetails.displayName  -> reviewer name
guestDetails.countryName  -> country
reviewedDate              -> date (int timestamp or string)
reviewScore               -> score (float or dict with .score key)
textDetails.positiveText  -> pros
textDetails.negativeText  -> cons
bookingDetails.tags[]     -> stay info tags
```
