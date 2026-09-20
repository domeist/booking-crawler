"""Minimal stand-ins for the Playwright objects the extractors touch.

The page and card extractors only ever call locator(), .first, .all() and
inner_text(), so they can be exercised without a browser — which is what makes
the selector-driven code testable at all.
"""

from playwright.async_api import Error as PlaywrightError


class FakeElement:
    def __init__(self, text: str):
        self._text = text

    async def inner_text(self, timeout: int | None = None) -> str:
        return self._text


class MissingElement:
    """What .first gives for a selector that matches nothing."""

    async def inner_text(self, timeout: int | None = None) -> str:
        raise PlaywrightError("Timeout: no element matched")


class FakeLocator:
    def __init__(self, elements):
        self._elements = list(elements)

    @property
    def first(self):
        return self._elements[0] if self._elements else MissingElement()

    async def all(self):
        return list(self._elements)

    async def count(self) -> int:
        return len(self._elements)


class FakeCard:
    """A review card: a mapping of selector -> text."""

    def __init__(self, fields: dict):
        self._fields = fields

    def locator(self, selector: str) -> FakeLocator:
        value = self._fields.get(selector)
        if value is None:
            return FakeLocator([])
        values = value if isinstance(value, list) else [value]
        return FakeLocator([FakeElement(text) for text in values])


class FakePage(FakeCard):
    """A page: the same mapping, plus the handful of page-level calls."""

    def __init__(self, fields: dict, cards=()):
        super().__init__(fields)
        self._cards = list(cards)

    def locator(self, selector: str) -> FakeLocator:
        from booking_crawler.reviews_dom import REVIEW_CARD_SELECTOR

        if selector == REVIEW_CARD_SELECTOR:
            return FakeLocator(self._cards)
        return super().locator(selector)

    async def evaluate(self, script: str, *args):
        return None
