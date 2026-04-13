# dedup/merger.py
from granite.utils import pick_best_value, extract_street, normalize_phone, sanitize_filename
from loguru import logger
import os


def _label(index: int) -> str:
    """A, B, ..., Z, AA, AB, ..."""
    result = ""
    while True:
        result = chr(ord("A") + index % 26) + result
        index = index // 26 - 1
        if index < 0:
            break
    return result


def merge_cluster(cluster_records: list[dict]) -> dict:
    """Слияние группы записей в одну Company.

    Правила:
    - name_best: самое длинное название
    - phones: объединение уникальных
    - address: самое длинное значение
    - website: самое длинное значение
    - emails: объединение уникальных
    - merged_from: список id исходных записей

    Args:
        cluster_records: список dict с полями RawCompany (из БД)
    """
    if not cluster_records:
        return {}

    # Объединяем messengers из всех raw-записей
    merged_messengers: dict = {}
    for r in cluster_records:
        messengers = r.get("messengers")
        if isinstance(messengers, dict):
            for k, v in messengers.items():
                if v and k not in merged_messengers:
                    merged_messengers[k] = v

    # Объединяем все телефоны с нормализацией и дедупликацией
    all_phones: list[str] = []
    seen_phones: set[str] = set()
    for r in cluster_records:
        for p in r.get("phones", []):
            norm = normalize_phone(p)
            if norm and norm not in seen_phones:
                seen_phones.add(norm)
                all_phones.append(norm)

    merged = {
        "merged_from": [r.get("id") for r in cluster_records if r.get("id") is not None],
        "name_best": pick_best_value(*(r.get("name", "") for r in cluster_records)),
        "phones": all_phones,
        "address": pick_best_value(
            *(r.get("address_raw", "") for r in cluster_records)
        ),
        "website": pick_best_value(
            *(r.get("website", "") or "" for r in cluster_records)
        ),
        "emails": list(
            dict.fromkeys(
                e
                for r in cluster_records
                for e in r.get("emails", [])
                if e  # skip None/empty
            )
        ),
        "messengers": merged_messengers,
        "city": cluster_records[0].get("city", ""),
        "needs_review": False,
        "review_reason": "",
    }

    # Очищаем пустые website
    if not merged["website"]:
        merged["website"] = None

    # Проверка: одинаковые названия, но разные адреса → конфликт
    streets = [extract_street(r.get("address_raw", "")) for r in cluster_records]
    unique_streets = {s for s in streets if s}

    if len(unique_streets) > 1:
        # Проверяем, действительно ли названия похожи — если нет, это разные компании
        names_raw = [r.get("name", "") for r in cluster_records]
        unique_names = list({n.strip().lower() for n in names_raw if n and n.strip()})

        def _jaccard_words(a: str, b: str) -> float:
            sa, sb = set(a.split()), set(b.split())
            if not sa or not sb:
                return 0.0
            return len(sa & sb) / len(sa | sb)

        names_similar = True
        if unique_names:
            for i in range(len(unique_names)):
                for j in range(i + 1, len(unique_names)):
                    if _jaccard_words(unique_names[i], unique_names[j]) <= 0.5:
                        names_similar = False
                        break
                if not names_similar:
                    break

        # Если названия совсем разные (Jaccard < 0.3) и адреса разные —
        # это точно разные компании, объединённые ошибочно по телефону
        if len(unique_names) > 1 and not names_similar:
            merged["needs_review"] = True
            merged["review_reason"] = "different_names_different_addresses"
        elif len(unique_names) <= 2 and names_similar:
            # Названия похожие, но адреса разные — помечаем для ручной проверки
            merged["needs_review"] = True
            merged["review_reason"] = "same_name_diff_address"

    # Проверка: разные города в кластере → конфликт
    cities = [r.get("city", "") for r in cluster_records if r.get("city")]
    if len(set(cities)) > 1:
        merged["needs_review"] = True
        if merged.get("review_reason"):
            merged["review_reason"] = merged["review_reason"] + " different_cities"
        else:
            merged["review_reason"] = "different_cities"

    return merged


def generate_conflicts_md(
    conflicts: list[dict], city: str, output_dir: str = "data/conflicts"
):
    """Генерация conflicts.md для Human-in-the-loop.

    Args:
        conflicts: список dict с полями:
            - "cluster_id": int
            - "records": list[dict] — исходные записи из кластера
            - "reason": str
        city: название города
        output_dir: путь для сохранения
    """
    if not conflicts:
        return

    os.makedirs(output_dir, exist_ok=True)
    safe_city = sanitize_filename(city)
    filepath = os.path.join(output_dir, f"{safe_city}_conflicts.md")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Конфликты дедупликации — {city}\n\n")
        f.write(f"**Найдено конфликтов:** {len(conflicts)}\n\n")
        f.write("Для каждого конфликта отметьте правильный вариант `[x]`:\n\n")
        f.write("---\n\n")

        for i, conflict in enumerate(conflicts, 1):
            f.write(f"## {i}. Конфликт #{conflict.get('cluster_id', '?')}\n\n")
            f.write(f"**Причина:** {conflict.get('reason', '?')}\n\n")

            records = conflict["records"]
            for j, record in enumerate(records):
                letter = _label(j)
                f.write(f"- [ ] **Вариант {letter}:** {record.get('name', 'N/A')}\n")
                f.write(f"  Адрес: {record.get('address_raw', 'N/A')}\n")
                f.write(f"  Телефон: {', '.join(record.get('phones', []))}\n")
                f.write(f"  Сайт: {record.get('website', 'N/A')}\n")
                f.write(f"  Источник: {record.get('source', 'N/A')}\n")
                f.write(f"  ID: {record.get('id', 'N/A')}\n\n")

            f.write(f"- [ ] **Разные компании** (не объединять)\n\n")
            f.write("---\n\n")

    logger.info(f"Conflicts сохранены: {filepath} ({len(conflicts)} конфликтов)")
