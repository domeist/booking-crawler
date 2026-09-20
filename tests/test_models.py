"""Review identity and deduplication."""

from booking_crawler.models import deduplicate, empty_review, review_signature

TEXT_ONLY = {"reviewer": "Anna", "date": "2025-01-01", "score": "10", "pros": "Perfect"}


def test_empty_review_has_every_field():
    review = empty_review()
    assert review["review_id"] == ""
    assert set(review) >= {"reviewer", "pros", "cons", "score", "reply"}


def test_signature_prefers_the_api_id():
    assert review_signature({**TEXT_ONLY, "review_id": "abc"}) != review_signature(
        {**TEXT_ONLY, "review_id": "def"}
    )


def test_signature_falls_back_to_content_without_an_id():
    assert review_signature(TEXT_ONLY) == review_signature(dict(TEXT_ONLY))
    assert review_signature({**TEXT_ONLY, "pros": "Noisy"}) != review_signature(TEXT_ONLY)


def test_identical_reviews_with_different_ids_are_both_kept():
    kept = deduplicate([{**TEXT_ONLY, "review_id": "a"}, {**TEXT_ONLY, "review_id": "b"}], set())
    assert len(kept) == 2


def test_identical_reviews_without_ids_collapse():
    """Known limit of the page-reading path, which has no id to work with."""
    assert len(deduplicate([dict(TEXT_ONLY), dict(TEXT_ONLY)], set())) == 1


def test_deduplicate_remembers_across_calls():
    seen = set()
    review = {**TEXT_ONLY, "review_id": "a"}
    assert deduplicate([review], seen) == [review]
    assert deduplicate([review], seen) == []
