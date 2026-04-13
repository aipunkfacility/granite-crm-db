# exporters/markdown.py
import os
from granite.database import Database, EnrichedCompanyRow
from loguru import logger
from granite.exporters.csv import _apply_preset_filter
from granite.utils import sanitize_filename, is_safe_link_url


def _capitalize_city(name: str) -> str:
    """Capitalize city name preserving hyphens (Санкт-Петербург, not Санкт-петербург)."""
    if not name:
        return name
    return "-".join(part.capitalize() for part in name.split("-"))


def _escape_md(text: str) -> str:
    """Экранирование markdown-символов и HTML-тегов для таблиц."""
    if not text:
        return ""
    # Escape HTML tags first (before markdown escaping)
    text = text.replace('<', '&lt;').replace('>', '&gt;')
    # Then escape markdown special characters
    for ch in ('\\', '|', '[', ']', '(', ')', '*', '_', '#', '`', '~'):
        text = text.replace(ch, '\\' + ch)
    return text


def _group_by_segment(records):
    """Группировка записей по сегментам A/B/C/D."""
    segments = {"A": [], "B": [], "C": [], "D": []}
    for r in records:
        seg = r.segment or "D"
        if seg in segments:
            segments[seg].append(r)
        else:
            segments["D"].append(r)
    return segments


def _write_segment_table(f, seg_label: str, seg_records: list):
    """Запись таблицы сегмента в markdown-файл."""
    if not seg_records:
        return
    f.write(f"## Сегмент {seg_label} ({len(seg_records)} шт.)\n\n")
    f.write("| Название | Телефон | Сайт | Telegram | CMS | Score |\n")
    f.write("|----------|---------|------|----------|-----|-------|\n")

    for r in sorted(seg_records, key=lambda x: x.crm_score or 0, reverse=True):
        d = r.to_dict()
        phones = "<br>".join(d.get("phones", []))

        site = d.get("website") or ""
        site = site if is_safe_link_url(site) else None
        site_render = f"[Сайт]({site})" if site else "—"

        tg = d.get("messengers", {}).get("telegram", "")
        tg = tg if is_safe_link_url(tg) else None
        tg_render = f"[TG]({tg})" if tg else "—"

        name = _escape_md(d.get("name", "Unknown"))
        f.write(
            f"| **{name}** | {phones} | {site_render} | {tg_render} | {d.get('cms', '-')} | {d.get('crm_score', 0)} |\n"
        )

    f.write("\n")


class MarkdownExporter:
    """Генератор Markdown-отчетов для Notion/Obsidian."""

    def __init__(self, db: Database, output_dir: str = "data/export"):
        self.db = db
        self.output_dir = output_dir

    def export_city(self, city: str):
        with self.db.session_scope() as session:
            records = session.query(EnrichedCompanyRow).filter_by(city=city).all()
            if not records:
                return

            os.makedirs(self.output_dir, exist_ok=True)
            safe_city = sanitize_filename(city)
            filepath = os.path.join(self.output_dir, f"{safe_city}_report.md")

            segments = _group_by_segment(records)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# База мастерских: {_capitalize_city(city)}\n\n")
                f.write(f"**Всего компаний:** {len(records)}\n\n")

                for seg in ["A", "B", "C", "D"]:
                    _write_segment_table(f, seg, segments[seg])

            logger.info(f"Экспорт Markdown завершен: {filepath}")

    def export_city_with_preset(self, city: str, preset_name: str, preset: dict):
        """Экспорт города с фильтром из пресета config.yaml."""
        with self.db.session_scope() as session:
            query = session.query(EnrichedCompanyRow).filter_by(city=city)
            query = _apply_preset_filter(query, preset_name, preset)
            records = query.all()

            if not records:
                logger.warning(
                    f"Нет данных для экспорта {city} с пресетом '{preset_name}' (markdown)"
                )
                return

            os.makedirs(self.output_dir, exist_ok=True)
            safe_city = sanitize_filename(city)
            filepath = os.path.join(self.output_dir, f"{safe_city}_{preset_name}.md")

            description = preset.get("description", "")
            segments = _group_by_segment(records)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# База мастерских: {_capitalize_city(city)} — {preset_name}\n\n")
                if description:
                    f.write(f"**Фильтр:** {description}\n\n")
                f.write(f"**Всего компаний:** {len(records)}\n\n")

                for seg in ["A", "B", "C", "D"]:
                    _write_segment_table(f, seg, segments[seg])

            logger.info(
                f"Экспорт Markdown (пресет '{preset_name}'): {filepath} ({len(records)} записей)"
            )
