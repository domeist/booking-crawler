import asyncio

import httpx
import pytest

from booking_crawler.reviews_api import (
    ReviewListRequest,
    fetch_all_reviews,
    format_review_date,
    format_score,
    parse_review_card,
)

CARD = {
    "reviewScore": 10.0,
    "reviewedDate": 1759521176,
    "partnerReply": {"reply": "thank you very much"},
    "textDetails": {
        "positiveText": "Spotless clean.",
        "negativeText": "Needs handrail on stairs.",
        "title": "Excellent value and location.",
    },
    "bookingDetails": {
        "roomType": {"name": "Double or Twin Room"},
        "checkinDate": "2025-10-02",
        "numNights": 1,
    },
    "reviewUrl": "2580844e87d67c50",
    "guestDetails": {
        "username": "Eric",
        "countryName": "United States",
        "guestTypeTranslation": "Solo traveller",
    },
}


def test_parse_review_card_maps_every_field():
    review = parse_review_card(CARD)
    assert review == {
        "review_id": "2580844e87d67c50",
        "reviewer": "Eric",
        "country": "United States",
        "date": "2025-10-03",
        "title": "Excellent value and location.",
        "score": "10",
        "traveller_type": "Solo traveller",
        "room": "Double or Twin Room",
        "stay": "1 night, October 2025",
        "pros": "Spotless clean.",
        "cons": "Needs handrail on stairs.",
        "reply": "thank you very much",
    }


def test_parse_review_card_handles_anonymous_guest():
    card = {**CARD, "guestDetails": {"anonymous": True, "countryCode": "de"}}
    review = parse_review_card(card)
    assert review["reviewer"] == "Anonymous"
    assert review["country"] == "de"


def test_parse_review_card_survives_missing_sections():
    review = parse_review_card({"reviewScore": 7.5})
    assert review["score"] == "7.5"
    assert review["reviewer"] == ""
    assert review["stay"] == ""


def test_parse_review_card_rejects_empty_cards():
    assert parse_review_card({}) is None
    assert parse_review_card({"textDetails": {"positiveText": ""}}) is None
    assert parse_review_card("not a card") is None


def test_parse_review_card_pluralises_nights():
    card = {**CARD, "bookingDetails": {"numNights": 3, "checkinDate": "2025-10-02"}}
    assert parse_review_card(card)["stay"] == "3 nights, October 2025"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1759521176, "2025-10-03"),
        ("1759521176", "2025-10-03"),
        ("2026-03-15T10:00:00Z", "2026-03-15"),
        (None, ""),
        ("", ""),
    ],
)
def test_format_review_date(raw, expected):
    assert format_review_date(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [(10.0, "10"), (9.6, "9.6"), ({"score": 8.0}, "8"), (None, ""), ("n/a", "n/a")],
)
def test_format_score(raw, expected):
    assert format_score(raw) == expected


def _request():
    return ReviewListRequest(
        url="https://www.booking.com/dml/graphql",
        body={
            "operationName": "ReviewList",
            "query": "query ReviewList {}",
            "variables": {"shouldShowPhotos": True, "input": {"hotelId": 1, "skip": 0, "limit": 25}},
        },
        headers={
            "cookie": "session=secret",
            "content-length": "123",
            "host": "www.booking.com",
            "x-booking-csrf-token": "token",
            ":authority": "www.booking.com",
        },
    )


def test_body_for_page_advances_skip_and_keeps_other_variables():
    body = _request().body_for_page(50)
    assert body["variables"]["input"]["skip"] == 50
    assert body["variables"]["input"]["limit"] == 25
    assert body["variables"]["input"]["hotelId"] == 1
    assert body["variables"]["shouldShowPhotos"] is True
    assert body["query"] == "query ReviewList {}"


def test_body_for_page_does_not_mutate_the_captured_request():
    request = _request()
    request.body_for_page(50)
    assert request.body["variables"]["input"]["skip"] == 0


def test_replay_headers_drops_connection_specific_headers():
    headers = _request().replay_headers()
    assert headers == {"x-booking-csrf-token": "token"}


def test_page_size_defaults_when_missing():
    request = ReviewListRequest(url="u", body={"variables": {"input": {}}})
    assert request.page_size == 10


def test_body_for_page_accepts_a_larger_page_size():
    body = _request().body_for_page(25, limit=25)
    assert body["variables"]["input"] == {"hotelId": 1, "skip": 25, "limit": 25}


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    """Serves queued responses and records which skips were requested."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.skips = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json):
        self.skips.append(json["variables"]["input"]["skip"])
        return self._responses.pop(0) if self._responses else _FakeResponse()


def _page(count, total, start=0):
    return _FakeResponse(
        {
            "data": {
                "reviewListFrontend": {
                    "reviewsCount": total,
                    "reviewCard": [
                        {
                            "reviewScore": 9.0,
                            "textDetails": {"positiveText": f"review {start + index}"},
                        }
                        for index in range(count)
                    ],
                }
            }
        }
    )


async def _collect(monkeypatch, responses, **kwargs):
    """Run fetch_all_reviews against canned responses, without real sleeps."""
    client = _FakeClient(responses)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("booking_crawler.reviews_api.asyncio.sleep", no_sleep)
    monkeypatch.setattr("booking_crawler.reviews_api.httpx.AsyncClient", lambda **_: client)
    return await fetch_all_reviews(_request(), {}, **kwargs), client


def test_fetch_stops_once_every_review_is_collected(monkeypatch):
    reviews, client = asyncio.run(
        _collect(monkeypatch, [_page(25, 30), _page(5, 30, start=25)])
    )
    assert len(reviews) == 30
    assert client.skips == [0, 25]


def test_fetch_retries_an_empty_page_before_giving_up(monkeypatch):
    reviews, _ = asyncio.run(
        _collect(monkeypatch, [_page(25, 50), _page(0, 50), _page(25, 50, start=25)])
    )
    assert len(reviews) == 50


def test_fetch_keeps_what_it_collected_when_the_api_fails(monkeypatch):
    errors = []
    responses = [_page(25, 100), *[_FakeResponse(status_code=502) for _ in range(3)]]
    reviews, _ = asyncio.run(_collect(monkeypatch, responses, on_error=errors.append))
    assert len(reviews) == 25
    assert len(errors) == 1


def test_fetch_deduplicates_reviews_repeated_across_pages(monkeypatch):
    reviews, _ = asyncio.run(
        _collect(monkeypatch, [_page(25, 100), _page(25, 100), _page(0, 100), _page(0, 100)])
    )
    assert len(reviews) == 25
