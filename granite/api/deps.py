"""FastAPI зависимости: сессия БД из app.state (инициализирована в lifespan)."""
from typing import Generator

from fastapi import Request
from sqlalchemy.orm import Session

__all__ = ["get_db"]


def get_db(request: Request) -> Generator[Session, None, None]:
    """Dependency: открывает сессию БД и закрывает после запроса."""
    session = request.app.state.Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
