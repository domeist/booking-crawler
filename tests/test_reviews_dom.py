"""Review-card parsing against stubbed cards — no browser required."""

import asyncio

from booking_crawler.reviews_dom import (
    compose_stay,
    extract_cards_on_page,
    parse_avatar,
    parse_review_date,
    parse_score,
    parse_stay_info,
    strip_ui_text,
)
from tests.fakes import FakeCard, FakePage

FULL_CARD = {
    '[data-testid="review-avatar"]': "Eric\nUnited States",
    '[data-testid="review-date"]': "Reviewed: 3 October 2025",
    '[data-testid="review-score"]': "Scored 10\n10",
    '[data-testid="review-stay-info"]': (
        "Double or Twin Room\n1 night\xa0·\xa0October 2025\nSolo traveller"
    ),
    '[data-testid="review-title"]': "Excellent value and location.",
    '[data-testid="review-positive-text"]': "Spotless clean.",
    '[data-testid="review-negative-text"]': "Needs handrail on stairs.",
    '[data-testid="review-traveler-type"]': "Solo traveller",
    '[data-testid="review-room-name"]': "Double or Twin Room",
    '[data-testid="review-partner-reply"]': "Hotel response: thank you very much",
}


def _extract(*cards):
    return asyncio.run(extract_cards_on_page(FakePage({}, [FakeCard(card) for card in cards])))


def test_a_full_card_maps_onto_every_field():
    (review,) = _extract(FULL_CARD)
    assert review == {
        "review_id": "",
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


def test_a_card_with_only_praise_still_parses():
    (review,) = _extract(
        {
            '[data-testid="review-avatar"]': "Anna",
            '[data-testid="review-positive-text"]': "All good",
        }
    )
    assert review["reviewer"] == "Anna"
    assert review["pros"] == "All good"
    assert review["cons"] == ""


def test_cards_with_no_score_and_no_text_are_dropped():
    assert _extract({'[data-testid="review-avatar"]': "Ghost"}) == []


def test_whitespace_inside_review_text_is_collapsed():
    (review,) = _extract(
        {'[data-testid="review-positive-text"]': "Lovely   place\n\n  by the sea"}
    )
    assert review["pros"] == "Lovely place by the sea"


def test_parse_avatar_splits_reviewer_and_country():
    assert parse_avatar("Eric\nUnited States") == ("Eric", "United States")
    assert parse_avatar("Eric") == ("Eric", "")
    assert parse_avatar("") == ("", "")


def test_parse_review_date_normalises_known_formats():
    assert parse_review_date("Reviewed: 3 October 2025") == "2025-10-03"
    assert parse_review_date("Reviewed: 3 Oct 2025") == "2025-10-03"
    assert parse_review_date("") == ""


def test_parse_review_date_keeps_text_it_cannot_parse():
    assert parse_review_date("Reviewed: last week") == "last week"


def test_parse_score_reads_the_number():
    assert parse_score("Scored 10\n10") == "10"
    assert parse_score("Scored 8,5\n8,5") == "8.5"
    assert parse_score("") == ""


def test_parse_stay_info_flattens_separators():
    assert parse_stay_info("1 night\xa0·\xa0October 2025") == "1 night, October 2025"


def test_compose_stay_drops_details_reported_in_their_own_fields():
    stay_info = "Double or Twin Room\n1 night\xa0·\xa0October 2025\nSolo traveller"
    assert compose_stay(stay_info, "Double or Twin Room", "Solo traveller") == (
        "1 night, October 2025"
    )


def test_compose_stay_keeps_everything_when_nothing_is_duplicated():
    assert compose_stay("2 nights · May 2026", "", "") == "2 nights, May 2026"


def test_strip_ui_text_removes_card_labels():
    assert strip_ui_text("Hotel response: thank you") == "thank you"
    assert strip_ui_text("Property response: thanks") == "thanks"
    assert strip_ui_text("A long review... Continue reading") == "A long review"
    assert strip_ui_text("") == ""
