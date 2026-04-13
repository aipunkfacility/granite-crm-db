#!/usr/bin/env python3
"""Бенчмарк: сравнение sync и async обогащения компаний.

Запуск: python -m scripts.benchmark
Или:    python scripts/benchmark.py

Измеряет время обогащения N компаний через:
- sync: ThreadPoolExecutor (legacy)
- async: asyncio + httpx.AsyncClient

Выводит: время sync, время async, ratio.
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

# Минимальный мок обогащения — без реальных HTTP-запросов
def _make_mock_company(i: int):
    """Создать мок CompanyRow."""
    c = MagicMock()
    c.id = i
    c.name_best = f"Компания {i}"
    c.phones = [f"790312345{i:02d}"]
    c.address = f"г. Тест, ул. Тестовая, {i}"
    c.website = f"https://test{i}.ru" if i % 2 == 0 else None
    c.emails = [] if i % 3 != 0 else [f"info@test{i}.ru"]
    c.city = "Тест"
    c.messengers = {}
    return c


def _enrich_one_sync(c, scanner, tech_ext):
    """Sync обогащение одной компании (мок — без HTTP)."""
    erow = MagicMock()
    erow.id = c.id
    erow.name = c.name_best
    erow.phones = c.phones
    erow.address_raw = c.address
    erow.website = c.website
    erow.emails = c.emails
    erow.city = c.city
    erow.messengers = {"telegram": "https://t.me/test"}
    erow.tg_trust = {"score": 5}
    erow.cms = "wordpress"
    erow.has_marquiz = False

    # Имитация задержки HTTP-запроса (50мс на компанию — реалистичнее)
    time.sleep(0.05)
    return erow


async def _enrich_one_async(snapshot, scanner, tech_ext):
    """Async обогащение одной компании (мок — без HTTP)."""
    erow = MagicMock()
    erow.id = snapshot["id"]
    erow.name = snapshot["name_best"]
    erow.phones = snapshot["phones"]
    erow.address_raw = snapshot["address"]
    erow.website = snapshot["website"]
    erow.emails = snapshot["emails"]
    erow.city = snapshot["city"]
    erow.messengers = {"telegram": "https://t.me/test"}
    erow.tg_trust = {"score": 5}
    erow.cms = "wordpress"
    erow.has_marquiz = False

    # Имитация задержки HTTP-запроса (50мс на компанию)
    await asyncio.sleep(0.05)
    return erow


def benchmark_sync(n: int, max_concurrent: int = 3):
    """Sync бенчмарк через ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    companies = [_make_mock_company(i) for i in range(n)]
    scanner = MagicMock()
    tech_ext = MagicMock()
    results = []

    start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = {
            executor.submit(_enrich_one_sync, c, scanner, tech_ext): c
            for c in companies
        }
        for future in as_completed(futures):
            results.append(future.result())

    elapsed = time.perf_counter() - start
    return elapsed, len(results)


async def benchmark_async(n: int, max_concurrent: int = 3):
    """Async бенчмарк через asyncio.Semaphore."""
    scanner = MagicMock()
    tech_ext = MagicMock()

    snapshots = [
        {
            "id": i,
            "name_best": f"Компания {i}",
            "phones": [f"790312345{i:02d}"],
            "address": f"г. Тест, ул. Тестовая, {i}",
            "website": f"https://test{i}.ru" if i % 2 == 0 else None,
            "emails": [] if i % 3 != 0 else [f"info@test{i}.ru"],
            "city": "Тест",
        }
        for i in range(n)
    ]

    sem = asyncio.Semaphore(max_concurrent)

    async def _with_sem(snap):
        async with sem:
            return await _enrich_one_async(snap, scanner, tech_ext)

    start = time.perf_counter()
    tasks = [_with_sem(snap) for snap in snapshots]
    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start

    return elapsed, len(results)


def main():
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    max_concurrent = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    print(f"{'='*60}")
    print(f"  Бенчмарк обогащения: {n} компаний, {max_concurrent} параллельных")
    print(f"{'='*60}")
    print()

    # Sync
    print(f"  Sync (ThreadPoolExecutor x{max_concurrent})...", end=" ", flush=True)
    sync_time, sync_count = benchmark_sync(n, max_concurrent)
    print(f"{sync_time:.2f}s ({sync_count} компаний)")

    # Async
    print(f"  Async (asyncio.Semaphore x{max_concurrent})...", end=" ", flush=True)
    async_time, async_count = asyncio.run(benchmark_async(n, max_concurrent))
    print(f"{async_time:.2f}s ({async_count} компаний)")

    # Результат
    ratio = sync_time / async_time if async_time > 0 else float('inf')
    print()
    print(f"{'='*60}")
    print(f"  Результат:")
    print(f"    Sync:  {sync_time:.2f}s")
    print(f"    Async: {async_time:.2f}s")
    print(f"    Ratio: {ratio:.2f}x")
    print()

    if ratio >= 2.0:
        print(f"  ✅ Async ускорение >= 2x (confirmado!)")
    elif ratio >= 1.5:
        print(f"  ⚠️  Async ускорение {ratio:.1f}x (близко к цели 2x)")
    else:
        print(f"  ❌ Async ускорение {ratio:.1f}x (ниже цели 2x)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
