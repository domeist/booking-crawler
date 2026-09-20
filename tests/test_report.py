from booking_crawler.report import clean_url, format_report, slug


def test_clean_url_strips_session_and_tracking_parameters():
    url = (
        "https://www.booking.com/hotel/al/example.en-gb.html"
        "?sid=abc123&label=gen173nr-10CA&aid=304142#_"
    )
    assert clean_url(url) == "https://www.booking.com/hotel/al/example.en-gb.html"


def test_clean_url_handles_empty_input():
    assert clean_url("") == ""


def test_slug_transliterates_accents():
    assert slug("Hôtel Ámsterdam") == "hotel-amsterdam"


def test_slug_falls_back_when_nothing_usable_remains():
    assert slug("...") == "property"
    assert slug("") == "property"


def _report(**overrides):
    data = {
        "url": "https://www.booking.com/hotel/al/example.html?sid=secret",
        "scraped_at": "2026-09-19T12:30:00",
        "metadata": {
            "name": "Guest House Shtaka",
            "property_type": "Hotel",
            "address": "Rruga Gole Gushi, Gjirokaster, Albania",
            "overall_score": "9.6",
            "score_label": "Exceptional",
            "review_count": "79",
            "category_scores": {"Staff": "9.9", "Location": "8.9"},
            "amenities": ["Free WiFi", "Bar"],
            "description": "A guest house.",
        },
        "reviews": [
            {
                "reviewer": "Eric",
                "country": "United States",
                "date": "2025-10-03",
                "title": "Excellent value",
                "score": "10",
                "traveller_type": "Solo traveller",
                "room": "Double Room",
                "stay": "1 night, October 2025",
                "pros": "Spotless clean.",
                "cons": "Needs a handrail.",
                "reply": "Thank you!",
            }
        ],
    }
    data.update(overrides)
    return format_report(data)


def test_report_contains_metadata_and_review_details():
    text = _report()
    assert "PROPERTY: Guest House Shtaka" in text
    assert "Type: Hotel" in text
    assert "Address: Rruga Gole Gushi, Gjirokaster, Albania" in text
    assert "Overall score: 9.6 (Exceptional) from 79 reviews" in text
    assert "  Staff: 9.9" in text
    assert "REVIEWS (1 total)" in text
    assert "Eric, United States, 2025-10-03" in text
    assert "Stay: Solo traveller | Double Room | 1 night, October 2025" in text
    assert "Liked: Spotless clean." in text
    assert "Property replied: Thank you!" in text


def test_report_never_leaks_the_session_id():
    assert "sid=secret" not in _report()


def test_report_omits_fields_that_are_missing():
    text = _report(
        metadata={"name": "Tiny Inn"},
        reviews=[{"score": "8", "pros": "Fine"}],
    )
    assert "Address:" not in text
    assert "Score breakdown:" not in text
    assert "Disliked:" not in text
    assert "Score: 8" in text


def test_report_handles_zero_reviews():
    assert "REVIEWS (0 total)" in _report(reviews=[])
