from __future__ import annotations

import pandas as pd

import data_client


def _frame(rows: list[tuple[int, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [r[0] for r in rows],
            "flow_rate": [r[1] for r in rows],
            "flow_amount": [0.0] * len(rows),
            "quality": [100.0] * len(rows),
        }
    )


def test_parallel_fetch_preserves_serial_result_shape(monkeypatch) -> None:
    responses = {
        (0, 3600): _frame([(3, 30.0), (1, 10.0)]),
        (3601, 7201): data_client._empty_flow_dataframe(),
        (7202, 10800): _frame([(7202, 70.0), (1, 999.0), (9000, 90.0)]),
    }

    def fake_fetch(serial, range_start, range_end, token=None, verbose=False):
        return responses[(range_start, range_end)]

    monkeypatch.setattr(data_client, "fetch_flow_data", fake_fetch)

    monkeypatch.setenv("BLUEBOT_FLOW_FETCH_WORKERS", "1")
    serial_df = data_client.fetch_flow_data_range("BB1", 0, 10800, token="tok", verbose=False)

    monkeypatch.setenv("BLUEBOT_FLOW_FETCH_WORKERS", "4")
    parallel_df, meta = data_client.fetch_flow_data_range(
        "BB1",
        0,
        10800,
        token="tok",
        verbose=False,
        return_metadata=True,
    )

    assert parallel_df.to_dict("records") == serial_df.to_dict("records")
    assert list(parallel_df["timestamp"]) == [1, 3, 7202, 9000]
    assert meta["chunk_count"] == 3
    assert meta["fetch_workers"] == 3
    assert isinstance(meta["fetch_elapsed_seconds"], float)
