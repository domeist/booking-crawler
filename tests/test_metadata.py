"""Pure parsers behind property metadata extraction."""

from booking_crawler.metadata import (
    MIN_JSON_LD_SCORE,
    clean_property_name,
    json_ld_score,
    metadata_from_json_ld,
    parse_score_block,
    parse_subscore,
)


def test_clean_property_name_strips_the_seo_suffix():
    assert clean_property_name("Guest House Shtaka (Hotel) (Albania) deals") == (
        "Guest House Shtaka",
        "Hotel",
    )


def test_clean_property_name_handles_a_heading_without_a_country():
    assert clean_property_name("Hotel Foo (Hotel) deals") == ("Hotel Foo", "Hotel")


def test_clean_property_name_leaves_a_plain_heading_alone():
    assert clean_property_name("The Grand Hotel") == ("The Grand Hotel", "")


def test_parse_score_block_reads_score_label_and_count():
    assert parse_score_block("9.6\nExceptional\xa0·\xa079 reviews") == {
        "overall_score": "9.6",
        "score_label": "Exceptional",
        "review_count": "79",
    }


def test_parse_score_block_ignores_the_screen_reader_lines():
    text = "Scored 9.6\n9.6\nRated exceptional\nExceptional\n59 reviews"
    assert parse_score_block(text)["score_label"] == "Exceptional"


def test_parse_score_block_handles_empty_input():
    assert parse_score_block("")["overall_score"] == ""


def test_parse_subscore_skips_the_accessibility_label():
    assert parse_subscore("Staff, 9.9, Average rating out of 10\nStaff\n9.9") == ("Staff", "9.9")


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


def test_a_type_given_as_a_list_still_identifies_the_property():
    """Schema.org allows @type to be an array; a stringified list is not a type."""
    payload = {"@type": ["LocalBusiness", "Hotel"], "name": "X", "aggregateRating": {"r": 1}}
    assert metadata_from_json_ld(payload)["property_type"] == "Hotel"
    assert json_ld_score(payload) >= MIN_JSON_LD_SCORE


def test_an_organisation_block_with_an_address_does_not_qualify():
    """Booking.com's own corporate block has a name and an address."""
    payload = {"@type": "Organization", "name": "Booking.com", "address": {"streetAddress": "1 St"}}
    assert json_ld_score(payload) < MIN_JSON_LD_SCORE


def test_a_lodging_block_or_a_rated_block_qualifies():
    assert json_ld_score({"@type": "Hotel", "name": "X"}) >= MIN_JSON_LD_SCORE
    assert json_ld_score({"@type": "Place", "name": "X", "aggregateRating": {"r": 1}}) >= (
        MIN_JSON_LD_SCORE
    )


def test_a_plain_text_address_is_kept():
    payload = {"@type": "Hotel", "name": "Y", "address": "12 High St, London"}
    assert metadata_from_json_ld(payload)["address"] == "12 High St, London"


def test_a_block_without_a_name_is_never_chosen():
    assert json_ld_score({"@type": "Hotel", "aggregateRating": {"r": 1}}) == 0
