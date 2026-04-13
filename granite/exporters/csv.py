# exporters/csv.py
import csv
import os
import re
from sqlalchemy import String
from granite.database import Database, EnrichedCompanyRow
from granite.utils import sanitize_filename
from loguru import logger


_CSV_FIELDS = [
    "id", "name", "phones", "address", "website", "emails",
    "segment", "crm_score", "is_network", "cms", "has_marquiz",
    "telegram", "vk", "whatsapp",
]


def _build_csv_row(d: dict) -> dict:
    """Сборка строки CSV из dict компании."""
    messengers = d.get("messengers", {})
    return {
        "id": d.get("id", ""),
        "name": d.get("name", ""),
        "phones": "; ".join(d.get("phones", [])),
        "address": d.get("address_raw", ""),
        "website": d.get("website", ""),
        "emails": "; ".join(d.get("emails", [])),
        "segment": d.get("segment", ""),
        "crm_score": d.get("crm_score") or 0,
        "is_network": "Yes" if d.get("is_network") else "No",
        "cms": d.get("cms", ""),
        "has_marquiz": "Yes" if d.get("has_marquiz") else "No",
        "telegram": messengers.get("telegram", ""),
        "vk": messengers.get("vk", ""),
        "whatsapp": messengers.get("whatsapp", ""),
    }


def _apply_preset_filter(query, preset_name: str, preset: dict):
    """Parse preset filter string and apply ORM filters to the query.

    Maps SQL-like filter conditions from config.yaml to SQLAlchemy ORM filters.
    Uses a dispatch table instead of sequential regex matching.
    """
    filter_str = preset.get("filters", "")
    if not filter_str or filter_str.strip() == "1=1":
        return query

    conditions = re.split(r"\s+AND\s+", filter_str, flags=re.IGNORECASE)

    # Lookup table: (regex_pattern) -> handler(query, match) -> query
    def _filter_messenger_has(messenger_key: str):
        """Returns a handler for IS NOT NULL on a messenger key."""
        def handler(q, _m):
            return q.filter(
                EnrichedCompanyRow.messengers.cast(String).contains(f'"{messenger_key}"')
            )
        return handler

    def _filter_messenger_null(messenger_key: str):
        """Returns a handler for IS NULL on a messenger key."""
        def handler(q, _m):
            return q.filter(
                ~EnrichedCompanyRow.messengers.cast(String).contains(f'"{messenger_key}"')
            )
        return handler

    _FILTER_TABLE = [
        # Messengers
        (r"telegram\s+IS\s+NOT\s+NULL", _filter_messenger_has("telegram")),
        (r"whatsapp\s+IS\s+NOT\s+NULL", _filter_messenger_has("whatsapp")),
        (r"vk\s+IS\s+NOT\s+NULL", _filter_messenger_has("vk")),
        (r"telegram\s+IS\s+NULL", _filter_messenger_null("telegram")),
        (r"whatsapp\s+IS\s+NULL", _filter_messenger_null("whatsapp")),
        (r"vk\s+IS\s+NULL", _filter_messenger_null("vk")),
        # Emails
        (r"emails?\s+IS\s+NOT\s+NULL", lambda q, _m: q.filter(
            EnrichedCompanyRow.emails.isnot(None),
            EnrichedCompanyRow.emails.cast(String) != "[]",
            EnrichedCompanyRow.emails.cast(String) != "",
        )),
        # Score
        (r"crm_score\s*>=\s*(\d+)", lambda q, m: q.filter(
            EnrichedCompanyRow.crm_score >= int(m.group(1))
        )),
        (r"crm_score\s*<=\s*(\d+)", lambda q, m: q.filter(
            EnrichedCompanyRow.crm_score <= int(m.group(1))
        )),
        (r"crm_score\s*=\s*(\d+)", lambda q, m: q.filter(
            EnrichedCompanyRow.crm_score == int(m.group(1))
        )),
        # Segment
        (r"segment\s*=\s*'?([A-D])'/?", lambda q, m: q.filter(
            EnrichedCompanyRow.segment == m.group(1)
        )),
    ]

    # Unsupported conditions — log and skip
    _SKIP_PATTERNS = [
        r"has_production\s*=\s*\d+",
        r"website_status\s*=\s*\d+",
        r"has_portrait_service\s*=\s*\d+",
        r"status\s*!=\s*'?\w+'?",
    ]

    for cond in conditions:
        cond = cond.strip()
        matched = False

        for pattern, handler in _FILTER_TABLE:
            m = re.match(pattern, cond, re.IGNORECASE)
            if m:
                query = handler(query, m)
                matched = True
                break

        if not matched:
            for skip_pat in _SKIP_PATTERNS:
                if re.match(skip_pat, cond, re.IGNORECASE):
                    logger.warning(
                        f"Preset '{preset_name}': '{cond}' not in current schema, skipping"
                    )
                    matched = True
                    break

        if not matched:
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
            filepath = os.path.join(self.output_dir, f"{sanitize_filename(city)}_enriched.csv")

            fields = _CSV_FIELDS

            with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for r in sorted(records, key=lambda x: x.crm_score or 0, reverse=True):
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
                self.output_dir, f"{sanitize_filename(city)}_{preset_name}.csv"
            )

            fields = _CSV_FIELDS

            with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for r in sorted(records, key=lambda x: x.crm_score or 0, reverse=True):
                    writer.writerow(_build_csv_row(r.to_dict()))
            logger.info(
                f"Экспорт CSV (пресет '{preset_name}'): {filepath} ({len(records)} записей)"
            )
