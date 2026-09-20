"""Metadata extraction against a stubbed page — no browser required."""

import asyncio
import json

from booking_crawler.metadata import (
    _ADDRESS_SELECTOR,
    _AMENITIES_SELECTOR,
    _DESCRIPTION_SELECTOR,
    _JSON_LD_SELECTOR,
    _SCORE_SELECTOR,
    _SUBSCORE_SELECTOR,
    extract_metadata,
    json_ld_score,
)
from tests.fakes import FakePage

PROPERTY_BLOCK = {
    "@type": "Hotel",
    "name": "Guest House Shtaka",
    "description": "Located in Gjirokaster.",
    "address": {"streetAddress": "Rruga Gole Gushi, 6001 Gjirokaster, Albania"},
    "aggregateRating": {"ratingValue": 9.6, "reviewCount": 79},
}
ORGANISATION_BLOCK = {"@type": "Organization", "name": "Booking.com"}
BREADCRUMB_BLOCK = {"@type": "BreadcrumbList", "name": "Albania hotels"}


def _page(json_ld_blocks, **fields):
    mapping = {_JSON_LD_SELECTOR: [json.dumps(block) for block in json_ld_blocks]}
    mapping.update(fields)
    return FakePage(mapping)


def test_property_block_wins_even_when_it_is_not_first():
    metadata = asyncio.run(
        extract_metadata(_page([ORGANISATION_BLOCK, BREADCRUMB_BLOCK, PROPERTY_BLOCK]))
    )
    assert metadata["name"] == "Guest House Shtaka"
    assert metadata["property_type"] == "Hotel"
    assert metadata["address"].startswith("Rruga Gole Gushi")
    assert metadata["overall_score"] == "9.6"


def test_json_ld_score_ranks_the_property_above_the_site_blocks():
    assert json_ld_score(PROPERTY_BLOCK) > json_ld_score(ORGANISATION_BLOCK)
    assert json_ld_score({"@type": "Hotel"}) == 0  # no name, unusable


def test_falls_back_to_the_heading_when_json_ld_is_missing_or_broken():
    page = FakePage(
        {
            _JSON_LD_SELECTOR: ["{not json at all", json.dumps(BREADCRUMB_BLOCK)],
            "h1": "Guest House Shtaka (Hotel) (Albania) deals",
            _ADDRESS_SELECTOR: "Rruga Gole Gushi\nAfter booking, all details...",
        }
    )
    metadata = asyncio.run(extract_metadata(page))
    assert metadata["name"] == "Guest House Shtaka"
    assert metadata["property_type"] == "Hotel"
    assert metadata["address"] == "Rruga Gole Gushi"


def test_reads_the_score_widget_and_category_breakdown():
    page = _page(
        [PROPERTY_BLOCK],
        **{
            _SCORE_SELECTOR: "9.6\nExceptional\xa0·\xa079 reviews",
            _SUBSCORE_SELECTOR: [
                "Staff, 9.9, Average rating out of 10\nStaff\n9.9",
                "Location, 8.9, Average rating out of 10\nLocation\n8.9",
                "Categories:",
            ],
            _AMENITIES_SELECTOR: ["Free WiFi", "Bar", "Free WiFi"],
        },
    )
    metadata = asyncio.run(extract_metadata(page))
    assert metadata["score_label"] == "Exceptional"
    assert metadata["review_count"] == "79"
    assert metadata["category_scores"] == {"Staff": "9.9", "Location": "8.9"}
    assert metadata["amenities"] == ["Free WiFi", "Bar"]


def test_page_description_is_preferred_over_the_truncated_json_ld_one():
    page = _page([PROPERTY_BLOCK], **{_DESCRIPTION_SELECTOR: "The full description."})
    assert asyncio.run(extract_metadata(page))["description"] == "The full description."


def test_missing_everything_yields_blank_fields_rather_than_an_error():
    metadata = asyncio.run(extract_metadata(FakePage({})))
    assert metadata["name"] == ""
    assert metadata["category_scores"] == {}
    assert metadata["amenities"] == []
