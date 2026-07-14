import asyncio

import pytest

import horoscope


@pytest.fixture(autouse=True)
def reset_horoscope_cache(monkeypatch):
    monkeypatch.setattr(horoscope, "_mail_cache_date", None)
    monkeypatch.setattr(horoscope, "_mail_cache", {})


@pytest.mark.asyncio
async def test_mail_pages_limit_concurrency_and_cache_results(monkeypatch):
    active_requests = 0
    max_active_requests = 0
    fetched_signs = []

    async def fetch(sign):
        nonlocal active_requests, max_active_requests

        fetched_signs.append(sign)
        active_requests += 1
        max_active_requests = max(max_active_requests, active_requests)
        await asyncio.sleep(0.01)
        active_requests -= 1
        return horoscope.HoroscopePage(f"{sign} full", f"{sign} first")

    monkeypatch.setattr(horoscope, "_fetch_mail_page", fetch)

    first_result = await horoscope._get_mail_pages(horoscope.horo_list)
    second_result = await horoscope._get_mail_pages(horoscope.horo_list)

    assert set(first_result) == set(horoscope.horo_list)
    assert second_result == first_result
    assert sorted(fetched_signs) == sorted(horoscope.horo_list)
    assert max_active_requests == horoscope._HTTP_CONCURRENCY


@pytest.mark.asyncio
async def test_failed_mail_page_is_not_cached(monkeypatch):
    attempts = 0

    async def fetch(sign):
        nonlocal attempts

        attempts += 1
        if attempts == 1:
            raise asyncio.TimeoutError
        return horoscope.HoroscopePage("full", "first")

    monkeypatch.setattr(horoscope, "_fetch_mail_page", fetch)

    assert await horoscope.get_horoscope("aries") == (
        "Не удалось загрузить гороскоп. Попробуйте позже."
    )
    assert await horoscope.get_horoscope("aries") == "first"
    assert attempts == 2
