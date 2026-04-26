import asyncio
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import httpx
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from rich.console import Console

from scraper import (
    _delay,
    _dismiss_cookie_banner,
    _extract_metadata,
    _extract_all_inline_reviews,
    _scrape_all_reviews,
)

console = Console()


def _parse_review_card(card: dict) -> dict | None:
    """Map a booking.com GraphQL reviewCard dict to our standard schema."""
    r = {}

    guest = card.get("guestDetails") or {}
    r["reviewer"] = guest.get("displayName") or guest.get("name") or ""
    r["country"] = guest.get("countryName") or guest.get("countryCode") or ""

    booking = card.get("bookingDetails") or {}
    raw_date = card.get("reviewedDate") or booking.get("checkin") or ""
    r["date"] = str(raw_date)[:10] if raw_date else ""

    score_raw = card.get("reviewScore")
    if isinstance(score_raw, dict):
        r["score"] = str(score_raw.get("score", ""))
    elif score_raw is not None:
        r["score"] = str(score_raw)
    else:
        r["score"] = ""

    text = card.get("textDetails") or {}
    r["pros"] = text.get("positiveText") or ""
    r["cons"] = text.get("negativeText") or ""

    tags = []
    for tag_obj in card.get("bookingDetails", {}).get("tags", []) or []:
        if isinstance(tag_obj, dict):
            name = tag_obj.get("name") or tag_obj.get("value") or ""
            if name:
                tags.append(name)
        elif isinstance(tag_obj, str):
            tags.append(tag_obj)
    r["info_tags"] = tags

    if r["pros"] or r["cons"] or r["score"]:
        return r
    return None


async def _fast_fetch_reviews(page, context, initial_url: str, initial_body: str) -> list | None:
    """
    Replicate the initial ReviewList GraphQL request for each page using httpx.
    initial_url and initial_body come from the intercepted 'Read all reviews' click.
    Returns full review list or None on failure.
    """
    try:
        req_body = json.loads(initial_body)
    except Exception:
        return None

    variables = req_body.get("variables", {}).get("input", {})
    if "skip" not in variables:
        console.print("  [yellow]No 'skip' in GraphQL variables — falling back[/yellow]")
        return None

    limit = variables.get("limit", 10)
    query = req_body.get("query", "")
    op_name = req_body.get("operationName", "ReviewList")
    extensions = req_body.get("extensions", {})

    # Build cookie dict from Playwright context
    pw_cookies = await context.cookies()
    cookie_dict = {c["name"]: c["value"] for c in pw_cookies}

    all_reviews = []
    seen_sigs: set[str] = set()
    skip = 0
    page_num = 1

    async with httpx.AsyncClient(
        cookies=cookie_dict,
        headers={
            "accept": "application/json",
            "accept-language": "en-GB,en;q=0.9",
            "content-type": "application/json",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "x-booking-context-aid": "304142",
            "origin": "https://www.booking.com",
            "referer": page.url,
        },
        follow_redirects=True,
        timeout=20.0,
    ) as client:
        consecutive_empty = 0

        while consecutive_empty < 2:
            body = {
                "operationName": op_name,
                "variables": {
                    **req_body.get("variables", {}),
                    "input": {**variables, "skip": skip, "limit": limit},
                },
                "extensions": extensions,
                "query": query,
            }
            console.print(f"  [dim]-> Fast-fetching page {page_num} (skip={skip})...[/dim]")

            try:
                resp = await client.post(initial_url, json=body)
                resp.raise_for_status()
            except Exception as e:
                console.print(f"  [yellow]HTTP error on page {page_num}: {e}[/yellow]")
                break

            try:
                data = resp.json()
            except Exception:
                console.print(f"  [yellow]Non-JSON response on page {page_num}[/yellow]")
                break

            cards = (
                data.get("data", {})
                .get("reviewListFrontend", {})
                .get("reviewCard", [])
            ) or []

            new_batch = []
            for card in cards:
                r = _parse_review_card(card)
                if not r:
                    continue
                sig = f"{r.get('reviewer','')}{r.get('date','')}{r.get('score','')}"
                if sig and sig not in seen_sigs:
                    seen_sigs.add(sig)
                    new_batch.append(r)

            if not new_batch:
                consecutive_empty += 1
            else:
                consecutive_empty = 0
                all_reviews.extend(new_batch)

            skip += limit
            page_num += 1
            await asyncio.sleep(random.uniform(0.3, 0.6))

    return all_reviews if all_reviews else None


async def scrape_fast(url: str, headless: bool = False, debug: bool = False) -> dict:
    console.print(f"\n[bold cyan]Launching browser (fast mode)...[/bold cyan]")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        # Intercept the ReviewList GraphQL call that fires when reviews section opens
        captured_gql: dict = {}

        async def on_route(route):
            req = route.request
            if "graphql" in req.url.lower() and not captured_gql.get("url"):
                pd = req.post_data or ""
                try:
                    op = json.loads(pd).get("operationName", "")
                    if op == "ReviewList":
                        captured_gql["url"] = req.url
                        captured_gql["body"] = pd
                        console.print(f"  [dim]Intercepted ReviewList GQL[/dim]")
                except Exception:
                    pass
            await route.continue_()

        await page.route("**/*", on_route)

        console.print("[bold cyan]Navigating to property page...[/bold cyan]")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except PlaywrightTimeout:
            raise RuntimeError("Timed out loading the page.")

        await _delay(2.0, 3.0)
        await _dismiss_cookie_banner(page)

        console.print("[bold cyan]Extracting property metadata...[/bold cyan]")
        metadata = await _extract_metadata(page)
        console.print(f"  [green]OK[/green] Property: {metadata.get('name', 'Unknown')}")

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await _delay(1.0, 2.0)

        # Click "Read all reviews" — this triggers the ReviewList GraphQL call
        console.print("[bold cyan]Opening reviews section...[/bold cyan]")
        for sel in [
            '[data-testid="fr-read-all-reviews"]',
            '[data-testid="review-score-read-all-actionable"]',
            'button:has-text("Read all reviews")',
            'a:has-text("Read all reviews")',
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    await _delay(2.0, 3.0)
                    break
            except Exception:
                continue

        await page.unroute("**/*")

        if debug:
            Path("results").mkdir(exist_ok=True)
            await page.screenshot(path="results/debug_reviews.png", full_page=True)
            Path("results/debug_reviews.html").write_text(await page.content(), encoding="utf-8")
            console.print("  [dim]Debug files saved[/dim]")

        # Determine whether we ended up on a dedicated /reviews/ page or inline
        current_url = page.url
        is_old_ui = await page.locator('li.review_item').count() > 0

        if "reviews" in urlparse(current_url).path:
            # Dedicated reviews page — use offset URL pagination
            console.print("[bold cyan]Paginating through reviews (offset URL)...[/bold cyan]")
            reviews_base = current_url.split("?")[0]
            reviews = await _scrape_all_reviews(page, reviews_base, is_old_ui)
        elif captured_gql.get("url"):
            # Inline reviews + captured GraphQL — fast-fetch remaining pages
            console.print("[bold cyan]Fast-fetching all reviews via GraphQL...[/bold cyan]")
            reviews = await _fast_fetch_reviews(
                page, context, captured_gql["url"], captured_gql["body"]
            )
            if reviews is None:
                console.print("[bold cyan]GraphQL parse failed — falling back to click-loop...[/bold cyan]")
                reviews = await _extract_all_inline_reviews(page)
        else:
            # No GraphQL captured — fall back to click-loop
            console.print("[bold cyan]No GraphQL captured — falling back to click-loop...[/bold cyan]")
            reviews = await _extract_all_inline_reviews(page)

        console.print(f"  [green]OK[/green] Collected {len(reviews)} reviews")
        try:
            await browser.close()
        except Exception:
            pass

    return {
        "url": url,
        "scraped_at": datetime.now().isoformat(),
        "metadata": metadata,
        "reviews": reviews,
    }
