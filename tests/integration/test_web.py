from fastapi.testclient import TestClient
from sqlalchemy import Engine

from survey.allowlist import DIMENSION_COLUMNS, MEASURE_COLUMNS, NUMERIC_MEASURES
from survey.api.app import app


def test_meta_lists_allowlist_vocabulary(clean_engine: Engine) -> None:
    # /meta is what the web UI reads instead of hardcoding the allowlist, so the
    # dropdowns and the numeric-vs-categorical gating cannot drift from the
    # server's truth. This guards that contract.
    with TestClient(app) as client:
        body = client.get("/meta").json()

    numeric = {m["id"]: m["numeric"] for m in body["measures"]}
    assert set(numeric) == set(MEASURE_COLUMNS)
    assert {m for m, is_num in numeric.items() if is_num} == set(NUMERIC_MEASURES)
    assert numeric["sentiment_label"] is False  # the categorical guard the UI relies on
    assert set(body["dimensions"]) == set(DIMENSION_COLUMNS)
    assert "city" not in body["dimensions"]  # high-cardinality, intentionally not a dimension
    assert "zip_code" not in body["dimensions"]
    assert isinstance(body["min_reliable_n"], int)


def test_ui_is_served_same_origin(clean_engine: Engine) -> None:
    # The web UI is a read-only API consumer served by the same app (no CORS).
    with TestClient(app) as client:
        root = client.get("/", follow_redirects=False)
        assert root.status_code in (301, 302, 307, 308)
        assert root.headers["location"] == "/ui/"

        page = client.get("/ui/")
        assert page.status_code == 200
        assert "Survey Insights" in page.text
        assert "app.js" in page.text  # the client script is wired up


def test_ui_client_wires_the_upload_path(clean_engine: Engine) -> None:
    # The drag-and-drop / upload feature must actually post the file to /ingest and
    # offer a reset; this pins that the served client carries that wiring so a
    # regression that drops it is caught.
    with TestClient(app) as client:
        script = client.get("/ui/app.js").text
    assert '"/ingest"' in script  # uploads the file body to the ingest endpoint
    assert "/ingest/sample" in script  # reset-to-sample control
    assert "onDrop" in script  # the drag-and-drop handler
