from booking_crawler.metadata import (
    clean_property_name,
    metadata_from_json_ld,
    parse_score_block,
    parse_subscore,
)
from booking_crawler.models import deduplicate, review_signature
from booking_crawler.reviews_dom import (
    parse_avatar,
    parse_review_date,
    parse_score,
    parse_stay_info,
)


def test_clean_property_name_strips_the_seo_suffix():
    assert clean_property_name("Guest House Shtaka (Hotel) (Albania) deals") == (
        "Guest House Shtaka",
        "Hotel",
    )


def test_clean_property_name_leaves_a_plain_heading_alone():
    assert clean_property_name("The Grand Hotel") == ("The Grand Hotel", "")


def test_parse_score_block_reads_score_label_and_count():
    assert parse_score_block("9.6\nExceptional\xa0·\xa079 reviews") == {
        "overall_score": "9.6",
        "score_label": "Exceptional",
        "review_count": "79",
    }


def test_parse_score_block_handles_empty_input():
    assert parse_score_block("")["overall_score"] == ""


def test_parse_subscore_skips_the_accessibility_label():
    text = "Staff, 9.9, Average rating out of 10\nStaff\n9.9"
    assert parse_subscore(text) == ("Staff", "9.9")


def test_parse_subscore_rejects_blocks_without_a_score():
    assert parse_subscore("Categories:") is None
    assert parse_subscore("") is None


def test_metadata_from_json_ld_extracts_the_fields_we_report():
    payload = {
        "name": "Guest House Shtaka",
        "@type": "Hotel",
        "description": "Located in Gjirokaster.",
        "address": {"streetAddress": "Rruga Gole Gushi, 6001 Gjirokaster, Albania"},
        "aggregateRating": {"ratingValue": 9.6, "reviewCount": 79},
    }
    assert metadata_from_json_ld(payload) == {
        "name": "Guest House Shtaka",
        "property_type": "Hotel",
        "description": "Located in Gjirokaster.",
        "address": "Rruga Gole Gushi, 6001 Gjirokaster, Albania",
        "overall_score": "9.6",
        "review_count": "79",
    }


def test_metadata_from_json_ld_builds_an_address_without_a_street():
    payload = {"address": {"addressLocality": "Gjirokaster", "addressCountry": "Albania"}}
    assert metadata_from_json_ld(payload)["address"] == "Gjirokaster, Albania"


def test_parse_avatar_splits_reviewer_and_country():
    assert parse_avatar("Eric\nUnited States") == ("Eric", "United States")
    assert parse_avatar("Eric") == ("Eric", "")
    assert parse_avatar("") == ("", "")


def test_parse_review_date_normalises_the_label():
    assert parse_review_date("Reviewed: 3 October 2025") == "2025-10-03"
    assert parse_review_date("") == ""


def test_parse_review_date_keeps_text_it_cannot_parse():
    assert parse_review_date("Reviewed: last week") == "last week"


def test_parse_score_reads_the_number():
    assert parse_score("Scored 10\n10") == "10"
    assert parse_score("") == ""


def test_parse_stay_info_flattens_separators():
    assert parse_stay_info("1 night\xa0·\xa0October 2025") == "1 night, October 2025"


def test_review_signature_distinguishes_same_named_reviewers():
    first = {"reviewer": "Eric", "date": "2025-10-03", "score": "10", "pros": "Clean"}
    second = {**first, "pros": "Noisy"}
    assert review_signature(first) != review_signature(second)


def test_deduplicate_keeps_first_occurrence_only():
    seen = set()
    review = {"reviewer": "Eric", "date": "2025-10-03", "score": "10", "pros": "Clean"}
    assert deduplicate([review, dict(review)], seen) == [review]
    assert deduplicate([review], seen) == []


def test_compose_stay_drops_details_reported_in_their_own_fields():
    from booking_crawler.reviews_dom import compose_stay

    stay_info = "Double or Twin Room\n1 night\xa0·\xa0October 2025\nSolo traveller"
    assert compose_stay(stay_info, "Double or Twin Room", "Solo traveller") == (
        "1 night, October 2025"
    )


def test_compose_stay_keeps_everything_when_nothing_is_duplicated():
    from booking_crawler.reviews_dom import compose_stay

    assert compose_stay("2 nights · May 2026", "", "") == "2 nights, May 2026"


def test_strip_ui_text_removes_card_labels():
    from booking_crawler.reviews_dom import strip_ui_text

    assert strip_ui_text("Hotel response: thank you") == "thank you"
    assert strip_ui_text("Property response: thanks") == "thanks"
    assert strip_ui_text("A long review... Continue reading") == "A long review"
    assert strip_ui_text("") == ""


def test_review_signature_prefers_the_api_id():
    first = {"review_id": "abc", "reviewer": "Eric", "pros": "Great"}
    second = {"review_id": "def", "reviewer": "Eric", "pros": "Great"}
    assert review_signature(first) != review_signature(second)


def test_identical_reviews_with_different_ids_are_both_kept():
    seen = set()
    text = {"reviewer": "Anna", "date": "2025-01-01", "score": "10", "pros": "Perfect"}
    kept = deduplicate([{**text, "review_id": "a"}, {**text, "review_id": "b"}], seen)
    assert len(kept) == 2
