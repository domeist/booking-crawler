import asyncio
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from rich.console import Console

console = Console()


async def _delay(lo=0.5, hi=2.0):
    await asyncio.sleep(random.uniform(lo, hi))


async def _dismiss_cookie_banner(page):
    for sel in [
        '[id*="onetrust-accept"]',
        'button[data-gdpr-consent]',
        '#cookie_warning button',
        'button:has-text("Accept")',
        'button:has-text("Okay")',
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await _delay(0.5, 1.0)
                return
        except Exception:
            continue


def _reviews_url(property_url: str) -> str | None:
    """
    Construct the dedicated reviews URL from a property URL.
    /hotel/{cc}/{name}.{lang}.html -> /reviews/{cc}/hotel/{name}.{lang}.html
    """
    m = re.search(r"/hotel/([^/]+)/([^?#]+\.html)", property_url)
    if not m:
        return None
    cc, filename = m.group(1), m.group(2)
    parsed = urlparse(property_url)
    return f"{parsed.scheme}://{parsed.netloc}/reviews/{cc}/hotel/{filename}"


async def _extract_metadata(page) -> dict:
    data = {}

    async def text(selector, default=""):
        try:
            return (await page.locator(selector).first.inner_text(timeout=3000)).strip()
        except Exception:
            return default

    data["name"] = await text('[data-testid="property-name"]') or await text("h1")
    data["address"] = await text('[data-testid="property-location"] span, .hp_address_subtitle')
    data["property_type"] = await text('[data-testid="property-type-badge"], .hp__hotel-type-badge')
    data["description"] = await text('#property_description_content, [data-testid="property-description"]')

    # Overall score — try selectors in priority order
    data["overall_score"] = ""
    for sel in [
        '[data-testid="review-score-right-component"]',
        '.bui-review-score__badge',
        '[data-testid="review-score-component"]',
    ]:
        try:
            val = (await page.locator(sel).first.inner_text(timeout=2000)).strip()
            if val and re.search(r'\d', val):
                data["overall_score"] = val
                break
        except Exception:
            pass

    # Category score breakdown
    category_scores = {}
    try:
        rows = await page.locator(
            '[data-testid="ReviewSubscoresDesktop"] [data-testid="review-subscore"]'
        ).all()
        for row in rows:
            try:
                parts = (await row.inner_text(timeout=2000)).strip().split("\n")
                parts = [p.strip() for p in parts if p.strip()]
                if len(parts) >= 2:
                    category_scores[parts[0]] = parts[-1]
            except Exception:
                continue
    except Exception:
        pass
    data["category_scores"] = category_scores

    # Amenities
    amenities = []
    try:
        items = await page.locator(
            '[data-testid="property-most-popular-facilities-wrapper"] span, '
            '[data-testid="facility-list-item"]'
        ).all()
        for item in items:
            try:
                t = (await item.inner_text(timeout=1000)).strip()
                if t:
                    amenities.append(t)
            except Exception:
                continue
    except Exception:
        pass
    data["amenities"] = list(dict.fromkeys(amenities))
    return data


async def _extract_new_ui_reviews(page) -> list:
    """Extract reviews using the new booking.com UI (data-testid selectors)."""
    cards = await page.locator('[data-testid="review-card"]').all()
    if not cards:
        return []

    console.print(f"  [dim]Found {len(cards)} review cards (new UI)[/dim]")
    reviews = []

    for card in cards:
        r = {}

        async def field(sel, default=""):
            try:
                return (await card.locator(sel).first.inner_text(timeout=1500)).strip() or default
            except Exception:
                return default

        r["reviewer"] = await field('[data-testid="review-avatar"] .b08850ce41')
        r["country"] = await field('[data-testid="review-avatar"] img[alt]')
        if not r["country"]:
            try:
                r["country"] = (
                    await card.locator('[data-testid="review-avatar"] .fff1944c52')
                    .first.inner_text(timeout=1000)
                ).strip()
            except Exception:
                r["country"] = ""

        r["date"] = await field('[data-testid="review-date"]')
        r["score"] = await field('[data-testid="review-score"] [aria-hidden="true"]')

        try:
            tags = await card.locator('[data-testid="review-stay-info"] li').all()
            r["info_tags"] = [
                t for t in
                [(await li.inner_text(timeout=1000)).strip() for li in tags]
                if t
            ]
        except Exception:
            r["info_tags"] = []

        r["pros"] = await field('[data-testid="review-positive-text"]')
        r["cons"] = await field('[data-testid="review-negative-text"]')
        for key in ("pros", "cons"):
            r[key] = re.sub(r'\s+', ' ', r[key]).strip()

        if r["pros"] or r["cons"] or r["score"]:
            reviews.append(r)

    return reviews


async def _extract_old_ui_reviews(page) -> list:
    """Extract reviews using the old booking.com UI (class-based selectors)."""
    cards = await page.locator('li.review_item').all()
    if not cards:
        return []

    console.print(f"  [dim]Found {len(cards)} review cards (old UI)[/dim]")
    reviews = []

    for card in cards:
        r = {}

        async def field(sel, default=""):
            try:
                return (await card.locator(sel).first.inner_text(timeout=1500)).strip() or default
            except Exception:
                return default

        r["date"] = await field("p.review_item_date")
        r["reviewer"] = await field("p.reviewer_name")
        r["country"] = await field("span.reviewer_country span[itemprop='name']")
        r["score"] = await field("span.review-score-badge")

        try:
            tags = await card.locator("li.review_info_tag").all()
            r["info_tags"] = [
                t for t in
                [(await li.inner_text(timeout=1000)).replace("•", "").strip() for li in tags]
                if t
            ]
        except Exception:
            r["info_tags"] = []

        r["pros"] = await field("p.review_pos span[itemprop='reviewBody']")
        r["cons"] = await field("p.review_neg span[itemprop='reviewBody']")

        if r["pros"] or r["cons"] or r["score"]:
            reviews.append(r)

    return reviews


async def _extract_reviews_on_page(page) -> list:
    """Try to extract reviews from the current page, auto-detecting UI variant."""
    try:
        title = await page.title()
        card_count_new = await page.locator('[data-testid="review-card"]').count()
        card_count_old = await page.locator('li.review_item').count()
        console.print(
            f"  [dim]Page: {title[:50]} | "
            f"new-UI cards: {card_count_new} | old-UI cards: {card_count_old}[/dim]"
        )
    except Exception:
        pass

    reviews = await _extract_new_ui_reviews(page)
    if reviews:
        return reviews
    reviews = await _extract_old_ui_reviews(page)
    if reviews:
        return reviews

    Path("results").mkdir(exist_ok=True)
    Path("results/debug_failed.html").write_text(await page.content(), encoding="utf-8")
    console.print("  [yellow]No reviews found — saved HTML to results/debug_failed.html[/yellow]")
    return []


async def _navigate_to_all_reviews(page, property_url: str) -> str:
    """
    Click 'Read all reviews' to open the full review listing.
    Returns the base URL to use for further pagination.
    If the click navigated to a new URL, returns that URL (for offset pagination).
    Otherwise returns the current property URL (for inline review extraction).
    """
    for sel in [
        '[data-testid="fr-read-all-reviews"]',
        '[data-testid="review-score-read-all-actionable"]',
        'button:has-text("Read all reviews")',
        'a:has-text("Read all reviews")',
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=3000):
                before_url = page.url
                await btn.click()
                await _delay(2.5, 3.5)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                after_url = page.url
                console.print(f"  [dim]After click URL: {after_url[:80]}[/dim]")

                if after_url != before_url:
                    return after_url.split("?")[0]
                break  # button found and clicked but no navigation — reviews are inline
        except Exception:
            continue

    # Button not found or reviews are inline — try the dedicated /reviews/ URL
    rev_url = _reviews_url(property_url)
    if rev_url:
        console.print(f"  [dim]Trying reviews URL: {rev_url}[/dim]")
        await page.goto(rev_url, wait_until="domcontentloaded", timeout=30000)
        await _delay(2.0, 3.0)
        await _dismiss_cookie_banner(page)

        if await page.locator('li.review_item, [data-testid="review-card"]').count() > 0:
            console.print("  [dim]Reviews URL works[/dim]")
            return rev_url

    console.print("  [yellow]Using inline reviews from property page[/yellow]")
    return page.url.split("?")[0]


async def _wait_for_reviews_change(page, old_first_text: str, timeout: float = 10.0):
    """Poll until the first review card shows different text, indicating new reviews loaded."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(0.4)
        try:
            new_text = await page.locator('[data-testid="review-card"]').first.inner_text(timeout=1000)
            if new_text != old_first_text:
                return
        except Exception:
            return
    await _delay(0.5, 1.0)


async def _extract_all_inline_reviews(page) -> list:
    """
    Paginate through the inline review section by clicking 'Next'.
    Waits for the DOM to update between clicks using content-change detection.
    Falls back to featured reviews if no review cards are found.
    """
    await _delay(1.0, 1.5)

    review_card_sel = '[data-testid="review-card"]'
    card_count = await page.locator(review_card_sel).count()
    console.print(f"  [dim]Review cards visible: {card_count}[/dim]")

    if card_count == 0:
        console.print("  [dim]No review section found — returning featured reviews[/dim]")
        return await _extract_reviews_on_page(page)

    all_reviews = []
    seen_signatures: set[str] = set()
    page_num = 1

    while True:
        console.print(f"  [dim]-> Review page {page_num}...[/dim]")
        try:
            batch = await _extract_new_ui_reviews(page)
        except Exception as e:
            console.print(f"  [yellow]Review extraction error on page {page_num}: {e}[/yellow]")
            break

        new_batch = [
            r for r in batch
            if (sig := f"{r.get('reviewer','')}{r.get('date','')}{r.get('score','')}")
            and sig not in seen_signatures
            and not seen_signatures.add(sig)  # add returns None, so always truthy negation
        ]

        if not new_batch:
            break
        all_reviews.extend(new_batch)

        try:
            first_text_before = await page.locator(review_card_sel).first.inner_text(timeout=2000)
        except Exception:
            first_text_before = ""

        # Scroll pagination controls into view
        try:
            await page.locator('[data-testid="review-list-container"]').last.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            try:
                await page.evaluate("window.scrollBy(0, 600)")
            except Exception:
                pass
        await _delay(0.4, 0.7)

        next_clicked = await page.evaluate("""() => {
            const containers = [
                document.querySelector('div[role="dialog"]'),
                document.querySelector('[data-testid="review-list-container"]'),
                document.querySelector('[data-testid="PropertyReviewsRegionBlock"]'),
                document.body
            ].filter(Boolean);

            for (const section of containers) {
                const btn = Array.from(section.querySelectorAll('button')).find(b => {
                    const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                    return (aria === 'next' || aria === 'next page') &&
                           b.offsetParent !== null && !b.disabled;
                });
                if (btn) {
                    btn.scrollIntoView({block: 'center'});
                    btn.click();
                    return true;
                }
            }
            return false;
        }""")

        if not next_clicked:
            break

        if first_text_before:
            await _wait_for_reviews_change(page, first_text_before, timeout=12.0)
        else:
            await _delay(2.0, 3.0)
        page_num += 1

    return all_reviews


async def _scrape_all_reviews(page, base_url: str) -> list:
    """Paginate through a dedicated /reviews/ page using offset URL parameters."""
    PAGE_SIZE = 25
    all_reviews = []
    offset = 0

    while True:
        console.print(f"  [dim]-> Scraping reviews at offset {offset}...[/dim]")
        batch = await _extract_reviews_on_page(page)
        all_reviews.extend(batch)

        if not batch:
            break

        offset += PAGE_SIZE
        await page.goto(f"{base_url}?offset={offset}", wait_until="domcontentloaded", timeout=30000)
        await _delay(1.5, 2.5)

        # Booking.com redirects back to the property page when offset exceeds total reviews
        if "reviews" not in page.url and offset > 0:
            console.print(f"  [yellow]Redirected at offset {offset} — all reviews collected[/yellow]")
            break

    return all_reviews


async def scrape(url: str, headless: bool = False, debug: bool = False) -> dict:
    console.print("\n[bold cyan]Launching browser...[/bold cyan]")

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

        console.print("[bold cyan]Navigating to property page...[/bold cyan]")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except PlaywrightTimeout:
            raise RuntimeError("Timed out loading the page.")

        await _delay(2.0, 3.0)
        await _dismiss_cookie_banner(page)

        if await page.locator('iframe[src*="captcha"], #captcha, .g-recaptcha').count() > 0:
            raise RuntimeError("CAPTCHA detected. Solve it in the browser window and re-run.")

        console.print("[bold cyan]Extracting property metadata...[/bold cyan]")
        metadata = await _extract_metadata(page)
        console.print(f"  [green]OK[/green] Property: {metadata.get('name', 'Unknown')}")

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await _delay(1.0, 2.0)

        console.print("[bold cyan]Navigating to full reviews...[/bold cyan]")
        reviews_base_url = await _navigate_to_all_reviews(page, url)
        console.print(f"  [dim]Reviews base URL: {reviews_base_url}[/dim]")

        if debug:
            Path("results").mkdir(exist_ok=True)
            await page.screenshot(path="results/debug_reviews.png", full_page=True)
            Path("results/debug_reviews.html").write_text(await page.content(), encoding="utf-8")
            console.print("  [dim]Debug files saved[/dim]")

        if "reviews" in reviews_base_url:
            console.print("[bold cyan]Paginating through reviews...[/bold cyan]")
            reviews = await _scrape_all_reviews(page, reviews_base_url)
        else:
            console.print("[bold cyan]Extracting inline reviews...[/bold cyan]")
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
