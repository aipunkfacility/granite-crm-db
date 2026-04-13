# dedup/site_matcher.py
from granite.utils import extract_domain


def cluster_by_site(companies: list[dict]) -> list[list[int]]:
    """Группировка по домену сайта.

    Записи с одинаковым доменом → один кластер.

    Args:
        companies: список dict с полями {"id": int, "website": str|None}
    """
    domain_to_ids: dict[str, list[int]] = {}

    for company in companies:
        company_id = company.get("id")
        if company_id is None:
            continue
        domain = extract_domain(company.get("website"))
        if domain:
            if domain not in domain_to_ids:
                domain_to_ids[domain] = []
            domain_to_ids[domain].append(company_id)

    return [ids for ids in domain_to_ids.values() if len(ids) > 1]
