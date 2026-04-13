# pipeline/export_phase.py
"""Фаза 6: автоматический экспорт в CSV + пресеты.

Вынесено из PipelineManager — экспорт не зависит от скрапинга/обогащения
и может запускаться отдельно.
"""
from loguru import logger
from granite.database import Database
from granite.exporters.csv import CsvExporter
from granite.exporters.markdown import MarkdownExporter
from granite.pipeline.status import print_status
from granite.utils import sanitize_filename

__all__ = ["ExportPhase"]


class ExportPhase:
    """Автоэкспорт результатов пайплайна."""

    def __init__(self, config: dict, db: Database):
        self.config = config
        self.db = db

    def run(self, city: str) -> None:
        """Запустить экспорт: базовый CSV + пресеты из config.yaml."""
        self._export_csv(city)
        self._export_presets(city)

    def _export_csv(self, city: str) -> None:
        """Базовый CSV-экспорт enriched данных города."""
        print_status("ФАЗА 6: Экспорт CSV", "info")
        try:
            exporter = CsvExporter(self.db)
            exporter.export_city(city)
            safe_city = sanitize_filename(city)
            print_status(f"Экспорт завершён: data/export/{safe_city}_enriched.csv", "success")
        except Exception as e:
            logger.error(f"Ошибка экспорта для {city}: {e}")
            print_status(f"Экспорт не удался: {e}", "warning")

    def _export_presets(self, city: str) -> None:
        """Экспорт по пресетам из config.yaml (export_presets)."""
        export_presets = self.config.get("export_presets", {})
        if not export_presets:
            return

        print_status(f"Экспорт пресетов: {len(export_presets)} шт.", "info")
        for preset_name, preset in export_presets.items():
            try:
                preset_format = preset.get("format", "csv")
                if preset_format in ("markdown", "md"):
                    md_exporter = MarkdownExporter(self.db)
                    md_exporter.export_city_with_preset(city, preset_name, preset)
                else:
                    csv_exporter = CsvExporter(self.db)
                    csv_exporter.export_city_with_preset(city, preset_name, preset)
                print_status(f"  Пресет '{preset_name}': OK", "success")
            except Exception as e:
                logger.error(f"Ошибка экспорта пресета '{preset_name}' для {city}: {e}")
                print_status(f"  Пресет '{preset_name}': ошибка — {e}", "warning")
