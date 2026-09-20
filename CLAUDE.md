# booking-crawler

CLI that scrapes a booking.com property page into a readable text report, for uploading to
an AI to summarise. See README.md for usage; this file records the non-obvious constraints.

## Gotchas

- **Clicking "Next" in the review list makes no network request.** Booking.com pre-loads
  reviews into an Apollo cache on first load. The request worth intercepting is the
  *initial* `ReviewList` GraphQL call fired when "Read all reviews" is clicked — not
  anything triggered by paging. Fast mode depends on this; don't "optimise" it into
  intercepting Next.
- **The GraphQL request cannot be built from scratch.** It carries a session-bound AWS WAF
  challenge token, so the browser is always needed for the first page load. Replay the
  headers captured from the real request rather than hand-writing them.
- **`/reviews/<cc>/hotel/<name>.html` no longer works** — it 302s back to the property page
  (verified 2026-09-19). The old offset-pagination and `li.review_item` extractors were
  removed. Don't re-add them without re-checking.
- **Property details come from the page's JSON-LD block**, not CSS selectors. The `<h1>` is
  decorated for SEO ("Name (Hotel) (Albania) deals") and the `data-testid` attributes for
  name, address and type were already dead by 2026. JSON-LD survived; prefer it.
- **`review-subscore` elements are not inside `ReviewSubscoresDesktop`.** Querying them as
  descendants silently returns nothing, which is how the score breakdown was missing from
  every report for months. Query the test id globally.
- **Review dates from the API are Unix timestamps**, and the guest's name is
  `guestDetails.username` — not `displayName`, which does not exist and silently yielded
  empty names.
- **Advance `skip` by the number of cards returned, never by the requested limit.** The code
  asks for 25 while the page asks for 10; if booking.com ever clamps it, a fixed stride
  silently skips reviews. `tests/test_reviews_api.py` has a fake server that clamps, which
  fails if this regresses.
- **A JSON-LD block needs more than a `name` to be the property.** Pages carry Organization
  and BreadcrumbList blocks too; picking the first one with a name titles every report
  "Booking.com". `json_ld_score` scores candidates and `MIN_JSON_LD_SCORE` rejects the rest.
- **Partial schema drift is worse than total drift.** If a renamed field leaves the rest
  intact, reviews parse into blanks and a run "succeeds". `detect_schema_drift` checks the
  first page for the fields the mapping needs and falls back to reading the page.
- **`--no-sandbox` is only for root.** Chromium's sandbox works fine as a normal user
  (verified 2026-09-19); passing the flag unconditionally disabled it for no reason.
  The user agent is built from the running browser's version — a UA that contradicts the
  engine is itself a bot signal.
- **Reports must not contain the URL's query string.** Booking.com URLs carry a `sid`
  session identifier; the whole point of the report is to share it. `report.clean_url`
  handles this — keep it that way.

## Anti-bot notes

Non-headless is the default because booking.com detects headless more aggressively.
`--headless` works for light use but hits challenge pages sooner. Repeated scrapes of the
same property in quick succession will start returning challenge pages; back off.

## Testing

`pytest` covers parsing, pagination and formatting with no browser and no network:
`tests/fakes.py` stubs the handful of Playwright calls the extractors make, and
`tests/test_reviews_api.py` has a fake review API that honours `skip`, clamps `limit` and
fails on demand. Selector and schema changes still need a live check, so verify edits to
`reviews_api.py` or `reviews_dom.py` with a real run in both modes.
