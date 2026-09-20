"""The parts of browser setup that need no browser."""

import asyncio

from booking_crawler import browser


def test_user_agent_matches_the_running_browser():
    assert "Chrome/153.0.8010.12 Safari" in browser.chrome_user_agent("153.0.8010.12")


def test_user_agent_falls_back_when_the_version_is_unreadable():
    for version in ("", None, "not-a-version"):
        assert browser.FALLBACK_CHROME_VERSION in browser.chrome_user_agent(version)


def test_sandbox_stays_on_for_a_normal_user(monkeypatch):
    monkeypatch.setattr(browser.os, "geteuid", lambda: 1000)
    assert "--no-sandbox" not in browser.launch_args()


def test_sandbox_is_disabled_only_for_root(monkeypatch):
    """Chromium's sandbox cannot start as root, which is the container case."""
    monkeypatch.setattr(browser.os, "geteuid", lambda: 0)
    assert "--no-sandbox" in browser.launch_args()


def test_automation_flag_is_always_masked():
    assert "--disable-blink-features=AutomationControlled" in browser.launch_args()


def test_wait_until_returns_as_soon_as_the_condition_holds():
    calls = []

    def condition():
        calls.append(1)
        return len(calls) >= 2

    assert asyncio.run(browser.wait_until(condition, timeout_s=5, interval_s=0.01)) is True
    assert len(calls) == 2


def test_wait_until_gives_up_and_reports_failure():
    assert asyncio.run(browser.wait_until(lambda: False, timeout_s=0.05, interval_s=0.01)) is False
