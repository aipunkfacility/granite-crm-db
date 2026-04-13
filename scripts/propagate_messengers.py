#!/usr/bin/env python3
"""
propagate_messengers.py - propagate messengers from raw_companies
to companies and enriched_companies.

Works with ANY source, not only jsprav. Scans all raw_companies
that have messengers, finds their parent companies via
CompanyRow.merged_from, and merges messengers up.

Runs in seconds, no network requests.

Usage:
    cd granite-crm-db
    python scripts/propagate_messengers.py [--db path/to/granite.db] [--dry-run]
"""
import argparse
import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from granite.database import Database, RawCompanyRow, CompanyRow, EnrichedCompanyRow


def main():
    parser = argparse.ArgumentParser(
        description="Propagate messengers from raw_companies to companies/enriched_companies"
    )
    parser.add_argument("--db", default=None, help="Path to DB (default from config.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to DB")
    args = parser.parse_args()

    db = Database(db_path=args.db or "data/granite.db")

    with db.session_scope() as sess:
        # 1. Build reverse map: {raw_id -> company_id}
        raw_to_company: dict[int, int] = {}
        all_companies = sess.query(CompanyRow).all()
        for comp in all_companies:
            mf = comp.merged_from or []
            for raw_id in mf:
                if isinstance(raw_id, int):
                    raw_to_company[raw_id] = comp.id

        logger.info(f"Total companies: {len(all_companies)}")
        logger.info(f"Raw->Company links: {len(raw_to_company)}")

        # 2. Get enriched_companies set for quick lookup
        enriched_ids = {
            row.id for row in sess.query(EnrichedCompanyRow.id).all()
        }
        logger.info(f"Enriched companies: {len(enriched_ids)}")

        # 3. Find raw_companies with messengers
        all_raws = sess.query(RawCompanyRow).all()
        raws_with_msg = [
            rc for rc in all_raws
            if rc.messengers and isinstance(rc.messengers, dict) and len(rc.messengers) > 0
        ]
        logger.info(f"Total raw companies: {len(all_raws)}")
        logger.info(f"Raw companies with messengers: {len(raws_with_msg)}")

        # 4. Propagate
        company_updates = 0
        enriched_updates = 0
        skipped_no_link = 0
        skipped_no_change = 0

        # Cache company objects to avoid repeated queries
        company_cache: dict[int, CompanyRow] = {
            c.id: c for c in all_companies
        }

        for rc in raws_with_msg:
            if not rc.messengers:
                continue

            company_id = raw_to_company.get(rc.id)
            if not company_id:
                skipped_no_link += 1
                continue

            # Update companies.messengers
            company = company_cache.get(company_id)
            if company:
                old = company.messengers or {}
                merged = {**old, **rc.messengers}
                if merged != old:
                    if not args.dry_run:
                        company.messengers = merged
                    company_updates += 1
                else:
                    skipped_no_change += 1

            # Update enriched_companies.messengers
            if company_id in enriched_ids:
                enriched = sess.query(EnrichedCompanyRow).get(company_id)
                if enriched:
                    old = enriched.messengers or {}
                    merged = {**old, **rc.messengers}
                    if merged != old:
                        if not args.dry_run:
                            enriched.messengers = merged
                        enriched_updates += 1

        # 5. Summary
        logger.info("=" * 50)
        logger.info("RESULTS:")
        logger.info(f"  companies updated:       {company_updates}")
        logger.info(f"  enriched_companies updated: {enriched_updates}")
        logger.info(f"  skipped (no link):       {skipped_no_link}")
        logger.info(f"  skipped (no change):     {skipped_no_change}")

        if args.dry_run:
            logger.info("  [DRY RUN - changes NOT written]")

        # 6. Show messenger stats after propagation
        if not args.dry_run:
            from collections import Counter
            msg_counter: Counter = Counter()
            for c in all_companies:
                if c.messengers:
                    for k in c.messengers:
                        msg_counter[k] += 1
            logger.info("")
            logger.info("companies.messengers stats:")
            for mtype, count in msg_counter.most_common():
                logger.info(f"  {mtype}: {count}")

            msg_counter_e: Counter = Counter()
            for e_id in enriched_ids:
                e = sess.query(EnrichedCompanyRow).get(e_id)
                if e and e.messengers:
                    for k in e.messengers:
                        msg_counter_e[k] += 1
            logger.info("")
            logger.info("enriched_companies.messengers stats:")
            for mtype, count in msg_counter_e.most_common():
                logger.info(f"  {mtype}: {count}")

    db.engine.dispose()


if __name__ == "__main__":
    main()
