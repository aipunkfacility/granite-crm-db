# dedup/name_matcher.py
from granite.utils import compare_names
from collections import defaultdict
from loguru import logger


def find_name_matches(companies: list[dict], threshold: int = 88) -> list[list[int]]:
    """Поиск дубликатов по названиям через fuzzy matching.

    Оптимизация: блокировка по первой букве названия — сравниваем только
    компании у которых совпадает первая буква. Сильно сокращает число
    сравнений на больших выборках.

    Args:
        companies: список dict с полями {"id": int, "name": str, "address": str}
        threshold: порог схожести (0-100, из config.yaml dedup.name_similarity_threshold)

    Returns:
        Список пар/групп: [[id1, id2], ...] — похожие названия
    """
    matches = []

    # Блокировка по первой букве названия
    # NOTE: First-letter blocking may miss matches where prefixes differ
    # (e.g., "ООО Ритуал-Сервис" vs "Ритуал-Сервис"). Consider using
    # sorted n-gram blocking for better recall.
    blocks: dict[str, list[dict]] = defaultdict(list)
    for company in companies:
        name_lower = (company.get("name") or "").lower().strip()
        if not name_lower:
            continue
        key = name_lower[0] if name_lower[0].isalpha() else "#"
        blocks[key].append(company)

    total_comparisons = 0
    sorted_keys = sorted(blocks.keys())
    for key, block_companies in blocks.items():
        n = len(block_companies)
        # Пропускаем блоки из 1 записи
        if n < 2:
            continue
        for i in range(n):
            for j in range(i + 1, n):
                total_comparisons += 1
                if compare_names(block_companies[i].get("name") or "", block_companies[j].get("name") or "", threshold):
                    id_i = block_companies[i].get("id")
                    id_j = block_companies[j].get("id")
                    if id_i is not None and id_j is not None:
                        matches.append([id_i, id_j])

    # Secondary pass: for small blocks, also compare against adjacent blocks
    # to recover cross-prefix matches (e.g., "ООО Ритуал" vs "Ритуал")
    _SMALL_BLOCK_THRESHOLD = 5
    for idx, key in enumerate(sorted_keys):
        block = blocks[key]
        if len(block) >= _SMALL_BLOCK_THRESHOLD:
            continue
        # Only compare forward to avoid duplicate mirrored pairs
        adj_candidates = []
        if idx + 1 < len(sorted_keys):
            adj_candidates.append(sorted_keys[idx + 1])
        for adj_key in adj_candidates:
            if adj_key not in blocks or adj_key == key:
                continue
            adj_block = blocks[adj_key]
            for a in block:
                for b in adj_block:
                    total_comparisons += 1
                    if compare_names(a.get("name") or "", b.get("name") or "", threshold):
                        aid = a.get("id")
                        bid = b.get("id")
                        if aid is not None and bid is not None:
                            matches.append([aid, bid])

    logger.debug(f"Name matcher: {len(companies)} компаний, {total_comparisons} сравнений, {len(matches)} совпадений")
    return matches
