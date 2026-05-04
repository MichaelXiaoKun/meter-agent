"""Static and generated artifact routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse

from .. import app as app_runtime

router = APIRouter(tags=["artifacts"])


@router.get("/api/logo")
def get_logo():
    """Serve the bluebot logo."""
    if not app_runtime._LOGO_PATH.exists():
        raise HTTPException(404, "Logo not found")
    return FileResponse(app_runtime._LOGO_PATH, media_type="image/jpeg")


@router.get("/api/plots/{filename}")
def get_plot(filename: str):
    """Serve a generated plot PNG by filename."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    path = (app_runtime._PLOTS_DIR / filename).resolve()
    try:
        path.relative_to(app_runtime._PLOTS_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid filename") from None
    if not path.is_file() or path.suffix.lower() != ".png":
        app_runtime.logger.warning(
            "Plot not found: %s (PLOTS_DIR=%s). "
            "Common causes: Railway scaled to >1 replica (plots are local disk per instance), "
            "redeploy cleared ephemeral files, or the browser requested a filename from markdown "
            "that does not match saved files.",
            path,
            app_runtime._PLOTS_DIR,
        )
        raise HTTPException(404, "Plot not found")
    return FileResponse(path, media_type="image/png")


@router.get("/api/analysis-artifacts/{filename}")
def get_analysis_artifact(filename: str, authorization: str = Header(...)):
    """Serve a generated analysis artifact by filename."""
    app_runtime._bearer_token(authorization)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    path = (app_runtime._ANALYSES_DIR / filename).resolve()
    try:
        path.relative_to(app_runtime._ANALYSES_DIR)
    except ValueError:
        raise HTTPException(400, "Invalid filename") from None
    if not path.is_file() or path.suffix.lower() != ".csv":
        raise HTTPException(404, "Analysis artifact not found")
    return FileResponse(
        path,
        media_type="text/csv",
        filename=filename,
    )
