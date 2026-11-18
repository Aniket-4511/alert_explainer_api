from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from main import app


ALERT_PAYLOAD = {
    "title": "High CPU usage",
    "severity": "critical",
    "source": "monitoring",
    "metric_name": "cpu_usage",
    "metric_value": 95.0,
    "threshold": 80.0,
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    def override_get_db() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    try:
        Base.metadata.create_all(bind=engine)
        with monkeypatch.context() as overrides:
            overrides.setitem(app.dependency_overrides, get_db, override_get_db)
            with TestClient(app) as test_client:
                yield test_client
    finally:
        engine.dispose()


def test_create_alert(client: TestClient) -> None:
    response = client.post("/alerts/", json=ALERT_PAYLOAD)

    assert response.status_code == 201
    alert = response.json()
    assert isinstance(alert["id"], int)
    assert {field: alert[field] for field in ALERT_PAYLOAD} == ALERT_PAYLOAD


def test_list_alerts(client: TestClient) -> None:
    empty_response = client.get("/alerts/")
    assert empty_response.status_code == 200
    assert empty_response.json() == []

    created = client.post("/alerts/", json=ALERT_PAYLOAD)
    assert created.status_code == 201

    response = client.get("/alerts/")

    assert response.status_code == 200
    assert response.json() == [created.json()]


def test_get_alert_by_id(client: TestClient) -> None:
    created = client.post("/alerts/", json=ALERT_PAYLOAD)
    assert created.status_code == 201
    alert = created.json()

    response = client.get(f"/alerts/{alert['id']}")

    assert response.status_code == 200
    assert response.json() == alert


def test_explain_alert(client: TestClient) -> None:
    created = client.post("/alerts/", json=ALERT_PAYLOAD)
    assert created.status_code == 201
    alert = created.json()

    response = client.post(f"/alerts/{alert['id']}/explain")

    assert response.status_code == 200
    result = response.json()
    assert result["alert_id"] == alert["id"]
    assert alert["severity"] in result["explanation"]
    assert alert["metric_name"] in result["explanation"]
    assert datetime.fromisoformat(result["generated_at"]).utcoffset() is not None


def _create_alert(client: TestClient, **overrides: str | float) -> dict:
    """Create an alert through the API and return its JSON response."""
    payload: dict[str, str | float] = {
        "title": "Test alert",
        "severity": "warning",
        "source": "test-source",
        "metric_name": "test_metric",
        "metric_value": 50.0,
        "threshold": 40.0,
    }
    payload.update(overrides)
    response = client.post("/alerts", json=payload)
    assert response.status_code == 201
    return response.json()


def test_explain_alert_for_nonexistent_id_returns_404(client: TestClient) -> None:
    response = client.post("/alerts/999/explain")

    assert response.status_code == 404
    assert response.json()["detail"] == "Alert not found"


def test_explain_alert_when_metric_is_below_threshold(
    client: TestClient,
) -> None:
    alert = _create_alert(
        client,
        title="Connection pool below minimum",
        severity="warning",
        source="db-pool",
        metric_name="active_connections",
        metric_value=2,
        threshold=10,
    )

    response = client.post(f"/alerts/{alert['id']}/explain")

    assert response.status_code == 200
    assert "fell below its threshold" in response.json()["explanation"]


def test_explain_alert_with_zero_threshold_does_not_crash(
    client: TestClient,
) -> None:
    alert = _create_alert(
        client,
        title="Unexpected traffic on idle endpoint",
        severity="info",
        source="edge-cache",
        metric_name="request_count",
        metric_value=5,
        threshold=0,
    )

    response = client.post(f"/alerts/{alert['id']}/explain")

    assert response.status_code == 200
    assert "threshold of 0.0" in response.json()["explanation"]