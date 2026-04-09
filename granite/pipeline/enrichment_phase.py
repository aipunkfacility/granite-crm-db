# pipeline/enrichment_phase.py
"""Фаза 3: обогащение данных компании (сайт-сканирование, Telegram, веб-поиск).

Вынесено из PipelineManager — самая сложная фаза пайплайна,
требующая отдельного тестирования и изоляции.
"""

from loguru import logger
from granite.database import Database, CompanyRow, EnrichedCompanyRow
from granite.pipeline.status import print_status
from granite.pipeline.web_client import WebClient
from granite.pipeline.region_resolver import RegionResolver
from granite.utils import normalize_phone, normalize_phones

# Import Enrichers
from granite.enrichers.messenger_scanner import MessengerScanner
from granite.enrichers.tech_extractor import TechExtractor
from granite.enrichers.tg_finder import find_tg_by_phone, find_tg_by_name
from granite.enrichers.tg_trust import check_tg_trust
from granite.dedup.validator import validate_website


class EnrichmentPhase:
    """Обогащение: мессенджеры, Telegram, CMS, точечный веб-поиск."""

    def __init__(self, config: dict, db: Database, web_client: WebClient):
        """
        Args:
            config: словарь конфигурации (config.yaml).
            db: экземпляр Database.
            web_client: экземпляр WebClient.
        """
        self.config = config
        self.db = db
        self.web = web_client
        self._resolver = RegionResolver(config)

    def run(self, city: str, only_new: bool = False) -> int:
        """Основной проход обогащения для города.

        Args:
            city: название города.
            only_new: если True — только компании без enriched-записи.

        Returns:
            Количество обогащённых компаний.
        """
        print_status("ФАЗА 3: Обогащение данных (Enrichment)", "info")

        with self.db.session_scope() as session:
            if only_new:
                # SQL subquery: NOT IN (SELECT id FROM enriched_companies WHERE city=...)
                enriched_ids = session.query(EnrichedCompanyRow.id).filter_by(city=city).subquery()
                companies = session.query(CompanyRow).filter(
                    CompanyRow.city == city, CompanyRow.id.notin_(enriched_ids)
                ).all()

                # Подсчёт enriched для информационного сообщения
                enriched_count = session.query(EnrichedCompanyRow.id).filter_by(city=city).count()

                if not companies:
                    print_status("Нет новых компаний для обогащения", "info")
                    return 0
                print_status(
                    f"Новых компаний: {len(companies)} (всего enriched: {enriched_count})",
                    "info",
                )
            else:
                companies = session.query(CompanyRow).filter_by(city=city).all()

            scanner = MessengerScanner(self.config)
            tech_ext = TechExtractor(self.config)

            count = self._enrich_companies(session, companies, scanner, tech_ext)
            print_status(f"Обогащение завершено для {count} компаний", "success")

            # ПРОХОД 2: точечный поиск недостающих данных через веб
            self._run_deep_enrich_for(
                session, companies, city, scanner, tech_ext, search_best_url=False
            )

            return count

    def run_deep_enrich_existing(self, city: str) -> int:
        """Точечный поиск для уже обогащённых компаний (--re-enrich).

        Заполняет пустые website/email через веб-поиск.

        Returns:
            Количество дополненных компаний.
        """
        print_status(
            "Точечный поиск недостающих данных (существующие компании)", "info"
        )

        with self.db.session_scope() as session:
            all_enriched = session.query(EnrichedCompanyRow).filter_by(city=city).all()
            needs_deep = [e for e in all_enriched if not e.website or not e.emails]

            if not needs_deep:
                print_status(
                    "Все компании уже с сайтами/email — нечего дополнять", "info"
                )
                return 0

            print_status(
                f"Компаний для точечного поиска: {len(needs_deep)}/{len(all_enriched)}",
                "info",
            )

            if not self._resolver.is_source_enabled("web_search"):
                print_status("Веб-поиск отключён — точечный поиск пропущен", "warning")
                return 0

            scanner = MessengerScanner(self.config)
            tech_ext = TechExtractor(self.config)

            return self._run_deep_enrich_for(
                session,
                needs_deep,
                city,
                scanner,
                tech_ext,
                search_best_url=True,
                name_attr="name",
            )

    def _enrich_companies(self, session, companies: list, scanner, tech_ext) -> int:
        """Основной цикл обогащения: мессенджеры, Telegram, траст, CMS.

        Запускается внутри внешнего session_scope, поэтому не управляет сессией.
        Использует session.flush() вместо session.commit() для батчей —
        финальный commit делает session_scope при успешном выходе.
        """
        count = 0
        for c in companies:
            try:
                erow = EnrichedCompanyRow(
                    id=c.id,
                    name=c.name_best,
                    phones=c.phones,
                    address_raw=c.address,
                    website=c.website,
                    emails=c.emails,
                    city=c.city,
                )

                messengers = dict(c.messengers) if c.messengers else {}

                # 1. Сканирование сайта
                if c.website:
                    valid_url, status = validate_website(c.website)
                    erow.website = valid_url
                    if valid_url and status == 200:
                        site_data = scanner.scan_website(valid_url)
                        # Мессенджеры
                        for k, v in site_data.items():
                            if not k.startswith("_") and k not in messengers:
                                messengers[k] = v

                        # Email из сайта
                        site_emails = site_data.get("_emails", [])
                        if site_emails:
                            existing_emails = set(erow.emails or [])
                            for em in site_emails:
                                existing_emails.add(em)
                            erow.emails = list(existing_emails)

                        # Телефоны из сайта
                        site_phones = site_data.get("_phones", [])
                        if site_phones:
                            erow.phones = normalize_phones(
                                (erow.phones or []) + site_phones
                            )

                        tech = tech_ext.extract(valid_url)
                        erow.cms = tech.get("cms", "unknown")
                        erow.has_marquiz = tech.get("has_marquiz", False)

                # 2. Поиск Telegram
                if "telegram" not in messengers:
                    if c.phones:
                        tg = find_tg_by_phone(c.phones[0], self.config)
                        if tg:
                            messengers["telegram"] = tg

                    if "telegram" not in messengers:
                        tg = find_tg_by_name(
                            c.name_best, c.phones[0] if c.phones else None, self.config
                        )
                        if tg:
                            messengers["telegram"] = tg

                # 3. Анализ Telegram (Траст)
                tg_trust = {}
                if "telegram" in messengers:
                    tg_trust = check_tg_trust(messengers["telegram"])

                erow.messengers = messengers
                erow.tg_trust = tg_trust

                session.merge(erow)
                if count % 50 == 49:
                    session.flush()
                count += 1

                parts = []
                if erow.messengers:
                    parts.append(f"мессенджеры: {', '.join(erow.messengers.keys())}")
                if erow.emails:
                    parts.append(f"email: {len(erow.emails)}")
                if erow.cms:
                    parts.append(f"cms: {erow.cms}")
                detail = " | ".join(parts) if parts else "нет данных"
                print_status(
                    f"Обогащено: {count}/{len(companies)} — {c.name_best} ({detail})"
                )
            except Exception as e:
                logger.error(f"Ошибка обогащения {c.name_best}: {e}")

        # Flush оставшиеся записи; финальный commit — через session_scope
        session.flush()
        return count

    def _run_deep_enrich_for(
        self,
        session,
        records: list,
        city: str,
        scanner,
        tech_ext,
        search_best_url: bool = False,
        name_attr: str = "name_best",
    ) -> int:
        """Единый метод точечного поиска через веб.

        Объединяет бывшие _run_phase_deep_enrich и _run_phase_deep_enrich_existing,
        различающиеся только источником данных и флагом search_best_url.

        Args:
            session: открытая сессия БД.
            records: список CompanyRow (основной проход) или EnrichedCompanyRow (re-enrich).
            city: название города.
            scanner: MessengerScanner.
            tech_ext: TechExtractor.
            search_best_url: искать лучший URL по названию или брать первый.
            name_attr: атрибут записи с названием ("name_best" для CompanyRow,
                       "name" для EnrichedCompanyRow).

        Returns:
            Количество дополненных компаний.
        """
        # Фильтруем: нет сайта ИЛИ нет email
        needs_deep = []
        for r in records:
            has_site = bool(r.website)
            has_email = bool(r.emails)
            if not has_site or not has_email:
                needs_deep.append(r)

        if not needs_deep:
            print_status(
                "Все компании уже с сайтами/email — точечный поиск не нужен", "info"
            )
            return 0

        total_msg = (
            f"Точечный поиск: {len(needs_deep)} компаний без сайта или email"
            if name_attr == "name_best"
            else f"Компаний для точечного поиска: {len(needs_deep)}"
        )
        print_status(total_msg, "info")

        if not self._resolver.is_source_enabled("web_search"):
            print_status("Веб-поиск отключён — точечный поиск пропущен", "warning")
            return 0

        found = 0
        for i, record in enumerate(needs_deep, 1):
            try:
                company_name = getattr(record, name_attr, None) or getattr(record, "name_best", None) or getattr(record, "name", "")
                if not company_name or not company_name.strip():
                    logger.debug(f"  Пропуск: пустое название компании (id={record.id})")
                    continue
                query = f"{company_name} {city}"

                erow = session.get(EnrichedCompanyRow, record.id)
                if not erow:
                    continue

                updated = self._deep_enrich_company(
                    session,
                    erow,
                    company_name,
                    city,
                    scanner,
                    tech_ext,
                    query,
                    i,
                    len(needs_deep),
                    search_best_url,
                )

                if updated:
                    found += 1
                    logger.info(f"  ✓ {company_name}: добавлено {', '.join(updated)}")
                else:
                    logger.debug(f"  — {company_name}: ничего нового")

                session.flush()
            except Exception as e:
                logger.error(
                    f"Ошибка deep enrich для {getattr(record, name_attr, '?')}: {e}"
                )

        print_status(
            f"Точечный поиск: дополнено {found}/{len(needs_deep)} компаний", "success"
        )
        return found

    def _deep_enrich_company(
        self,
        session,
        erow,
        company_name: str,
        city: str,
        scanner,
        tech_ext,
        query: str,
        row_num: int,
        total: int,
        search_best_url: bool = True,
    ) -> list[str]:
        """Единая логика веб-обогащения для одной компании.

        Returns:
            Список обновлённых полей (например ["website", "email"]).
        """
        logger.info(f"  [{row_num}/{total}] Веб-поиск: {query}")

        result = self.web.search(query)
        if not result:
            logger.debug(f"  Пустой ответ для '{query}'")
            return []

        web_results = result.get("data", {}).get("web", [])
        if not web_results:
            logger.debug(f"  Нет web-результатов для '{query}'")
            return []

        # Ищем наиболее релевантный URL
        best_url = None
        if search_best_url:
            for wr in web_results:
                wr_url = wr.get("url", "")
                wr_title = wr.get("title", "").lower()
                if wr_url:
                    name_words = company_name.lower().split()[:3]
                    if any(w in wr_title for w in name_words if len(w) > 2):
                        best_url = wr_url
                        break
        # Фоллбэк: первый результат
        if not best_url:
            best_url = web_results[0].get("url", "")

        if not best_url:
            return []

        logger.info(f"  Найден сайт: {best_url} для {company_name}")

        details = self.web.scrape(best_url)
        if not details:
            logger.debug(f"  Скрапинг не дал данных для {best_url}")
            return []

        updated = []
        new_emails = details.get("emails", [])
        new_phones = details.get("phones", [])

        # Получаем CompanyRow для обновления
        c = session.get(CompanyRow, erow.id)

        # Обновляем website
        if not erow.website and best_url:
            erow.website = best_url
            if c:
                c.website = best_url
            updated.append("website")

        # Обновляем email
        if new_emails:
            existing = set(erow.emails or [])
            for em in new_emails:
                if em not in existing:
                    existing.add(em)
            if "email" not in updated:
                updated.append("email")
            erow.emails = list(existing)
            if c:
                c.emails = list(existing)

        # Обновляем телефоны (дополняем)
        if new_phones:
            existing_phones = set(erow.phones or [])
            for ph in new_phones:
                ph_norm = normalize_phone(ph)
                if ph_norm and ph_norm not in existing_phones:
                    existing_phones.add(ph_norm)
            if "phone" not in updated:
                updated.append("phone")
            erow.phones = list(existing_phones)
            if c:
                c.phones = list(existing_phones)

        # Мессенджеры и CMS с найденного сайта
        if best_url:
            valid_url, status = validate_website(best_url)
            if valid_url and status == 200:
                site_messengers = scanner.scan_website(valid_url)
                existing_msg = dict(erow.messengers or {})
                for k, v in site_messengers.items():
                    if k not in existing_msg:
                        existing_msg[k] = v
                        updated.append(k)
                erow.messengers = existing_msg
                if c:
                    c.messengers = existing_msg

                if erow.cms in (None, "unknown", ""):
                    tech = tech_ext.extract(valid_url)
                    if tech.get("cms") and tech["cms"] != "unknown":
                        erow.cms = tech["cms"]
                        updated.append(f"cms:{tech['cms']}")

        return updated

