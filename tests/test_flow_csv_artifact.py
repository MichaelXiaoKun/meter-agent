from __future__ import annotations

import pandas as pd

from main import _write_flow_csv_artifact


def test_flow_csv_artifact_is_oldest_first_and_deduped(tmp_path) -> None:
    df = pd.DataFrame(
        {
            "timestamp": [300, 100, 200, 100],
            "flow_rate": [3.0, 1.0, 2.0, 1.0],
            "flow_amount": [30.0, 10.0, 20.0, 10.0],
            "quality": [93.0, 91.0, 92.0, 91.0],
            "extra": ["drop", "drop", "drop", "drop"],
        }
    )

    artifact = _write_flow_csv_artifact(df, str(tmp_path), "BB/TEST", 10, 20)

    assert artifact["kind"] == "csv"
    assert artifact["filename"] == "flow_data_BB_TEST_10_20.csv"
    assert artifact["row_count"] == 3

    out = pd.read_csv(artifact["path"])
    assert list(out.columns) == ["timestamp", "flow_rate", "flow_amount", "quality"]
    assert list(out["timestamp"]) == [100, 200, 300]
    assert list(out["flow_rate"]) == [1.0, 2.0, 3.0]
