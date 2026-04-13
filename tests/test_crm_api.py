"""Smoke-тесты для CRM API."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def client():
    """Создать тестовый клиент FastAPI с in-memory SQLite.

    Dependency override: get_db направляет все запросы в in-memory БД.
    """
    from granite.api.app import app
    from granite.api.deps import get_db
    from granite.database import Base, CrmTemplateRow

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, conn_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)

    # Verify tables exist
    _tables = inspect(engine).get_table_names()
    assert "crm_contacts" in _tables, f"Missing crm_contacts in {_tables}"
    assert "crm_email_campaigns" in _tables, f"Missing crm_email_campaigns in {_tables}"
    assert "crm_templates" in _tables, f"Missing crm_templates in {_tables}"

    TestSession = sessionmaker(bind=engine)

    # Seed templates
    with TestSession() as s:
        s.add(CrmTemplateRow(
            name="cold_email_1", channel="email",
            subject="Test", body="Hello {from_name}",
        ))
        s.add(CrmTemplateRow(
            name="tg_intro", channel="tg",
            subject="", body="Hi {from_name}",
        ))
        s.commit()

    def get_test_db():
        session = TestSession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    engine.dispose()


class TestHealthEndpoint:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_funnel_empty(self, client):
        r = client.get("/api/v1/funnel")
        assert r.status_code == 200
        data = r.json()
        assert data["new"] == 0
        assert "email_sent" in data
        # All 9 stages present
        assert len(data) == 9

    def test_campaigns_list_empty(self, client):
        r = client.get("/api/v1/campaigns")
        assert r.status_code == 200
        assert r.json() == []


class TestValidation:
    def test_touch_invalid_channel(self, client):
        r = client.post("/api/v1/companies/1/touches", json={"channel": "fax"})
        assert r.status_code == 422  # Pydantic validation

    def test_update_invalid_stage(self, client):
        r = client.patch("/api/v1/companies/1", json={"funnel_stage": "banana"})
        assert r.status_code == 422

    def test_send_invalid_channel(self, client):
        r = client.post("/api/v1/companies/1/send", json={"channel": "sms"})
        assert r.status_code == 422

    def test_task_invalid_priority(self, client):
        r = client.post("/api/v1/companies/1/tasks", json={"priority": "urgent"})
        assert r.status_code == 422
