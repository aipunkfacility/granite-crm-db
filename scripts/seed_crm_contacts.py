"""SEED: создать crm_contacts для всех companies без CRM-записи.

Запуск: python -m scripts.seed_crm_contacts
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from granite.database import Database, CompanyRow, CrmContactRow
from loguru import logger


def seed_crm_contacts():
    db = Database()
    with db.session_scope() as session:
        from sqlalchemy import text
        result = session.execute(text(
            "INSERT OR IGNORE INTO crm_contacts (company_id, funnel_stage) "
            "SELECT id, 'new' FROM companies "
            "WHERE id NOT IN (SELECT company_id FROM crm_contacts)"
        ))
        count = result.rowcount
        logger.info(f"SEED crm_contacts: создано {count} записей")

    db.engine.dispose()
    return count


if __name__ == "__main__":
    seed_crm_contacts()
