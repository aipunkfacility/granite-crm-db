#!/usr/bin/env python3
"""
enrich_jsprav_messengers.py — одноразовый скрипт для обогащения jsprav-записей мессенджерами.

Берёт raw_companies от jsprav, находит их detail-страницы на jsprav.ru,
парсит base64 data-link (TG, VK, WA, Viber, OK) и обновляет:
  - raw_companies.messengers
  - companies.messengers (для смерженных)
  - enriched_companies.messengers (если есть)

Запуск:
    cd granite-crm-db
    python scripts/enrich_jsprav_messengers.py [--db path/to/granite.db] [--cities Город1 Город2] [--dry-run]
"""
import argparse
import base64
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup
from loguru import logger
from rapidfuzz import fuzz

# ── Добавляем корень проекта в sys.path ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from granite.database import Database, RawCompanyRow, CompanyRow, EnrichedCompanyRow

# ===== User-Agent =====
_UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

JSPRAV_CATEGORY = "izgotovlenie-i-ustanovka-pamyatnikov-i-nadgrobij"


def get_ua():
    return random.choice(_UA_LIST)


def classify_messenger(url: str) -> tuple[str, str] | None:
    """Возвращает (type, url) или None."""
    u = url.lower()
    if "t.me" in u:
        return ("telegram", url)
    if "vk.com" in u or "vkontakte" in u:
        return ("vk", url)
    if "wa.me" in u or "whatsapp" in u:
        return ("whatsapp", url)
    if "viber" in u:
        return ("viber", url)
    if "ok.ru" in u:
        return ("odnoklassniki", url)
    if "instagram" in u:
        return ("instagram", url)
    return None  # YouTube и прочее — пропускаем


def fetch_detail_messengers(detail_url: str, session: requests.Session) -> dict:
    """Загружает detail-страницу и парсит мессенджеры из base64 data-link."""
    result = {"messengers": {}, "website": None, "phones": []}
    try:
        r = session.get(detail_url, timeout=20)
        if r.status_code != 200:
            return result
    except Exception:
        return result

    soup = BeautifulSoup(r.text, "html.parser")

    for a in soup.find_all("a", attrs={"data-link": True}):
        try:
            decoded = base64.b64decode(a["data-link"]).decode("utf-8")
            dtype = a.get("data-type", "")
            if dtype == "org-link":
                result["website"] = decoded
            elif dtype == "org-social-link":
                classified = classify_messenger(decoded)
                if classified:
                    result["messengers"][classified[0]] = classified[1]
        except Exception:
            pass

    # Телефоны из data-props
    for el in soup.find_all(attrs={"data-props": True}):
        try:
            props = json.loads(el.get("data-props", "{}"))
            if "phones" in props:
                result["phones"] = props["phones"]
        except Exception:
            pass

    return result


def get_jsprav_listing_urls(subdomain: str, session: requests.Session) -> dict[str, str]:
    """Парсит листинг jsprav, возвращает {name_norm: detail_url}."""
    url_map = {}
    base_url = f"https://{subdomain}.jsprav.ru/{JSPRAV_CATEGORY}/"

    for page in range(1, 6):  # max 5 страниц
        if page == 1:
            url = base_url
        else:
            url = f"{base_url}page-{page}/"

        try:
            r = session.get(url, timeout=30, headers={"User-Agent": get_ua()})
            if r.status_code == 404:
                break
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:
            break

        found = 0
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if data.get("@type") != "ItemList":
                    continue
                for item in data.get("itemListElement", []):
                    c = item.get("item", {})
                    if c.get("@type") != "LocalBusiness":
                        continue
                    name = c.get("name", "")
                    org_url = c.get("url", "")
                    if name and org_url:
                        url_map[name.lower().strip()] = org_url
                        found += 1
            except Exception:
                continue

        if found == 0:
            break

        time.sleep(random.uniform(0.8, 1.5))

    return url_map


def slugify_city(city: str) -> str:
    """Транслитерация города для субдомена jsprav."""
    TRANSLIT = [
        ("щ", "shch"), ("ш", "sh"), ("ч", "ch"), ("ж", "zh"),
        ("ю", "yu"), ("я", "ya"), ("ё", "yo"), ("э", "e"),
        ("х", "kh"), ("ц", "ts"),
        ("а", "a"), ("б", "b"), ("в", "v"), ("г", "g"), ("д", "d"),
        ("е", "e"), ("з", "z"), ("и", "i"), ("й", "y"), ("к", "k"),
        ("л", "l"), ("м", "m"), ("н", "n"), ("о", "o"), ("п", "p"),
        ("р", "r"), ("с", "s"), ("т", "t"), ("у", "u"), ("ф", "f"),
        ("ъ", ""), ("ы", "y"), ("ь", ""),
    ]
    text = city.lower().strip()
    for cyr, lat in TRANSLIT:
        text = text.replace(cyr, lat)
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s]+", "-", text).strip("-")
    return text


def match_name(raw_name: str, listing_names: list[str]) -> str | None:
    """Находит лучшее совпадение имени в listing. Возвращает key или None."""
    raw_lower = raw_name.lower().strip()
    best_key = None
    best_score = 0
    for key in listing_names:
        score = fuzz.token_sort_ratio(raw_lower, key)
        if score >= best_score and score >= 80:
            best_score = score
            best_key = key
    return best_key


def main():
    parser = argparse.ArgumentParser(description="Обогащение jsprav raw_companies мессенджерами")
    parser.add_argument("--db", default=None, help="Путь к БД (по умолчанию из config.yaml)")
    parser.add_argument("--cities", nargs="+", default=None, help="Города (по умолчанию все из БД)")
    parser.add_argument("--dry-run", action="store_true", help="Не записывать в БД")
    args = parser.parse_args()

    # Config
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    subdomain_map = config.get("sources", {}).get("jsprav", {}).get("subdomain_map", {})

    # DB
    db = Database(db_path=args.db or config.get("database", {}).get("path", "data/granite.db"))

    session = requests.Session()
    session.headers["User-Agent"] = get_ua()

    with db.session_scope() as sess:
        # Считаем jsprav-записи без мессенджеров
        query = sess.query(RawCompanyRow).filter(
            RawCompanyRow.source.in_(["jsprav", "jsprav_playwright"]),
            RawCompanyRow.city.isnot(None),
        )

        if args.cities:
            query = query.filter(RawCompanyRow.city.in_(args.cities))

        raw_companies = query.all()
        logger.info(f"Найдено {len(raw_companies)} jsprav-записей")

        # Группируем по городам
        by_city: dict[str, list[RawCompanyRow]] = {}
        for rc in raw_companies:
            by_city.setdefault(rc.city, []).append(rc)

        logger.info(f"Города: {list(by_city.keys())}")

        # Статистика
        stats = {
            "total": len(raw_companies),
            "matched": 0,
            "enriched": 0,
            "messengers_found": 0,
            "tg": 0, "vk": 0, "wa": 0, "viber": 0, "ok": 0,
            "errors": 0,
        }

        for city, rc_list in by_city.items():
            logger.info(f"\n{'='*50}")
            logger.info(f"Город: {city} ({len(rc_list)} записей)")

            # Определяем субдомен
            city_lower = city.lower().strip()
            if city_lower in subdomain_map:
                subdomain = subdomain_map[city_lower]
            else:
                subdomain = slugify_city(city)
                if subdomain.endswith("iy"):
                    subdomain = subdomain[:-2] + "ij"

            # 1. Получаем listing → {name: url}
            logger.info(f"  Скачиваю listing: {subdomain}.jsprav.ru ...")
            url_map = get_jsprav_listing_urls(subdomain, session)
            logger.info(f"  Listing: {len(url_map)} компаний с URL")

            if not url_map:
                logger.warning(f"  Пустой listing для {city} — пропуск")
                continue

            # 2. Кэш уже посещённых detail-страниц
            url_cache: dict[str, dict] = {}  # detail_url → result

            # 3. Матчим raw_companies с listing
            for rc in rc_list:
                key = match_name(rc.name, list(url_map.keys()))
                if not key:
                    continue

                detail_url = url_map[key]
                stats["matched"] += 1

                # 4. Загружаем detail-страницу (с кэшем по URL)
                if detail_url not in url_cache:
                    url_cache[detail_url] = fetch_detail_messengers(detail_url, session)
                    time.sleep(random.uniform(0.3, 0.7))

                detail = url_cache[detail_url]
                if not detail["messengers"]:
                    continue

                # 5. Обновляем raw_companies
                old_msg = rc.messengers or {}
                merged_msg = {**old_msg, **detail["messengers"]}

                if not args.dry_run:
                    rc.messengers = merged_msg
                    if detail["website"] and not rc.website:
                        rc.website = detail["website"]

                stats["enriched"] += 1
                for mtype in detail["messengers"]:
                    stats["messengers_found"] += 1
                    if mtype == "telegram":
                        stats["tg"] += 1
                    elif mtype == "vk":
                        stats["vk"] += 1
                    elif mtype == "whatsapp":
                        stats["wa"] += 1
                    elif mtype == "viber":
                        stats["viber"] += 1
                    elif mtype == "odnoklassniki":
                        stats["ok"] += 1

                logger.debug(
                    f"  + {rc.name[:40]} -> TG={'Y' if 'telegram' in merged_msg else '-'} "
                    f"VK={'Y' if 'vk' in merged_msg else '-'} "
                    f"WA={'Y' if 'whatsapp' in merged_msg else '-'}"
                )

        # ── Шаг 2: обновление companies и enriched_companies ──
        if not args.dry_run and stats["enriched"] > 0:
            logger.info(f"\n{'='*50}")
            logger.info("Обновляю companies и enriched_companies...")

            # Строим обратную карту: {raw_id -> company_id}
            # Дедуп пайплайн пишет CompanyRow.merged_from = [raw_id, ...],
            # но RawCompanyRow.merged_into НИКОГДА не заполняется.
            raw_to_company: dict[int, int] = {}
            all_companies = sess.query(CompanyRow).all()
            for comp in all_companies:
                mf = comp.merged_from or []
                for raw_id in mf:
                    if isinstance(raw_id, int):
                        raw_to_company[raw_id] = comp.id

            logger.info(f"  Карта raw->company: {len(raw_to_company)} связей")

            company_updates = 0
            enriched_updates = 0
            for rc in raw_companies:
                if not rc.messengers:
                    continue

                company_id = raw_to_company.get(rc.id)
                if not company_id:
                    continue

                # Обновляем companies.messengers
                company = sess.query(CompanyRow).get(company_id)
                if company:
                    old = company.messengers or {}
                    merged = {**old, **rc.messengers}
                    if merged != old:
                        company.messengers = merged
                        company_updates += 1

                # Обновляем enriched_companies.messengers
                enriched = sess.query(EnrichedCompanyRow).get(company_id)
                if enriched:
                    old = enriched.messengers or {}
                    merged = {**old, **rc.messengers}
                    if merged != old:
                        enriched.messengers = merged
                        enriched_updates += 1

            logger.info(f"  companies обновлено: {company_updates}")
            logger.info(f"  enriched_companies обновлено: {enriched_updates}")

        # ── Итоги ──
        logger.info(f"\n{'='*50}")
        logger.info(f"ИТОГИ:")
        logger.info(f"  Всего jsprav записей: {stats['total']}")
        logger.info(f"  Сопоставлено с listing: {stats['matched']}")
        logger.info(f"  Обогащено мессенджерами: {stats['enriched']}")
        logger.info(f"  Мессенджеров найдено: {stats['messengers_found']}")
        logger.info(f"    Telegram: {stats['tg']}")
        logger.info(f"    VK: {stats['vk']}")
        logger.info(f"    WhatsApp: {stats['wa']}")
        logger.info(f"    Viber: {stats['viber']}")
        logger.info(f"    OK: {stats['ok']}")
        if args.dry_run:
            logger.info("  [DRY RUN - izmeneniya NE zapisany]")

    db.engine.dispose()


if __name__ == "__main__":
    main()
