# pipeline/dedup_phase.py
"""Фаза 2: дедупликация сырых данных из БД.

Вынесено из PipelineManager — полностью независимая фаза
кластеризации и слияния дубликатов.
"""

from granite.database import Database, RawCompanyRow, CompanyRow
from loguru import logger
from granite.pipeline.status import print_status

# Import Dedup
from granite.dedup.phone_cluster import cluster_by_phones
from granite.dedup.site_matcher import cluster_by_site
from granite.dedup.merger import merge_cluster
from granite.dedup.validator import validate_phones, validate_emails


class DedupPhase:
    """Дедупликация: кластеризация по телефону/сайту + слияние."""

    def __init__(self, db: Database):
        self.db = db

    def run(self, city: str) -> int:
        """Запустить дедупликацию для города.

        Returns:
            Количество уникальных компаний после слияния.
        """
        print_status("ФАЗА 2: Дедупликация и слияние (Dedup)", "info")
        with self.db.session_scope() as session:
            raw_records = session.query(RawCompanyRow).filter_by(city=city).all()
            if not raw_records:
                print_status("Нет данных для дедупликации", "warning")
                return 0

            # Перевод в dict для алгоритмов
            dicts = []
            for r in raw_records:
                dicts.append(
                    {
                        "id": r.id,
                        "source": r.source,
                        "source_url": r.source_url or "",
                        "name": r.name,
                        "phones": r.phones or [],
                        "address_raw": r.address_raw or "",
                        "website": r.website,
                        "emails": r.emails or [],
                        "geo": r.geo,
                        "messengers": r.messengers or {},
                        "city": r.city,
                    }
                )

            # Валидация перед кластеризацией
            for d in dicts:
                d["phones"] = validate_phones(d.get("phones", []))
                d["emails"] = validate_emails(d.get("emails", []))

            # Алгоритмы кластеризации (только телефон и сайт — без name_matcher)
            clusters_phone = cluster_by_phones(dicts)
            clusters_site = cluster_by_site(dicts)

            # Объединение всех кластеров (Union-Find)
            superclusters = self._union_find(dicts, clusters_phone + clusters_site)

            print_status(
                f"Найдено {len(superclusters)} уникальных компаний из {len(dicts)} записей"
            )

            # Слияние и сохранение
            # O(1) lookup via dict instead of O(N) list comprehension
            dicts_by_id = {d["id"]: d for d in dicts}
            conflicts = []
            for i, cl in enumerate(superclusters):
                cluster_dicts = [dicts_by_id[cid] for cid in cl]
                merged = merge_cluster(cluster_dicts)

                row = CompanyRow(
                    name_best=merged["name_best"],
                    phones=merged["phones"],
                    address=merged["address"],
                    website=merged["website"],
                    emails=merged["emails"],
                    city=merged["city"],
                    status="raw",
                )
                session.add(row)

                if merged["needs_review"]:
                    conflicts.append(
                        {
                            "cluster_id": i + 1,
                            "records": cluster_dicts,
                            "reason": merged["review_reason"],
                        }
                    )

            if conflicts:
                logger.warning(f"Конфликты при слиянии: {len(conflicts)} компаний")

            return len(superclusters)

    @staticmethod
    def _union_find(dicts: list[dict], clusters: list[list[int]]) -> list[list[int]]:
        """Объединение перекрывающихся кластеров через Union-Find.

        Args:
            dicts: список всех записей (нужны только id).
            clusters: список кластеров, каждый — список id записей.

        Returns:
            Список уникальных суперкластеров (списков id).
        """
        id_to_supercluster: dict[int, set[int]] = {}
        for d in dicts:
            id_to_supercluster[d["id"]] = {d["id"]}

        for cl in clusters:
            connected = set()
            for cid in cl:
                connected.update(id_to_supercluster.get(cid, {cid}))
            for cid in connected:
                id_to_supercluster[cid] = connected

        # Уникальные суперкластеры
        seen = set()
        superclusters = []
        for cid, cl in id_to_supercluster.items():
            k = frozenset(cl)
            if k not in seen:
                seen.add(k)
                superclusters.append(list(cl))

        return superclusters
