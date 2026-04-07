# exporters/csv.py
import csv
import os
import re
from sqlalchemy import String
from granite.database import Database, EnrichedCompanyRow
from loguru import logger


def _build_csv_row(d: dict) -> dict:
    """Сборка строки CSV из dict компании."""
    messengers = d.get("messengers", {})
    return {
        "id": d["id"],
        "name": d["name"],
        "phones": "; ".join(d.get("phones", [])),
        "address": d.get("address_raw", ""),
        "website": d.get("website", ""),
        "emails": "; ".join(d.get("emails", [])),
        "segment": d.get("segment", ""),
        "crm_score": d.get("crm_score", 0),
        "is_network": "Yes" if d.get("is_network") else "No",
        "cms": d.get("cms", ""),
        "has_marquiz": "Yes" if d.get("has_marquiz") else "No",
        "telegram": messengers.get("telegram", ""),
        "vk": messengers.get("vk", ""),
        "whatsapp": messengers.get("whatsapp", ""),
    }


def _apply_preset_filter(query, preset_name: str, preset: dict):
    """Parse preset filter string and apply ORM filters to the query.

    Maps SQL-like filter conditions from config.yaml to SQLAlchemy ORM filters:
    - "telegram IS NOT NULL" → messengers JSON has "telegram" key
    - "whatsapp IS NOT NULL" → messengers JSON has "whatsapp" key
    - "email IS NOT NULL" → emails column is not empty/null
    - "priority_score >= 50" → crm_score >= 50
    - "has_production = 1" → not in current schema, skip
    - "website_status = 200" → not stored directly, skip
    - "1=1" → all records (full_dump)
    """
    filter_str = preset.get("filters", "")
    if not filter_str or filter_str.strip() == "1=1":
        return query

    conditions = re.split(r"\s+AND\s+", filter_str, flags=re.IGNORECASE)
    for cond in conditions:
        cond = cond.strip()

        # telegram IS NOT NULL
        if re.match(r"telegram\s+IS\s+NOT\s+NULL", cond, re.IGNORECASE):
            query = query.filter(
                EnrichedCompanyRow.messengers.cast(String).contains('"telegram"')
            )

        # whatsapp IS NOT NULL
        elif re.match(r"whatsapp\s+IS\s+NOT\s+NULL", cond, re.IGNORECASE):
            query = query.filter(
                EnrichedCompanyRow.messengers.cast(String).contains('"whatsapp"')
            )

        # email IS NOT NULL
        elif re.match(r"email\s+IS\s+NOT\s+NULL", cond, re.IGNORECASE):
            query = query.filter(
                EnrichedCompanyRow.emails.isnot(None),
                EnrichedCompanyRow.emails != [],  # SQLAlchemy handles JSON comparison
            )

        # priority_score >= N
        elif m_score := re.match(r"priority_score\s*>=\s*(\d+)", cond, re.IGNORECASE):
            threshold = int(m_score.group(1))
            query = query.filter(EnrichedCompanyRow.crm_score >= threshold)

        # has_production = 1 → not in current schema, skip
        elif re.match(r"has_production\s*=\s*1", cond, re.IGNORECASE):
            logger.debug(
                f"Preset '{preset_name}': 'has_production = 1' not in current schema, skipping"
            )

        # website_status = N → not stored directly, skip
        elif re.match(r"website_status\s*=\s*\d+", cond, re.IGNORECASE):
            logger.debug(
                f"Preset '{preset_name}': 'website_status' not stored directly, skipping"
            )

        # has_portrait_service = 0 → not in current schema, skip
        elif re.match(r"has_portrait_service\s*=\s*\d+", cond, re.IGNORECASE):
            logger.debug(
                f"Preset '{preset_name}': 'has_portrait_service' not in current schema, skipping"
            )

        # status != 'contacted' → not in current enriched schema, skip
        elif re.match(r"status\s*!=\s*'?\w+'?", cond, re.IGNORECASE):
            logger.debug(
                f"Preset '{preset_name}': 'status' filter not applicable to enriched table, skipping"
            )

        # telegram IS NULL
        elif re.match(r"telegram\s+IS\s+NULL", cond, re.IGNORECASE):
            query = query.filter(
                ~EnrichedCompanyRow.messengers.cast(String).contains('"telegram"')
            )

        # whatsapp IS NULL
        elif re.match(r"whatsapp\s+IS\s+NULL", cond, re.IGNORECASE):
            query = query.filter(
                ~EnrichedCompanyRow.messengers.cast(String).contains('"whatsapp"')
            )

        else:
            logger.warning(
                f"Preset '{preset_name}': unknown filter condition: '{cond}', skipping"
            )

    return query


class CsvExporter:
    """Экспорт обогащенных данных в CSV."""

    def __init__(self, db: Database, output_dir: str = "data/export"):
        self.db = db
        self.output_dir = output_dir

    def export_city(self, city: str):
        """Экспорт одного города."""
        with self.db.session_scope() as session:
            records = session.query(EnrichedCompanyRow).filter_by(city=city).all()
            if not records:
                logger.warning(f"Нет данных для экспорта {city}")
                return

            os.makedirs(self.output_dir, exist_ok=True)
            filepath = os.path.join(self.output_dir, f"{city.lower()}_enriched.csv")

            fields = [
                "id",
                "name",
                "phones",
                "address",
                "website",
                "emails",
                "segment",
                "crm_score",
                "is_network",
                "cms",
                "has_marquiz",
                "telegram",
                "vk",
                "whatsapp",
            ]

            with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for r in sorted(records, key=lambda x: x.crm_score, reverse=True):
                    writer.writerow(_build_csv_row(r.to_dict()))
            logger.info(f"Экспорт CSV завершен: {filepath}")

    def export_city_with_preset(self, city: str, preset_name: str, preset: dict):
        """Экспорт города с фильтром из пресета config.yaml."""
        with self.db.session_scope() as session:
            query = session.query(EnrichedCompanyRow).filter_by(city=city)
            query = _apply_preset_filter(query, preset_name, preset)
            records = query.all()

            if not records:
                logger.warning(
                    f"Нет данных для экспорта {city} с пресетом '{preset_name}'"
                )
                return

            os.makedirs(self.output_dir, exist_ok=True)
            filepath = os.path.join(
                self.output_dir, f"{city.lower()}_{preset_name}.csv"
            )

            fields = [
                "id",
                "name",
                "phones",
                "address",
                "website",
                "emails",
                "segment",
                "crm_score",
                "is_network",
                "cms",
                "has_marquiz",
                "telegram",
                "vk",
                "whatsapp",
            ]

            with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for r in sorted(records, key=lambda x: x.crm_score, reverse=True):
                    writer.writerow(_build_csv_row(r.to_dict()))
            logger.info(
                f"Экспорт CSV (пресет '{preset_name}'): {filepath} ({len(records)} записей)"
            )
