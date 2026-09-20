"""Test package.

Making `tests` a package is what lets the test modules import their shared
fakes as `tests.fakes` under the bare `pytest` command, which — unlike
`python -m pytest` — does not put the working directory on sys.path.
"""
