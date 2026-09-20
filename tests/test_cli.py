from pathlib import Path

from booking_crawler.cli import _output_path, build_parser, main


def test_rejects_urls_from_other_sites():
    assert main(["https://example.com/hotel"]) == 2


def test_default_output_path_is_slugged_into_results(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _output_path(None, "Hôtel Ámsterdam")
    assert path == Path("results/hotel-amsterdam.txt")
    assert path.parent.is_dir()


def test_explicit_output_path_creates_missing_parents(tmp_path):
    path = _output_path(tmp_path / "nested" / "dir" / "report.txt", "ignored")
    assert path.parent.is_dir()


def test_fast_mode_is_the_default():
    assert build_parser().parse_args(["https://www.booking.com/hotel/x.html"]).mode == "fast"


def test_warns_when_fewer_reviews_came_back_than_expected():
    from booking_crawler.scrape import _warn_if_incomplete

    warnings = []
    reporter = type("R", (), {"warn": lambda self, message: warnings.append(message)})()

    _warn_if_incomplete(reporter, [{}] * 230, {"review_count": "1734"})
    assert "230 of 1734" in warnings[0]

    warnings.clear()
    _warn_if_incomplete(reporter, [{}] * 79, {"review_count": "79"})
    _warn_if_incomplete(reporter, [{}], {"review_count": ""})
    _warn_if_incomplete(reporter, [{}], {"review_count": "lots"})
    assert warnings == []
