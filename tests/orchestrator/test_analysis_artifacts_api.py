"""HTTP tests for authenticated analysis artifact downloads."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient

_root = Path(__file__).resolve().parents[2]
_orch = str(_root / "orchestrator")
if _orch in sys.path:
    sys.path.remove(_orch)
sys.path.insert(0, _orch)


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUEBOT_ANALYSES_DIR", str(tmp_path))
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "artifact_api.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name in list(sys.modules):
        if name in ("api", "store", "agent") or name == "processors" or name.startswith("processors."):
            sys.modules.pop(name, None)
    import api as api_mod  # noqa: WPS433

    importlib.reload(api_mod)
    return TestClient(api_mod.app)


def test_analysis_artifact_download_requires_auth(tmp_path, monkeypatch) -> None:
    (tmp_path / "flow_data_BB1_1_2.csv").write_text("timestamp\n1\n", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)

    response = client.get("/api/analysis-artifacts/flow_data_BB1_1_2.csv")

    assert response.status_code in (401, 422)


def test_analysis_artifact_download_serves_csv_attachment(tmp_path, monkeypatch) -> None:
    filename = "flow_data_BB1_1_2.csv"
    (tmp_path / filename).write_text("timestamp,flow_rate\n1,2.0\n", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)

    response = client.get(
        f"/api/analysis-artifacts/{filename}",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert f'filename="{filename}"' in response.headers["content-disposition"]
    assert response.text == "timestamp,flow_rate\n1,2.0\n"


def test_analysis_artifact_download_rejects_bad_filenames(tmp_path, monkeypatch) -> None:
    (tmp_path / "flow_data_BB1_1_2.txt").write_text("nope", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer test-token"}

    traversal = client.get("/api/analysis-artifacts/..%2Fsecret.csv", headers=headers)
    non_csv = client.get("/api/analysis-artifacts/flow_data_BB1_1_2.txt", headers=headers)
    missing = client.get("/api/analysis-artifacts/missing.csv", headers=headers)

    assert traversal.status_code in (400, 404)
    assert non_csv.status_code == 404
    assert missing.status_code == 404
