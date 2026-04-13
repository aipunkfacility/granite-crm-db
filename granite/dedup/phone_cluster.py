# dedup/phone_cluster.py
from collections import defaultdict
from granite.utils import normalize_phone


def cluster_by_phones(raw_companies: list[dict]) -> list[list[int]]:
    """Группировка записей по общим номерам телефонов (Union-Find).

    Возвращает список кластеров: [[id1, id2, id3], [id4, id5], ...]
    Записи с общим номером → один кластер (транзитивно).

    Args:
        raw_companies: список dict с полями {"id": int, "phones": list[str]}
    """
    # phone → set of company ids (phones normalized for correct dedup)
    phone_to_ids: dict[str, set[int]] = defaultdict(set)
    for company in raw_companies:
        cid = company.get("id")
        if cid is None:
            continue
        for phone in company.get("phones", []):
            norm = normalize_phone(phone)
            if norm:
                phone_to_ids[norm].add(cid)

    # Union-Find: id → set of connected ids
    id_to_cluster: dict[int, set[int]] = {}

    for phone, ids in phone_to_ids.items():
        if len(ids) < 2:
            continue  # Один владелец номера — не кластер

        # Найти существующие кластеры, которые пересекаются с текущими ids
        connected: set[int] = set()
        for cid in ids:
            if cid in id_to_cluster:
                connected.update(id_to_cluster[cid])

        # Новый кластер = объединение всех найденных + текущие ids
        new_cluster = connected | ids
        for cid in new_cluster:
            id_to_cluster[cid] = new_cluster

    # Убираем дубли кластеров
    seen: set[frozenset] = set()
    clusters = []
    for cid, cluster in id_to_cluster.items():
        cluster_key = frozenset(cluster)
        if cluster_key not in seen:
            seen.add(cluster_key)
            clusters.append(list(cluster))

    return clusters
