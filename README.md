# Booking Crawler

[![CI](https://github.com/domeist/booking-crawler/actions/workflows/ci.yml/badge.svg)](https://github.com/domeist/booking-crawler/actions/workflows/ci.yml)

A command-line tool that scrapes every guest review from a **Booking.com** property page into
a single plain-text report — then you upload that file to **[Claude.ai](https://claude.ai)**
(or any other AI) and ask it what the guests actually think.

A property with 1,700 reviews takes a few minutes and produces one file you can read or paste
anywhere.

## What it does

- Collects every review on a property, not just the first page
- Pulls them from Booking.com's own review API, 25 at a time, rather than clicking through the page
- Falls back to reading the page when the API cannot be reached or its schema has moved
- Records each review's score, title, text, dates, room, stay length, traveller type and the property's reply
- Reads the property's name, address, type, category scores, amenities and description from the page's structured data
- Strips the session and tracking identifiers out of the URL it writes, so the report is safe to share
- Says how many reviews are missing if Booking.com stops responding part-way, rather than quietly truncating

## Quick start

```bash
git clone https://github.com/domeist/booking-crawler.git
cd booking-crawler

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install --with-deps chromium
```

**Requires Python 3.10+.**

The last step is separate from `pip` on purpose: it downloads a Chromium build and the system
libraries it needs, which is why `--with-deps` asks for `sudo` on Linux. On macOS and Windows,
drop it — `playwright install chromium`. If you skip the step entirely, the first run tells you
exactly which command to run.

Then point it at any property page:

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

A browser window opens while it works — that is deliberate, and more reliable than running
hidden. Installing the package (`pip install .`) also gives you a `booking-crawler` command
taking the same arguments.

## Options

| Flag | What it does |
|---|---|
| `-o`, `--output PATH` | Where to write the report. Default: `results/<property-name>.txt` |
| `--mode standard` | Click through the review list instead of replaying the review API |
| `--headless` | Run without a browser window — quicker, but more likely to hit a bot check |
| `--debug` | Save a screenshot and HTML dump of the review page |
| `--traceback` | Show the full traceback instead of a one-line error, for bug reports |
| `--version` | Print the version and exit |

Reports land in `results/`, which is gitignored. A property whose name has no Latin characters
is named after its URL instead, so two of them cannot overwrite each other.

## The report

```
PROPERTY: Guest House Shtaka
URL: https://www.booking.com/hotel/al/guest-house-shtaka.en-gb.html
Scraped: 2026-09-20

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

The URL is written without its query string, so the session and tracking identifiers in the
link you pasted never end up in a file you share.

## How it works

Both modes start the same way: a stealth-patched Chromium loads the property page, the cookie
banner is dismissed, property details are read from the page's JSON-LD block, and "Read all
reviews" is clicked.

**Fast mode (default).** That click fires a `ReviewList` GraphQL request. It is intercepted,
and its URL, headers, cookies and query are replayed directly with `httpx`, 25 reviews per
request, until the property's review count is reached. No rendering and no clicking, so a
property with 79 reviews finishes in about 25 seconds where standard mode needs four minutes.

Pages that come back empty or with a 5xx are retried, and if Booking.com stops responding
part-way the reviews already collected are still written out, with a warning saying how many
are missing.

Clicking "Next" in the review list makes no network request at all: Booking.com pre-loads the
reviews into an Apollo cache. That is why the *initial* request is the one worth capturing.

**Standard mode.** Reads review cards straight from the DOM and clicks "Next", waiting for the
list to change between pages. Slower, but it does not depend on the GraphQL schema. Fast mode
falls back to it on its own if interception fails.

## Project layout

```
crawl.py                        CLI entry point
booking_crawler/
    cli.py                      argument parsing, console output
    scraper.py                  orchestration, mode selection, fallback
    errors.py                   the one exception type the CLI catches
    browser.py                  browser launch, cookie banner, bot-check detection
    metadata.py                 property details (JSON-LD first, DOM to fill gaps)
    reviews_api.py              fast mode: intercept and replay the review API
    reviews_dom.py              standard mode: parse review cards, click through pages
    report.py                   text report formatting
    models.py                   the review shape and deduplication
tests/                          unit tests; fakes.py stubs the Playwright objects
```

## When it breaks

- **English URLs.** Property pages are opened in `en-GB`, and dates and property types are
  parsed as English. A `.fr.html` or `.de.html` URL still scrapes, but those fields come back
  raw or empty.
- **Bot check.** Booking.com detects automation. A visible browser — the default — is
  noticeably more reliable than `--headless`. If a challenge page appears the scraper stops
  and says so: solve it in the window and re-run.
- **Selector drift.** Property details come from the page's JSON-LD block, which is stable.
  Review cards are matched on `data-testid` attributes, which are not: if reviews stop coming
  back, run with `--debug` and compare `results/debug_reviews.html` against the selectors in
  `reviews_dom.py`.
- **GraphQL schema changes.** If a field the reviews depend on disappears from the response,
  fast mode names it and reads the page instead, rather than reporting a run full of blank
  reviews. The field mapping lives in `reviews_api.parse_review_card`.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

Both run in CI on every push, against Python 3.10, 3.12 and 3.13.

The suite covers the parsers, the pagination, the report formatting and the orchestration,
against stubbed pages and a fake review API. Nothing touches the network or launches a
browser, so it finishes in well under a second.

## Legal and ethical use

For personal research on publicly visible review data. Read Booking.com's Terms of Service
before using it. Don't use it for commercial data harvesting or bulk scraping.

Reports contain other people's names, countries and written reviews. That is personal data:
keep the files to yourself, and don't republish them.

## Licence

MIT — see [LICENSE](LICENSE).
