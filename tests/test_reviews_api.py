"""The fast path: request replay, pagination and GraphQL card mapping."""

import asyncio

import httpx
import pytest

from booking_crawler.reviews_api import (
    ReviewListRequest,
    detect_schema_drift,
    fetch_all_reviews,
    format_review_date,
    format_score,
    parse_review_card,
)

CARD = {
    "reviewScore": 10.0,
    "reviewedDate": 1759521176,
    "reviewUrl": "2580844e87d67c50",
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
    "guestDetails": {
        "username": "Eric",
        "countryName": "United States",
        "guestTypeTranslation": "Solo traveller",
    },
}


# --- card mapping ----------------------------------------------------------


def test_parse_review_card_maps_every_field():
    assert parse_review_card(CARD) == {
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
    review = parse_review_card({**CARD, "guestDetails": {"anonymous": True, "countryCode": "de"}})
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
    [(10.0, "10"), (9.6, "9.6"), ("8,5", "8.5"), (None, ""), ("n/a", "n/a")],
)
def test_format_score(raw, expected):
    assert format_score(raw) == expected


# --- schema drift ----------------------------------------------------------


def test_detect_schema_drift_names_the_field_that_moved():
    renamed = {key: value for key, value in CARD.items() if key != "textDetails"}
    assert detect_schema_drift([renamed]) == "textDetails"
    assert detect_schema_drift([{**CARD, "reviewUrl": ""}]) == "reviewUrl"


def test_detect_schema_drift_accepts_a_healthy_page():
    assert detect_schema_drift([CARD, {"reviewScore": 8.0}]) is None
    assert detect_schema_drift([]) is None


# --- request replay --------------------------------------------------------


def _request():
    return ReviewListRequest(
        url="https://www.booking.com/dml/graphql",
        body={
            "operationName": "ReviewList",
            "query": "query ReviewList {}",
            "variables": {"shouldShowPhotos": True, "input": {"hotelId": 1, "skip": 0, "limit": 10}},
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
    body = _request().body_for_page(50, limit=25)
    assert body["variables"]["input"] == {"hotelId": 1, "skip": 50, "limit": 25}
    assert body["variables"]["shouldShowPhotos"] is True
    assert body["query"] == "query ReviewList {}"


def test_body_for_page_does_not_mutate_the_captured_request():
    request = _request()
    request.body_for_page(50)
    assert request.body["variables"]["input"]["skip"] == 0


def test_replay_headers_drops_connection_specific_headers():
    assert _request().replay_headers() == {"x-booking-csrf-token": "token"}


def test_page_size_defaults_when_missing():
    assert ReviewListRequest(url="u", body={"variables": {"input": {}}}).page_size == 10


# --- pagination ------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _card(index):
    return {**CARD, "reviewUrl": f"id-{index}", "textDetails": {"positiveText": f"review {index}"}}


def _payload(cards, total):
    return {"data": {"reviewListFrontend": {"reviewsCount": total, "reviewCard": cards}}}


class _FakeServer:
    """Serves `total` reviews, honouring skip and clamping limit like a real API."""

    def __init__(self, total, max_limit=None, failures=()):
        self.total = total
        self.max_limit = max_limit
        self.failures = list(failures)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json):
        variables = json["variables"]["input"]
        skip, limit = variables["skip"], variables["limit"]
        self.requests.append((skip, limit))
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                return _FakeResponse(status_code=failure)
        served = min(limit, self.max_limit or limit)
        cards = [_card(index) for index in range(skip, min(skip + served, self.total))]
        return _FakeResponse(_payload(cards, self.total))


class _QueuedServer(_FakeServer):
    """Serves exactly the pages it is given, for the awkward cases."""

    def __init__(self, pages):
        super().__init__(total=0)
        self.pages = list(pages)

    async def post(self, url, json):
        self.requests.append((json["variables"]["input"]["skip"], json["variables"]["input"]["limit"]))
        return self.pages.pop(0) if self.pages else _FakeResponse(_payload([], 0))


def _run(monkeypatch, server, **kwargs):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("booking_crawler.reviews_api.asyncio.sleep", no_sleep)
    monkeypatch.setattr("booking_crawler.reviews_api.httpx.AsyncClient", lambda **_: server)
    return asyncio.run(fetch_all_reviews(_request(), {}, **kwargs))


def test_collects_every_review_and_stops(monkeypatch):
    server = _FakeServer(total=30)
    reviews = _run(monkeypatch, server)
    assert len(reviews) == 30
    assert server.requests == [(0, 25), (25, 25)]


def test_loses_nothing_when_the_server_clamps_the_page_size(monkeypatch):
    """The stride must follow what the server sent, not what we asked for."""
    server = _FakeServer(total=100, max_limit=10)
    reviews = _run(monkeypatch, server)
    assert len(reviews) == 100
    assert [skip for skip, _ in server.requests] == list(range(0, 100, 10))


def test_retries_a_transient_empty_page_at_the_same_offset(monkeypatch):
    server = _QueuedServer(
        [
            _FakeResponse(_payload([_card(index) for index in range(25)], 50)),
            _FakeResponse(_payload([], 50)),
            _FakeResponse(_payload([_card(index) for index in range(25, 50)], 50)),
        ]
    )
    reviews = _run(monkeypatch, server)
    assert len(reviews) == 50
    assert [skip for skip, _ in server.requests] == [0, 25, 25]


def test_does_not_retry_the_final_page_once_every_review_is_in(monkeypatch):
    server = _FakeServer(total=25)
    _run(monkeypatch, server)
    assert len(server.requests) == 1


def test_keeps_what_it_collected_when_the_api_fails(monkeypatch):
    errors = []
    server = _FakeServer(total=500, failures=[None, 502, 502, 502])
    reviews = _run(monkeypatch, server, on_error=errors.append)
    assert len(reviews) == 25
    assert len(errors) == 1


def test_reports_drift_and_returns_nothing_rather_than_blank_reviews(monkeypatch):
    drifted = [
        {"reviewScore": 9.0, "guestDetails": {"username": "Ann"}, "reviewUrl": "x"}
        for _ in range(25)
    ]
    reported = []
    server = _QueuedServer([_FakeResponse(_payload(drifted, 200))])
    reviews = _run(monkeypatch, server, on_drift=reported.append)
    assert reviews == []
    assert reported == ["textDetails"]


def test_deduplicates_reviews_repeated_across_pages(monkeypatch):
    repeated = _FakeResponse(_payload([_card(index) for index in range(25)], 100))
    server = _QueuedServer([repeated, repeated, repeated])
    assert len(_run(monkeypatch, server)) == 25
