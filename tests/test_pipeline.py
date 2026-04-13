# tests/test_pipeline.py — Тесты пайплайна с моками БД и скреперов
import pytest
from unittest.mock import patch, MagicMock
from granite.database import EnrichedCompanyRow
from granite.exporters.csv import CsvExporter
from granite.exporters.markdown import MarkdownExporter


class TestExporter:
    """Тесты экспортёров."""

    def _make_enriched_row(self, **kwargs):
        """Создаёт mock EnrichedCompanyRow."""
        defaults = {
            "id": 1,
            "name": "Гранит-Мастер",
            "phones": ["79031234567"],
            "address_raw": "ул. Ленина, 10",
            "website": "https://granit.ru",
            "emails": ["info@granit.ru"],
            "city": "Астрахань",
            "messengers": {"telegram": "https://t.me/granit"},
            "tg_trust": {"trust_score": 2},
            "cms": "bitrix",
            "has_marquiz": True,
            "is_network": False,
            "crm_score": 50,
            "segment": "B",
        }
        defaults.update(kwargs)
        row = MagicMock()
        for k, v in defaults.items():
            setattr(row, k, v)
        row.to_dict = lambda: dict(defaults)
        return row

    def test_csv_exporter_writes_file(self, tmp_path):
        """CSVExporter создаёт файл с правильным заголовком."""
        mock_db = MagicMock()
        mock_session = MagicMock()

        row = self._make_enriched_row()
        mock_session.query.return_value.filter_by.return_value.all.return_value = [row]
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session_scope.return_value.__exit__ = MagicMock(return_value=False)

        exporter = CsvExporter(mock_db, output_dir=str(tmp_path))
        exporter.export_city("Астрахань")

        # Проверяем что файл создан
        files = list(tmp_path.glob("*.csv"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8-sig")
        assert "Гранит-Мастер" in content
        assert "79031234567" in content
        assert "telegram" in content
        assert "B" in content

    def test_csv_exporter_empty_data(self, tmp_path):
        """Нет данных — файл не создаётся."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.all.return_value = []
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session_scope.return_value.__exit__ = MagicMock(return_value=False)

        exporter = CsvExporter(mock_db, output_dir=str(tmp_path))
        exporter.export_city("ПустойГород")

        files = list(tmp_path.glob("*.csv"))
        assert len(files) == 0

    def test_csv_exporter_sorted_by_score(self, tmp_path):
        """Записи сортируются по crm_score (убывание)."""
        mock_db = MagicMock()
        mock_session = MagicMock()

        row_a = self._make_enriched_row(id=1, name="CompanyA", crm_score=30)
        row_b = self._make_enriched_row(id=2, name="CompanyB", crm_score=80)
        row_c = self._make_enriched_row(id=3, name="CompanyC", crm_score=50)
        # Возвращаем в произвольном порядке
        mock_session.query.return_value.filter_by.return_value.all.return_value = [row_a, row_b, row_c]
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session_scope.return_value.__exit__ = MagicMock(return_value=False)

        exporter = CsvExporter(mock_db, output_dir=str(tmp_path))
        exporter.export_city("Тест")

        files = list(tmp_path.glob("*.csv"))
        content = files[0].read_text(encoding="utf-8-sig")
        # CompanyB (80) должен быть перед CompanyC (50) перед CompanyA (30)
        pos_b = content.index("CompanyB")
        pos_c = content.index("CompanyC")
        pos_a = content.index("CompanyA")
        assert pos_b < pos_c < pos_a

    def test_markdown_exporter_writes_file(self, tmp_path):
        """MarkdownExporter создаёт файл с таблицей."""
        mock_db = MagicMock()
        mock_session = MagicMock()

        row = self._make_enriched_row()
        mock_session.query.return_value.filter_by.return_value.all.return_value = [row]
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_db.session_scope.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_db.session_scope.return_value.__exit__ = MagicMock(return_value=False)

        exporter = MarkdownExporter(mock_db, output_dir=str(tmp_path))
        exporter.export_city("Астрахань")

        files = list(tmp_path.glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "Гранит-Мастер" in content
        assert "Сегмент B" in content


class TestPresetFilter:
    """Тесты фильтрации по пресетам."""

    def test_apply_preset_filter_full_dump(self):
        """Пресет full_dump (1=1) не фильтрует."""
        from granite.exporters.csv import _apply_preset_filter
        query = MagicMock()
        preset = {"filters": "1=1"}
        result = _apply_preset_filter(query, "full_dump", preset)
        assert result is query  # тот же объект, без вызовов filter

    def test_apply_preset_filter_empty(self):
        """Пустой фильтр — без фильтрации."""
        from granite.exporters.csv import _apply_preset_filter
        query = MagicMock()
        preset = {"filters": ""}
        result = _apply_preset_filter(query, "empty", preset)
        assert result is query

    def test_apply_preset_filter_telegram_not_null(self):
        """Фильтр по telegram IS NOT NULL."""
        from granite.exporters.csv import _apply_preset_filter
        query = MagicMock()
        preset = {"filters": "telegram IS NOT NULL"}
        result = _apply_preset_filter(query, "with_telegram", preset)
        assert query.filter.called

    def test_apply_preset_filter_telegram_is_null(self):
        """Фильтр по telegram IS NULL."""
        from granite.exporters.csv import _apply_preset_filter
        query = MagicMock()
        preset = {"filters": "telegram IS NULL"}
        result = _apply_preset_filter(query, "cold_email", preset)
        assert query.filter.called

    def test_apply_preset_filter_crm_score(self):
        """Фильтр по crm_score >= N."""
        from granite.exporters.csv import _apply_preset_filter
        query = MagicMock()
        preset = {"filters": "crm_score >= 50"}
        result = _apply_preset_filter(query, "hot_leads", preset)
        assert query.filter.called

    def test_apply_preset_filter_segment(self):
        """Фильтр по сегменту."""
        from granite.exporters.csv import _apply_preset_filter
        query = MagicMock()
        preset = {"filters": "segment = 'A'"}
        result = _apply_preset_filter(query, "high_score", preset)
        assert query.filter.called

    def test_apply_preset_filter_emails_plural(self):
        """Фильтр по emails IS NOT NULL (множественное число)."""
        from granite.exporters.csv import _apply_preset_filter
        query = MagicMock()
        preset = {"filters": "emails IS NOT NULL"}
        result = _apply_preset_filter(query, "cold_email", preset)
        assert query.filter.called

    def test_apply_preset_filter_combined_and(self):
        """Комбинированный фильтр с AND."""
        from granite.exporters.csv import _apply_preset_filter
        query = MagicMock()
        # Flask-SQLAlchemy chaining: filter() возвращает query, каждый вызов chained
        query.filter.return_value = query
        preset = {"filters": "telegram IS NOT NULL AND crm_score >= 50"}
        result = _apply_preset_filter(query, "hot_leads", preset)
        # filter вызван дважды (для каждого условия)
        assert query.filter.call_count >= 2

    def test_apply_preset_filter_skips_unknown(self):
        """Неизвестное условие — фильтр пропускается."""
        from granite.exporters.csv import _apply_preset_filter
        query = MagicMock()
        preset = {"filters": "has_production = 1 AND status != 'raw'"}
        result = _apply_preset_filter(query, "producers_only", preset)
        assert not query.filter.called


class TestEnrichedCompanyRow:
    """Тесты ORM-модели."""

    def test_to_dict(self):
        row = EnrichedCompanyRow(
            id=42,
            name="Тест",
            phones=["79031234567"],
            address_raw="ул. Тестовая",
            website="https://test.ru",
            emails=["a@b.ru"],
            city="Тестовск",
            messengers={"telegram": "t.me/test"},
            tg_trust={"trust_score": 1},
            cms="wordpress",
            crm_score=25,
            segment="C",
        )
        d = row.to_dict()
        assert d["id"] == 42
        assert d["name"] == "Тест"
        assert d["phones"] == ["79031234567"]
        assert d["messengers"]["telegram"] == "t.me/test"
        assert d["crm_score"] == 25
        assert "updated_at" in d

    def test_to_dict_empty_collections(self):
        """Пустые коллекции возвращают [] и {}, не None."""
        row = EnrichedCompanyRow(
            id=1, name="Empty",
        )
        d = row.to_dict()
        assert d["phones"] == []
        assert d["emails"] == []
        assert d["messengers"] == {}

    def test_to_dict_includes_updated_at_after_commit(self):
        """updated_at попадает в to_dict() после session.commit()."""
        from granite.database import Database, CompanyRow
        import time
        uid = int(time.time() * 1000) % 10_000_000
        db = Database(auto_migrate=False)
        try:
            with db.session_scope() as session:
                parent = CompanyRow(id=uid, name_best="Updated", city="Тест")
                session.add(parent)
            with db.session_scope() as session:
                company = EnrichedCompanyRow(
                    id=uid, name="Updated", city="Тест", segment="D",
                )
                session.merge(company)
            with db.session_scope() as session:
                row = session.get(EnrichedCompanyRow, uid)
                assert row is not None
                d = row.to_dict()
                assert "updated_at" in d
                assert d["updated_at"] is not None
        finally:
            # Cleanup
            with db.session_scope() as session:
                session.query(EnrichedCompanyRow).filter_by(id=uid).delete()
                session.query(CompanyRow).filter_by(id=uid).delete()
