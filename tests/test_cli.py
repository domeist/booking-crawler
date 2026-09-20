"""Argument handling, URL validation and output paths."""

from pathlib import Path

import pytest

from booking_crawler.cli import _output_path, build_parser, is_booking_url, main


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
    second = _output_path(None, "Гостиница Нева", "https://www.booking.com/hotel/ru/neva.en-gb.html")
    assert first == Path("results/moskva.txt")
    assert second == Path("results/neva.txt")


def test_output_path_of_last_resort():
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
        ["https://www.booking.com/hotel/x.html", "--mode", "standard", "--headless", "-o", "out.txt"]
    )
    assert args.mode == "standard"
    assert args.headless is True
    assert args.output == Path("out.txt")
