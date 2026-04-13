# pipeline/checkpoint.py
from granite.database import Database, EnrichedCompanyRow, RawCompanyRow, CompanyRow
from loguru import logger


class CheckpointManager:
    """Управление стадиями и возобновлением.
    Смотрит в базу и понимает с какого места продолжить.
    """

    def __init__(self, db: Database):
        self.db = db

    def get_stage(self, city: str) -> str:
        """Определить этап для города.
        Возможные стадии: 'start', 'scraped', 'deduped', 'enriched'
        """
        with self.db.session_scope() as session:
            enriched_count = (
                session.query(EnrichedCompanyRow).filter_by(city=city).count()
            )
            if enriched_count > 0:
                return "enriched"

            dedup_count = session.query(CompanyRow).filter_by(city=city).count()
            if dedup_count > 0:
                return "deduped"

            raw_count = session.query(RawCompanyRow).filter_by(city=city).count()
            if raw_count > 0:
                return "scraped"

            return "start"

    def clear_city(self, city: str):
        """Полная очистка всех данных по городу (при --force)."""
        with self.db.session_scope() as session:
            session.query(EnrichedCompanyRow).filter_by(city=city).delete()
            session.query(RawCompanyRow).filter_by(city=city).delete()
            session.query(CompanyRow).filter_by(city=city).delete()
            logger.info(f"Очищены все данные для города {city}")
