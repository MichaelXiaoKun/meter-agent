from __future__ import annotations

import pandas as pd

import adaptive_fetch


def _df(points: int, *, start: int = 0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [start + i for i in range(points)],
            "flow_rate": [1.0] * points,
            "flow_amount": [0.0] * points,
            "quality": [100.0] * points,
        }
    )


def test_fetch_for_analysis_expands_until_adequate(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []
    frames = [_df(5), _df(12)]

    def fake_fetch(serial, start, end, token=None, verbose=False):
        calls.append((start, end))
        return frames[len(calls) - 1]

    monkeypatch.setattr(adaptive_fetch, "fetch_flow_data_range", fake_fetch)

    df, adequacy, history = adaptive_fetch.fetch_for_analysis(
        "BB1",
        100,
        {"min_points": 10, "ideal_points": 20, "max_gap_pct": 100},
        initial_window_seconds=10,
        max_retries=2,
        max_window_seconds=100,
        token="tok",
    )

    assert len(df) == 12
    assert adequacy["ok"] is True
    assert [h["window_seconds"] for h in history] == [10, 20]
    assert calls == [(90, 100), (80, 100)]


def test_fetch_for_analysis_returns_last_failure_at_retry_limit(monkeypatch) -> None:
    calls = 0

    def fake_fetch(serial, start, end, token=None, verbose=False):
        nonlocal calls
        calls += 1
        return _df(3)

    monkeypatch.setattr(adaptive_fetch, "fetch_flow_data_range", fake_fetch)

    df, adequacy, history = adaptive_fetch.fetch_for_analysis(
        "BB1",
        100,
        {"min_points": 10, "ideal_points": 20, "max_gap_pct": 100},
        initial_window_seconds=10,
        max_retries=1,
        max_window_seconds=100,
        token="tok",
    )

    assert len(df) == 3
    assert adequacy["ok"] is False
    assert len(history) == 2
    assert calls == 2


def test_fetch_for_analysis_stops_when_window_cap_reached(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []

    def fake_fetch(serial, start, end, token=None, verbose=False):
        calls.append((start, end))
        return _df(3)

    monkeypatch.setattr(adaptive_fetch, "fetch_flow_data_range", fake_fetch)

    _, adequacy, history = adaptive_fetch.fetch_for_analysis(
        "BB1",
        100,
        {"min_points": 10, "ideal_points": 20, "max_gap_pct": 100},
        initial_window_seconds=100,
        max_retries=5,
        max_window_seconds=100,
        token="tok",
    )

    assert adequacy["ok"] is False
    assert len(history) == 1
    assert calls == [(0, 100)]
