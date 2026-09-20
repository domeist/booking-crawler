"""Argument handling, URL validation and output paths."""

from pathlib import Path

import pytest

from booking_crawler import cli
from booking_crawler.cli import _output_path, build_parser, is_booking_url, main
from booking_crawler.errors import ScrapeError


@pytest.mark.parametrize(
    "url",
    [
        "https://www.booking.com/hotel/gb/example.html",
        "http://booking.com/hotel/gb/example.html",
        "https://www.booking.com/hotel/al/x.en-gb.html?sid=1",
    ],
)
def test_accepts_booking_urls(url):
    assert is_booking_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/?ref=booking.com",
        "https://notbooking.com/hotel/x.html",
        "booking.com/hotel/x.html",
        "file:///etc/passwd",
        "",
    ],
)
def test_rejects_everything_else(url):
    assert not is_booking_url(url)


def test_main_exits_2_on_a_url_from_another_site():
    assert main(["https://evil.example/?ref=booking.com"]) == 2


def test_default_output_path_uses_the_property_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _output_path(None, "Hôtel Ámsterdam", "https://www.booking.com/hotel/nl/x.html")
    assert path == Path("results/hotel-amsterdam.txt")
    assert path.parent.is_dir()


def test_default_output_path_falls_back_to_the_url_for_non_latin_names(tmp_path, monkeypatch):
    """Two Cyrillic-named properties must not write to the same file."""
    monkeypatch.chdir(tmp_path)
    first = _output_path(None, "Гостиница Москва", "https://www.booking.com/hotel/ru/moskva.html")
    second = _output_path(
        None, "Гостиница Нева", "https://www.booking.com/hotel/ru/neva.en-gb.html"
    )
    assert first == Path("results/moskva.txt")
    assert second == Path("results/neva.txt")


def test_output_path_of_last_resort(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _output_path(None, "", "").name == "property.txt"


def test_explicit_output_path_is_used_verbatim_and_its_parents_created(tmp_path):
    target = tmp_path / "nested" / "dir" / "report.txt"
    path = _output_path(target, "Ignored Name", "https://www.booking.com/hotel/x.html")
    assert path == target
    assert path.parent.is_dir()


def test_defaults():
    args = build_parser().parse_args(["https://www.booking.com/hotel/x.html"])
    assert args.mode == "fast"
    assert args.headless is False
    assert args.debug is False
    assert args.traceback is False
    assert args.output is None


def test_standard_mode_and_flags_parse():
    args = build_parser().parse_args(
        [
            "https://www.booking.com/hotel/x.html",
            "--mode",
            "standard",
            "--headless",
            "-o",
            "out.txt",
        ]
    )
    assert args.mode == "standard"
    assert args.headless is True
    assert args.output == Path("out.txt")


# --- the happy path, with the scrape itself stubbed out ---------------------

SCRAPED = {
    "url": "https://www.booking.com/hotel/al/example.html",
    "scraped_at": "2026-09-20T12:00:00",
    "metadata": {"name": "Example Hotel", "review_count": "1"},
    "reviews": [{"reviewer": "Ann", "score": "9", "pros": "Lovely"}],
}


@pytest.fixture
def stubbed_scrape(monkeypatch):
    """Run main() without a browser; `outcome` decides what scrape() does."""
    outcome = {"data": SCRAPED, "error": None}

    def fake_run(coro):
        coro.close()
        if outcome["error"]:
            raise outcome["error"]
        return outcome["data"]

    monkeypatch.setattr(cli.asyncio, "run", fake_run)
    return outcome


def test_writes_the_report_and_exits_zero(tmp_path, monkeypatch, stubbed_scrape):
    monkeypatch.chdir(tmp_path)

    assert main(["https://www.booking.com/hotel/al/example.html"]) == 0

    report = tmp_path / "results" / "example-hotel.txt"
    assert "PROPERTY: Example Hotel" in report.read_text(encoding="utf-8")
    assert "Liked: Lovely" in report.read_text(encoding="utf-8")


def test_honours_an_explicit_output_path(tmp_path, stubbed_scrape):
    target = tmp_path / "somewhere" / "report.txt"

    assert main(["https://www.booking.com/hotel/al/example.html", "-o", str(target)]) == 0
    assert target.exists()


def test_a_bad_output_path_is_an_error_message_not_a_traceback(tmp_path, stubbed_scrape):
    """The scrape costs minutes — a write failure must not surface as a crash."""
    directory = tmp_path / "already-a-directory"
    directory.mkdir()

    assert main(["https://www.booking.com/hotel/al/example.html", "-o", str(directory)]) == 1


def test_a_failed_write_leaves_the_previous_report_intact(tmp_path, monkeypatch, stubbed_scrape):
    target = tmp_path / "report.txt"
    target.write_text("previous good report", encoding="utf-8")

    def exploding_write(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(cli, "write_report", exploding_write)

    assert main(["https://www.booking.com/hotel/al/example.html", "-o", str(target)]) == 1
    assert target.read_text(encoding="utf-8") == "previous good report"


def test_a_scrape_failure_exits_one(stubbed_scrape):
    stubbed_scrape["error"] = ScrapeError("bot check")
    assert main(["https://www.booking.com/hotel/al/example.html"]) == 1


def test_an_interrupt_exits_130(stubbed_scrape):
    stubbed_scrape["error"] = KeyboardInterrupt()
    assert main(["https://www.booking.com/hotel/al/example.html"]) == 130


def test_an_unexpected_error_is_summarised_but_re_raised_with_traceback(stubbed_scrape):
    stubbed_scrape["error"] = RuntimeError("something odd")
    assert main(["https://www.booking.com/hotel/al/example.html"]) == 1

    with pytest.raises(RuntimeError):
        main(["https://www.booking.com/hotel/al/example.html", "--traceback"])
