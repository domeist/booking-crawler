"""Orchestration: mode selection, fallbacks and warnings, with no browser."""

import asyncio
from contextlib import asynccontextmanager

import pytest

import booking_crawler.scraper as scrape_module
from booking_crawler import reviews_api, reviews_dom
from booking_crawler.errors import ScrapeError
from booking_crawler.scraper import _warn_if_incomplete, scrape
from tests.fakes import FakePage

URL = "https://www.booking.com/hotel/al/example.html?sid=secret"
METADATA = {"name": "Example Hotel", "review_count": "2"}
API_REVIEW = {"review_id": "a", "pros": "From the API"}
DOM_REVIEW = {"review_id": "", "pros": "From the page"}


class _Recorder:
    def __init__(self):
        self.warnings = []
        self.statuses = []

    def status(self, message):
        self.statuses.append(message)

    def progress(self, collected, total):
        pass

    def warn(self, message):
        self.warnings.append(message)


class _FakeContext:
    async def cookies(self):
        return [{"name": "session", "value": "x"}]


@pytest.fixture
def stub(monkeypatch):
    """Replace the browser and both review paths with controllable stubs."""

    state = {
        "captured": [],
        "button": True,
        "api_reviews": [],
        "dom_reviews": [],
        "metadata": dict(METADATA),
        "drift": None,
        "api_called": False,
        "dom_called": False,
    }

    @asynccontextmanager
    async def fake_browser_page(headless):
        yield FakePage({}), _FakeContext()

    @asynccontextmanager
    async def fake_intercept(page):
        yield state["captured"]

    async def fake_open_property_page(page, url):
        return None

    async def fake_extract_metadata(page):
        return state["metadata"]

    async def fake_open_reviews_section(page):
        return state["button"]

    async def fake_raise_if_bot_check(page):
        return None

    async def instant_wait(condition, timeout_s, interval_s=0.25):
        # The real one polls for CAPTURE_TIMEOUT_S; wait_until has its own tests.
        return bool(condition())

    async def fake_fetch_all_reviews(
        request, cookies, *, on_progress=None, on_error=None, on_drift=None
    ):
        state["api_called"] = True
        if state["drift"] and on_drift:
            on_drift(state["drift"])
            return []
        return list(state["api_reviews"])

    async def fake_paginate_reviews(page, *, on_progress=None):
        state["dom_called"] = True
        return list(state["dom_reviews"])

    monkeypatch.setattr(scrape_module, "browser_page", fake_browser_page)
    monkeypatch.setattr(scrape_module, "open_property_page", fake_open_property_page)
    monkeypatch.setattr(scrape_module, "extract_metadata", fake_extract_metadata)
    monkeypatch.setattr(scrape_module, "open_reviews_section", fake_open_reviews_section)
    monkeypatch.setattr(scrape_module, "raise_if_bot_check", fake_raise_if_bot_check)
    monkeypatch.setattr(scrape_module, "wait_until", instant_wait)
    monkeypatch.setattr(reviews_api, "intercept_review_list", fake_intercept)
    monkeypatch.setattr(reviews_api, "fetch_all_reviews", fake_fetch_all_reviews)
    monkeypatch.setattr(reviews_dom, "paginate_reviews", fake_paginate_reviews)
    return state


def _run(reporter=None, **kwargs):
    return asyncio.run(scrape(URL, reporter=reporter, **kwargs))


def test_fast_mode_uses_the_api_and_does_not_touch_the_page(stub):
    stub["captured"] = ["a-request"]
    stub["api_reviews"] = [API_REVIEW, dict(API_REVIEW, review_id="b")]

    data = _run()

    assert data["reviews"] == stub["api_reviews"]
    assert stub["api_called"] and not stub["dom_called"]


def test_the_returned_url_carries_no_session_id(stub):
    stub["captured"] = ["a-request"]
    stub["api_reviews"] = [API_REVIEW]
    assert _run()["url"] == "https://www.booking.com/hotel/al/example.html"


def test_falls_back_to_the_page_when_the_api_request_is_not_seen(stub):
    stub["dom_reviews"] = [DOM_REVIEW]
    reporter = _Recorder()

    data = _run(reporter)

    assert data["reviews"] == [DOM_REVIEW]
    assert stub["dom_called"] and not stub["api_called"]
    assert any("intercept" in warning for warning in reporter.warnings)


def test_falls_back_to_the_page_when_the_api_schema_has_moved(stub):
    stub["captured"] = ["a-request"]
    stub["drift"] = "textDetails"
    stub["dom_reviews"] = [DOM_REVIEW]
    reporter = _Recorder()

    data = _run(reporter)

    assert data["reviews"] == [DOM_REVIEW]
    assert stub["api_called"] and stub["dom_called"]
    assert any("textDetails" in warning for warning in reporter.warnings)


def test_standard_mode_never_calls_the_api(stub):
    stub["captured"] = ["a-request"]
    stub["dom_reviews"] = [DOM_REVIEW]

    _run(mode="standard")

    assert stub["dom_called"] and not stub["api_called"]


def test_a_stale_read_all_reviews_button_is_reported(stub):
    stub["button"] = False
    stub["dom_reviews"] = [DOM_REVIEW]
    reporter = _Recorder()

    _run(reporter)

    assert any("Read all reviews" in warning for warning in reporter.warnings)


def test_an_incomplete_scrape_is_reported_on_the_api_path(stub):
    stub["captured"] = ["a-request"]
    stub["api_reviews"] = [API_REVIEW]  # metadata claims 2
    reporter = _Recorder()

    _run(reporter)

    assert any("1 of 2" in warning for warning in reporter.warnings)


def test_an_incomplete_scrape_is_reported_on_the_page_path_too(stub):
    """The warning must not be wired to one path only."""
    stub["dom_reviews"] = [DOM_REVIEW]
    reporter = _Recorder()

    _run(reporter)

    assert any("1 of 2" in warning for warning in reporter.warnings)


def test_a_complete_scrape_says_nothing(stub):
    stub["captured"] = ["a-request"]
    stub["api_reviews"] = [API_REVIEW, dict(API_REVIEW, review_id="b")]
    reporter = _Recorder()

    _run(reporter)

    assert reporter.warnings == []


def test_no_reviews_anywhere_is_an_error_not_an_empty_report(stub):
    with pytest.raises(ScrapeError, match="No reviews found"):
        _run()


def test_an_unknown_mode_is_rejected_before_a_browser_is_launched():
    with pytest.raises(ValueError, match="mode must be one of"):
        asyncio.run(scrape(URL, mode="turbo"))


# --- the warning itself ----------------------------------------------------


def test_warns_when_fewer_reviews_came_back_than_expected():
    reporter = _Recorder()
    _warn_if_incomplete(reporter, [{}] * 230, {"review_count": "1734"})
    assert "230 of 1734" in reporter.warnings[0]


def test_stays_quiet_when_the_count_matches_or_is_unusable():
    reporter = _Recorder()
    _warn_if_incomplete(reporter, [{}] * 79, {"review_count": "79"})
    _warn_if_incomplete(reporter, [{}], {"review_count": ""})
    _warn_if_incomplete(reporter, [{}], {"review_count": "lots"})
    _warn_if_incomplete(reporter, [{}], {"review_count": ["2"]})
    _warn_if_incomplete(reporter, [{}], {})
    assert reporter.warnings == []
