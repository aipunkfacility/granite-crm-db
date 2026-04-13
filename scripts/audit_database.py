# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "sqlalchemy",
#     "click",
#     "tabulate",
# ]
# ///
import os
import sys

# Fix Windows CP1251 encoding for emoji support
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
"""
Granite CRM — Data Auditor
Аудит качества базы данных granite.db.

Использование:
  uv run scripts/audit_database.py
  uv run scripts/audit_database.py --city "Краснодар"
  uv run scripts/audit_database.py --output data/audit_report.md
"""

import json
from datetime import datetime
from pathlib import Path

import click

# Добавляем корень проекта в sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from granite.database import Database


# ─── helpers ────────────────────────────────────────────────────────────────

def _q(session, sql: str, params: dict | None = None):
    """Выполнить raw SQL и вернуть список dict."""
    from sqlalchemy import text
    result = session.execute(text(sql), params or {})
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]


def pct(part, total) -> float:
    return round(100.0 * part / total, 1) if total else 0.0


def _json_has_value(json_str: str | None) -> bool:
    """True если JSON-поле содержит данные (не пустой список/словарь/None)."""
    if json_str is None:
        return False
    try:
        val = json.loads(json_str)
        if isinstance(val, (list, dict)):
            return len(val) > 0
        return bool(val)
    except Exception:
        return bool(json_str)


# ─── individual checks ───────────────────────────────────────────────────────

def check_overview(session, city_filter: str | None) -> dict:
    """Общая статистика."""
    city_clause = "WHERE city = :city" if city_filter else ""
    params = {"city": city_filter} if city_filter else {}
    rows = _q(session, f"""
        SELECT
            COUNT(*) as total,
            COUNT(DISTINCT city) as cities,
            SUM(CASE WHEN segment IS NULL THEN 1 ELSE 0 END) as no_segment,
            SUM(CASE WHEN crm_score IS NULL OR crm_score = 0 THEN 1 ELSE 0 END) as zero_score,
            SUM(CASE WHEN website IS NULL AND (emails IS NULL OR emails = '[]')
                          AND (messengers IS NULL OR messengers = '{{}}') THEN 1 ELSE 0 END) as no_contacts,
            SUM(CASE WHEN messengers LIKE '%"telegram"%' THEN 1 ELSE 0 END) as has_telegram,
            SUM(CASE WHEN segment = 'A' THEN 1 ELSE 0 END) as seg_a,
            SUM(CASE WHEN segment = 'B' THEN 1 ELSE 0 END) as seg_b,
            SUM(CASE WHEN segment = 'C' THEN 1 ELSE 0 END) as seg_c,
            SUM(CASE WHEN segment = 'D' THEN 1 ELSE 0 END) as seg_d
        FROM enriched_companies {city_clause}
    """, params)

    raw_total = _q(session, "SELECT COUNT(*) as n FROM raw_companies", {})[0]["n"]
    companies_total = _q(session, "SELECT COUNT(*) as n FROM companies", {})[0]["n"]

    r = rows[0]
    return {
        "raw_total": raw_total,
        "companies_total": companies_total,
        "enriched_total": r["total"],
        "cities": r["cities"],
        "no_segment": r["no_segment"],
        "zero_score": r["zero_score"],
        "no_contacts": r["no_contacts"],
        "has_telegram": r["has_telegram"],
        "seg_a": r["seg_a"],
        "seg_b": r["seg_b"],
        "seg_c": r["seg_c"],
        "seg_d": r["seg_d"],
    }


def check_quality_by_city(session) -> list[dict]:
    """Качество по городам, отсортировано по % нулевых записей (хуже — выше)."""
    return _q(session, """
        SELECT
            city,
            COUNT(*) as total,
            SUM(CASE WHEN crm_score = 0 OR crm_score IS NULL THEN 1 ELSE 0 END) as zero_score,
            SUM(CASE WHEN website IS NULL AND (emails IS NULL OR emails = '[]')
                          AND (messengers IS NULL OR messengers = '{}') THEN 1 ELSE 0 END) as no_contacts,
            ROUND(100.0 * SUM(CASE WHEN crm_score = 0 OR crm_score IS NULL THEN 1 ELSE 0 END) / COUNT(*), 1) as pct_zero
        FROM enriched_companies
        GROUP BY city
        ORDER BY pct_zero DESC
    """)


def check_scoring_anomalies(session, city_filter: str | None) -> list[dict]:
    """Компании с хорошими данными, но низким скором (баг скоринга)."""
    city_clause = "AND city = :city" if city_filter else ""
    params = {"city": city_filter} if city_filter else {}
    return _q(session, f"""
        SELECT id, name, city, crm_score, segment, website, messengers, emails
        FROM enriched_companies
        WHERE crm_score < 15
          AND (
            website IS NOT NULL
            OR (messengers IS NOT NULL AND messengers != '{{}}' AND messengers != 'null')
          )
          {city_clause}
        ORDER BY city, crm_score DESC
        LIMIT 50
    """, params)


def check_duplicates_by_name(session, city_filter: str | None) -> list[dict]:
    """Дубли по точному совпадению названия в одном городе."""
    city_clause = "HAVING cnt > 1 AND city = :city" if city_filter else "HAVING cnt > 1"
    params = {"city": city_filter} if city_filter else {}
    return _q(session, f"""
        SELECT name, city, COUNT(*) as cnt, GROUP_CONCAT(id) as ids
        FROM enriched_companies
        GROUP BY LOWER(TRIM(name)), city
        {city_clause}
        ORDER BY cnt DESC
        LIMIT 30
    """, params)


def check_dead_records(session, city_filter: str | None) -> list[dict]:
    """Мёртвые записи: нет ничего, кроме названия."""
    city_clause = "AND city = :city" if city_filter else ""
    params = {"city": city_filter} if city_filter else {}
    rows = _q(session, f"""
        SELECT city, COUNT(*) as dead
        FROM enriched_companies
        WHERE (phones IS NULL OR phones = '[]')
          AND website IS NULL
          AND (emails IS NULL OR emails = '[]')
          AND (messengers IS NULL OR messengers = '{{}}')
          {city_clause}
        GROUP BY city
        ORDER BY dead DESC
    """, params)
    return rows


def check_cms_distribution(session, city_filter: str | None) -> list[dict]:
    """Распределение CMS."""
    city_clause = "AND city = :city" if city_filter else ""
    params = {"city": city_filter} if city_filter else {}
    return _q(session, f"""
        SELECT COALESCE(cms, 'unknown') as cms, COUNT(*) as cnt
        FROM enriched_companies
        WHERE 1=1 {city_clause}
        GROUP BY cms
        ORDER BY cnt DESC
    """, params)


def check_tg_trust(session, city_filter: str | None) -> dict:
    """Статистика по Telegram trust."""
    city_clause = "AND city = :city" if city_filter else ""
    params = {"city": city_filter} if city_filter else {}
    rows = _q(session, f"""
        SELECT
            SUM(CASE WHEN messengers LIKE '%"telegram"%' THEN 1 ELSE 0 END) as has_tg,
            SUM(CASE WHEN json_extract(tg_trust, '$.trust_score') >= 2 THEN 1 ELSE 0 END) as tg_live,
            SUM(CASE WHEN json_extract(tg_trust, '$.trust_score') = 0 
                         AND messengers LIKE '%"telegram"%' THEN 1 ELSE 0 END) as tg_dead
        FROM enriched_companies
        WHERE 1=1 {city_clause}
    """, params)
    return rows[0]


# ─── report generation ───────────────────────────────────────────────────────

def generate_report(
    overview: dict,
    quality_by_city: list[dict],
    anomalies: list[dict],
    duplicates: list[dict],
    dead: list[dict],
    cms: list[dict],
    tg: dict,
    city_filter: str | None,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    scope = f" [{city_filter}]" if city_filter else ""

    total = overview["enriched_total"]
    problems_total = overview["zero_score"]
    health_pct = pct(total - problems_total, total)
    health_icon = "🟢" if health_pct >= 80 else "🟡" if health_pct >= 60 else "🔴"

    lines = [
        f"# 🔍 Granite CRM — Health Check{scope}",
        f"*Сгенерирован: {now}*",
        "",
        "---",
        "",
        "## 📊 Общая картина",
        "",
        f"| Параметр | Значение |",
        f"|----------|----------|",
        f"| Сырых записей (raw) | {overview['raw_total']:,} |",
        f"| После дедупликации | {overview['companies_total']:,} |",
        f"| Обогащённых компаний | {total:,} |",
        f"| Городов | {overview['cities']} |",
        f"| Без сегмента | {overview['no_segment']} |",
        f"| Нулевой скор | {overview['zero_score']:,} ({pct(overview['zero_score'], total)}%) |",
        f"| Без контактов | {overview['no_contacts']:,} ({pct(overview['no_contacts'], total)}%) |",
        f"| Есть Telegram | {overview['has_telegram']:,} ({pct(overview['has_telegram'], total)}%) |",
        f"| {health_icon} Здоровье базы | **{health_pct}%** |",
        "",
        "### Сегменты",
        "",
        f"| A (горячие) | B | C | D (мёртвые) |",
        f"|------------|---|---|------------|",
        f"| {overview['seg_a']} ({pct(overview['seg_a'], total)}%) "
        f"| {overview['seg_b']} ({pct(overview['seg_b'], total)}%) "
        f"| {overview['seg_c']} ({pct(overview['seg_c'], total)}%) "
        f"| {overview['seg_d']} ({pct(overview['seg_d'], total)}%) |",
        "",
    ]

    # Quality by city
    if not city_filter:
        problem_cities = [r for r in quality_by_city if r["pct_zero"] > 25]
        ok_cities = [r for r in quality_by_city if r["pct_zero"] <= 25]
        lines += [
            "---",
            "",
            "## 🏙️ Качество по городам",
            "",
        ]
        if problem_cities:
            lines += [
                "### ⚠️ Требуют внимания (> 25% нулевых записей)",
                "",
                "| Город | Всего | Нулевой скор | Без контактов | % нулевых |",
                "|-------|-------|-------------|--------------|-----------|",
            ]
            for r in problem_cities:
                flag = "🔴" if r["pct_zero"] > 35 else "🟡"
                lines.append(
                    f"| {flag} {r['city']} | {r['total']} | {r['zero_score']} | {r['no_contacts']} | {r['pct_zero']}% |"
                )
            lines.append("")
        if ok_cities:
            lines += [
                "### ✅ Нормальное качество (≤ 25%)",
                "",
                "| Город | Всего | Нулевой скор | % нулевых |",
                "|-------|-------|-------------|-----------|",
            ]
            for r in ok_cities:
                lines.append(f"| {r['city']} | {r['total']} | {r['zero_score']} | {r['pct_zero']}% |")
            lines.append("")

    # Scoring anomalies
    lines += ["---", "", "## ⚡ Аномалии скоринга", ""]
    if anomalies:
        lines += [
            f"Найдено **{len(anomalies)}** компаний с данными, но низким скором (< 15):",
            "",
            "| ID | Название | Город | Скор | Сегмент | Сайт | Мессенджеры |",
            "|----|----------|-------|------|---------|------|------------|",
        ]
        for r in anomalies[:20]:
            has_site = "✓" if r["website"] else "—"
            has_msg = "✓" if r["messengers"] and r["messengers"] not in ("{}", "null") else "—"
            lines.append(
                f"| {r['id']} | {r['name'][:30]} | {r['city']} | {r['crm_score']} | {r['segment']} | {has_site} | {has_msg} |"
            )
        if len(anomalies) > 20:
            lines.append(f"*...и ещё {len(anomalies) - 20} записей*")
    else:
        lines.append("✅ Аномалий скоринга не найдено.")
    lines.append("")

    # Duplicates
    lines += ["---", "", "## 👥 Дубли (точное совпадение названия)", ""]
    if duplicates:
        lines += [
            f"Найдено **{len(duplicates)}** групп дублей:",
            "",
            "| Название | Город | Кол-во | ID записей |",
            "|----------|-------|--------|-----------|",
        ]
        for r in duplicates:
            lines.append(f"| {r['name'][:30]} | {r['city']} | {r['cnt']} | {r['ids']} |")
    else:
        lines.append("✅ Явных дублей не найдено.")
    lines.append("")

    # Dead records
    lines += ["---", "", "## 💀 Мёртвые записи (совсем нет контактов)", ""]
    if dead:
        total_dead = sum(r["dead"] for r in dead)
        lines += [
            f"Всего мёртвых записей: **{total_dead}** ({pct(total_dead, total)}%)",
            "",
            "| Город | Мёртвых |",
            "|-------|--------|",
        ]
        for r in dead:
            lines.append(f"| {r['city']} | {r['dead']} |")
    else:
        lines.append("✅ Мёртвых записей нет.")
    lines.append("")

    # CMS
    lines += ["---", "", "## 🖥️ CMS-распределение", ""]
    if cms:
        lines += [
            "| CMS | Кол-во |",
            "|-----|--------|",
        ]
        for r in cms[:10]:
            lines.append(f"| {r['cms']} | {r['cnt']} |")
    lines.append("")

    # TG
    lines += [
        "---",
        "",
        "## 📱 Telegram Trust",
        "",
        f"| Параметр | Кол-во |",
        f"|----------|--------|",
        f"| Есть Telegram | {tg['has_tg']} |",
        f"| Живой профиль (trust ≥ 2) | {tg['tg_live']} |",
        f"| Мёртвый профиль (trust = 0) | {tg['tg_dead']} |",
        "",
    ]

    # Recommendations
    lines += ["---", "", "## 🛠️ Рекомендации", ""]
    recs = []

    if not city_filter:
        for r in quality_by_city:
            if r["pct_zero"] > 30:
                recs.append(
                    f"- Запустить `python cli.py run \"{r['city']}\" --re-enrich` "
                    f"(нулевых: {r['pct_zero']}%)"
                )

    if anomalies:
        recs.append(
            f"- Проверить `granite/enrichers/classifier.py` — найдено {len(anomalies)} "
            f"аномалий скоринга (есть данные, но скор < 15)"
        )
    if duplicates:
        recs.append(f"- Найдено {len(duplicates)} группы дублей — запустить дедупликацию `--no-scrape`")

    if tg["tg_dead"] > tg["tg_live"] and tg["has_tg"] > 0:
        recs.append(
            "- Много мёртвых TG-профилей — проверить `granite/enrichers/tg_trust.py`"
        )

    if not recs:
        recs.append("✅ Явных проблем не выявлено. База в хорошем состоянии.")

    lines += recs
    lines += ["", "---", f"*Отчёт создан автоматически: `scripts/audit_database.py`*"]

    return "\n".join(lines)


# ─── CLI ────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--city", default=None, help="Аудит только одного города")
@click.option("--output", default=None, help="Сохранить отчёт в Markdown-файл")
@click.option("--db", default=None, help="Путь к БД (по умолчанию: data/granite.db)")
def main(city: str | None, output: str | None, db: str | None):
    """Granite CRM — аудит качества базы данных."""
    db_path = db or str(ROOT / "data" / "granite.db")
    click.echo(f"[*] Подключение к базе: {db_path}")
    database = Database(db_path=db_path)

    with database.session_scope() as session:
        click.echo("[*] Вычисляю статистику...")
        overview = check_overview(session, city)
        quality_by_city = check_quality_by_city(session) if not city else []
        anomalies = check_scoring_anomalies(session, city)
        duplicates = check_duplicates_by_name(session, city)
        dead = check_dead_records(session, city)
        cms = check_cms_distribution(session, city)
        tg = check_tg_trust(session, city)

    report = generate_report(overview, quality_by_city, anomalies, duplicates, dead, cms, tg, city)

    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        click.echo(f"[OK] Отчёт сохранён: {output_path}")
    else:
        click.echo("\n" + report)


if __name__ == "__main__":
    main()
