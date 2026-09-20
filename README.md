# booking-crawler

Scrape every guest review from a [Booking.com](https://www.booking.com) property page into a
single plain-text report — then upload that file to [Claude.ai](https://claude.ai) (or any
other AI) and ask it what the guests actually think.

```
$ python crawl.py "https://www.booking.com/hotel/al/guest-house-shtaka.en-gb.html"
───────────────────────────── booking-crawler ─────────────────────────────
Launching browser
Loading property page
Reading property details
Property: Guest House Shtaka
Opening reviews
Fetching reviews from the review API
  25 of 79 reviews
  50 of 79 reviews
  75 of 79 reviews
  79 of 79 reviews
───────────────────────────────────────────────────────────────────────────
Done. 79 reviews scraped.
Report saved to results/guest-house-shtaka.txt
```

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/domeist/booking-crawler.git
cd booking-crawler

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
```

`--with-deps` installs the system libraries Chromium needs and will ask for `sudo` on Linux.
On macOS and Windows you can drop it: `python -m playwright install chromium`.

## Use

```bash
# Scrape a property (opens a visible browser window)
python crawl.py "https://www.booking.com/hotel/gb/example.html"

# Write somewhere specific
python crawl.py <url> --output reports/example.txt

# Run without a browser window — faster, but more likely to hit a bot check
python crawl.py <url> --headless

# Click through the review list instead of replaying the review API
python crawl.py <url> --mode standard

# Save a screenshot and HTML dump for debugging a failed scrape
python crawl.py <url> --debug

# Show the full traceback instead of a one-line error, for bug reports
python crawl.py <url> --traceback
```

Reports are written to `results/<property-name>.txt`, which is gitignored. A property whose
name has no Latin characters is named after its URL instead, so two of them cannot overwrite
each other.

Installing the package (`pip install .`) also gives you a `booking-crawler` command that takes
the same arguments.

## Output

```
PROPERTY: Guest House Shtaka
URL: https://www.booking.com/hotel/al/guest-house-shtaka.en-gb.html
Scraped: 2026-09-19

Address: Rruga Gole Gushi, 6001 Gjirokastër, Albania
Type: Hotel
Overall score: 9.6 (Exceptional) from 79 reviews

Score breakdown:
  Staff: 9.9
  Facilities: 9.7
  Cleanliness: 9.9
  Comfort: 9.7
  Value for money: 9.8
  Location: 8.9

Amenities: Free parking, Free WiFi, Non-smoking rooms, Family rooms, Bar, Breakfast

Description:
Comfortable Accommodation: Guest House Shtaka in Gjirokastër offers family rooms with
air-conditioning, private bathrooms, and city views. [...]

============================================================
REVIEWS (79 total)
============================================================

--- Review 1 ---
Eric, United States, 2025-10-03
Stay: Solo traveller | Double or Twin Room | 1 night, October 2025
Score: 10
Title: Excellent value and location.
Liked: Spotless clean. Good sized room. Amazing breakfast! Staff was extremely friendly
and helpful. Located 1.5 km from museum and tourist district.
Disliked: Needs handrail on stairs.
Property replied: thank you very much
```

The URL in the report is stripped of its query string, so the session and tracking
identifiers in the link you pasted never end up in a file you share.

## How it works

Both modes start the same way: a stealth-patched Chromium loads the property page, the
cookie banner is dismissed, property details are read from the page's JSON-LD block, and
"Read all reviews" is clicked.

**Fast mode (default).** Clicking "Read all reviews" fires a `ReviewList` GraphQL request.
That request is intercepted, and its URL, headers, cookies and query are replayed directly
with `httpx`, 25 reviews at a time, until the property's review count is reached. No
rendering and no clicking, so a property with 79 reviews finishes in about 25 seconds
where standard mode needs four minutes.

Pages that come back empty or with a 5xx are retried, and if booking.com stops responding
part-way the reviews already collected are still written out, with a warning saying how
many are missing.

Clicking "Next" in the review list makes no network request at all: Booking.com pre-loads
the reviews into an Apollo cache. That is why the *initial* request is the one worth
capturing.

**Standard mode.** Reads review cards straight from the DOM and clicks "Next", waiting for
the list to change between pages. Slower, but it does not depend on the GraphQL schema.
Fast mode falls back to it automatically if interception fails.

## Project layout

```
crawl.py                        CLI entry point
booking_crawler/
    cli.py                      argument parsing, console output
    scrape.py                   orchestration, mode selection, fallback
    browser.py                  browser launch, cookie banner, bot-check detection
    metadata.py                 property details (JSON-LD first, DOM to fill gaps)
    reviews_api.py              fast mode: intercept and replay the review API
    reviews_dom.py              standard mode: parse review cards, click through pages
    report.py                   text report formatting
    models.py                   the review shape and deduplication
tests/                          unit tests; fakes.py stubs the Playwright objects
```

Run the tests with `pip install -e ".[dev]"` then `pytest`. They cover the parsing,
pagination and formatting logic against stubbed pages and a fake review API — no browser and
no network, so the suite finishes in well under a second. GitHub Actions runs it on 3.10,
3.12 and 3.13.

## When it breaks

- **Bot check.** Booking.com detects automation. Running with a visible browser (the
  default) is noticeably more reliable than `--headless`. If a challenge page appears the
  scraper stops and says so — solve it in the window and re-run.
- **Selector drift.** Property details come from the page's JSON-LD block, which is stable.
  Review cards are matched on `data-testid` attributes, which are not: if reviews stop
  coming back, run with `--debug` and compare `results/debug_reviews.html` against the
  selectors in `reviews_dom.py`.
- **GraphQL schema changes.** If a field the reviews depend on disappears from the response,
  fast mode says which one and reads the page instead, rather than reporting a run full of
  blank reviews. The field mapping lives in `reviews_api.parse_review_card`.
- **Short scrapes.** If booking.com stops returning results part-way, the reviews collected
  so far are still written out and the run says how many are missing.

## Legal and ethical use

For personal research on publicly visible review data. Read Booking.com's Terms of Service
before using it. Don't use it for commercial data harvesting or bulk scraping.

Reports contain other people's names, countries and written reviews. That is personal data:
keep the files to yourself, and don't republish them.

## Licence

[MIT](LICENSE)
