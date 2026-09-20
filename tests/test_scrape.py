"""Orchestration behaviour that does not need a browser."""

import asyncio

import pytest

from booking_crawler.scrape import _warn_if_incomplete, scrape


class _Recorder:
    def __init__(self):
        self.warnings = []

    def status(self, message):
        pass

    def progress(self, collected, total):
        pass

    def warn(self, message):
        self.warnings.append(message)


def test_warns_when_fewer_reviews_came_back_than_expected():
    reporter = _Recorder()
    _warn_if_incomplete(reporter, [{}] * 230, {"review_count": "1734"})
    assert "230 of 1734" in reporter.warnings[0]


def test_stays_quiet_when_the_count_matches_or_is_unusable():
    reporter = _Recorder()
    _warn_if_incomplete(reporter, [{}] * 79, {"review_count": "79"})
    _warn_if_incomplete(reporter, [{}], {"review_count": ""})
    _warn_if_incomplete(reporter, [{}], {"review_count": "lots"})
    _warn_if_incomplete(reporter, [{}], {})
    assert reporter.warnings == []


def test_an_unknown_mode_is_rejected_before_a_browser_is_launched():
    with pytest.raises(ValueError, match="mode must be one of"):
        asyncio.run(scrape("https://www.booking.com/hotel/x.html", mode="turbo"))
