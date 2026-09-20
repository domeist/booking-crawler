"""The review/metadata shapes both scrape modes produce."""

REVIEW_FIELDS = (
    "review_id",
    "reviewer",
    "country",
    "date",
    "title",
    "score",
    "traveller_type",
    "room",
    "stay",
    "pros",
    "cons",
    "reply",
)


def empty_review() -> dict:
    """A review dict with every field present, so callers never need .get()."""
    return {field: "" for field in REVIEW_FIELDS}


def review_signature(review: dict) -> str:
    """Identify a review for deduplication.

    The review API gives each review a stable id, which is exact. Cards read
    from the page have no id, so those fall back to the review's content —
    which does merge the occasional pair of genuinely identical short reviews.
    """
    if review_id := str(review.get("review_id", "")).strip():
        return f"id:{review_id}"
    return "|".join(
        str(review.get(field, "")).strip()
        for field in ("reviewer", "date", "score", "pros", "cons")
    )


def deduplicate(reviews: list[dict], seen: set[str]) -> list[dict]:
    """Return the reviews whose signature is new, recording them in `seen`."""
    fresh = []
    for review in reviews:
        signature = review_signature(review)
        if signature in seen:
            continue
        seen.add(signature)
        fresh.append(review)
    return fresh
