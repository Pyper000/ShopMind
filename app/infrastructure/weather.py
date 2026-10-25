# -*- coding: utf-8 -*-
"""Open-Meteo lookup used only for travel plans with a city and dates."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_DAILY_FORECAST = (
    "temperature_2m_min,temperature_2m_max,precipitation_probability_max,"
    "precipitation_sum,rain_sum,showers_sum,snowfall_sum,wind_speed_10m_max,uv_index_max"
)
_DAILY_ARCHIVE = "temperature_2m_min,temperature_2m_max,precipitation_sum,rain_sum,showers_sum,snowfall_sum,wind_speed_10m_max"
_TRAVEL_TERMS = ("旅游", "出行", "出差", "度假", "出游", "游玩")
_DATE_PATTERN = re.compile(
    r"(?<!\d)(?:(?P<year>20\d{2})年|(?P<iso_year>20\d{2})[-/.])?"
    r"(?P<month>\d{1,2})(?:月|[-/.])(?P<day>\d{1,2})日?"
)


def _parse_destination(query: str) -> Optional[str]:
    match = re.search(
        r"(?:目的地(?:是|为)?|去|到|前往|飞往|抵达)\s*"
        r"([\u3400-\u9fffA-Za-z·\s]{2,32}?)"
        r"(?:\s*[（(](?:日本|Japan|JP)[）)])?"
        r"(?=旅行|旅游|出行|出差|度假|出游|游玩|[，,。；;、\d]|$)",
        query,
    )
    if not match:
        return None
    destination = re.sub(r"\s+", " ", match.group(1)).strip()
    return destination.removeprefix("日本") if destination.startswith("日本") and len(destination) > 2 else destination


def _parse_dates(query: str, today: date) -> tuple[Optional[date], Optional[date]]:
    matches = list(_DATE_PATTERN.finditer(query))
    dates: list[date] = []
    for match in matches:
        year = int(match.group("year") or match.group("iso_year") or today.year)
        try:
            current = date(year, int(match.group("month")), int(match.group("day")))
        except ValueError:
            continue
        if dates and match.group("year") is None and match.group("iso_year") is None:
            current = current.replace(year=dates[0].year)
            if current < dates[0]:
                current = current.replace(year=current.year + 1)
        dates.append(current)

    if dates and matches[0].group("year") is None and matches[0].group("iso_year") is None and dates[0] < today:
        dates = [_shift_year(value, -1) for value in dates]

    if len(dates) == 1:
        # Accept the common abbreviated range “10月28日至30日”.
        trailing_day = re.search(r"(?:至|到|[-~～])\s*(\d{1,2})日?", query[matches[0].end():])
        if trailing_day:
            try:
                end = date(dates[0].year, dates[0].month, int(trailing_day.group(1)))
                if end < dates[0]:
                    end = end.replace(month=end.month + 1) if end.month < 12 else date(end.year + 1, 1, end.day)
                return dates[0], end
            except ValueError:
                pass
        duration_query = re.sub(
            r"(?:(?:20\d{2})(?:年|[-/.]))?\d{1,2}(?:月|[-/.])\d{1,2}日?"
            r"(?:(?:至|到|[-~～])\d{1,2}日?)?",
            "", query,
        )
        duration = re.search(r"(\d{1,2}|[一二两三四五六七八九十]+)\s*(?:天|日|晚|夜)", duration_query)
        if duration:
            digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                      "七": 7, "八": 8, "九": 9, "十": 10}
            raw = duration.group(1)
            count = int(raw) if raw.isdigit() else digits.get(raw, 1)
            return dates[0], dates[0] + timedelta(days=max(1, count) - 1)
        return dates[0], dates[0]
    if len(dates) >= 2:
        return dates[0], dates[-1]

    relative = next((offset for word, offset in (("大后天", 3), ("后天", 2), ("明天", 1), ("今天", 0)) if word in query), None)
    if relative is not None:
        start = today + timedelta(days=relative)
        if "周末" in query:
            start += timedelta(days=(5 - start.weekday()) % 7)
            return start, start + timedelta(days=1)
        return start, start
    if "周末" in query:
        start = today + timedelta(days=(5 - today.weekday()) % 7)
        return start, start + timedelta(days=1)
    return None, None


def parse_trip_weather_request(query: str, today: Optional[date] = None) -> Optional[dict]:
    """Return a small request only for travel text; dates are never guessed from locale."""
    travel_cues = ("准备", "清单", "出发", "行程", "天气", "穿衣", "周末", "日期", "目的地", "去", "到")
    is_trip = (
        any(term in query for term in _TRAVEL_TERMS)
        or ("旅行" in query and any(cue in query for cue in travel_cues))
        or bool(re.search(r"(?:去|到).{0,12}(?:玩|逛)", query))
    )
    if not is_trip:
        return None
    today = today or date.today()
    destination = _parse_destination(query)
    start, end = _parse_dates(query, today)
    missing = [field for field, value in (("destination", destination), ("travel_dates", start and end)) if not value]
    if start and end and (end < start or (end - start).days > 30):
        missing.append("a_trip_of_30_days_or_less")
    request = {
        "destination": destination,
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "missing": missing,
    }
    if destination == "东京" or re.search(r"日本|\bJapan\b|\bJP\b", query, re.IGNORECASE):
        request["country_code"] = "JP"
    return request


def _shift_year(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:  # Feb 29: compare against Feb 28 in non-leap years.
        return value.replace(year=value.year - years, day=28)


def _values(daily: dict, key: str) -> list[float]:
    return [float(value) for value in daily.get(key, []) if isinstance(value, (int, float))]


def _rain_values(daily: dict) -> list[float]:
    rain, showers = _values(daily, "rain_sum"), _values(daily, "showers_sum")
    return [
        (rain[index] if index < len(rain) else 0) + (showers[index] if index < len(showers) else 0)
        for index in range(max(len(rain), len(showers)))
    ]


def _mean_or_none(values: list[float]) -> Optional[float]:
    return round(mean(values), 1) if values else None


def _normalize_location(value: str) -> str:
    return re.sub(r"(?:市|县|区|省|州)$", "", re.sub(r"\s+", "", value).casefold())


class OpenMeteoWeatherClient:
    """Small, timeout-bounded client with a six-hour per-process response cache."""

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None, timeout: float = 4.0) -> None:
        self._transport = transport
        self._timeout = timeout
        self._cache: dict[tuple[str, str, str, str], tuple[float, dict]] = {}

    async def for_query(self, query: str, today: Optional[date] = None) -> Optional[dict]:
        today = today or date.today()
        request = parse_trip_weather_request(query, today)
        if request is None:
            return None
        if request["missing"]:
            return {"mode": "needs_details", **request, "provider": "Open-Meteo"}

        destination, start_text, end_text = request["destination"], request["start_date"], request["end_date"]
        country_code = request.get("country_code", "")
        key = (destination.casefold(), country_code, start_text, end_text)
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            return dict(cached[1])

        start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                geo_params = {"name": destination, "count": 5, "language": "zh"}
                if country_code:
                    geo_params["countryCode"] = country_code
                if country_code == "JP" and destination == "东京":
                    geo_params.update(name="Tokyo", language="en")
                geo_response = await client.get(_GEOCODING_URL, params=geo_params)
                geo_response.raise_for_status()
                results = geo_response.json().get("results") or []
                if not results:
                    return {"mode": "unresolved_location", **request, "provider": "Open-Meteo"}
                exact = [item for item in results if _normalize_location(str(item.get("name", ""))) == _normalize_location(geo_params["name"])]
                major_cities = [item for item in exact if item.get("feature_code") in {"PPLC", "PPLA"}]
                if len(major_cities) == 1:
                    location = major_cities[0]
                elif len(major_cities) > 1:
                    return {
                        "mode": "ambiguous_location", **request, "provider": "Open-Meteo",
                        "candidates": list(dict.fromkeys(
                            "，".join(filter(None, (item.get("name"), item.get("admin1"), item.get("country"))))
                            for item in major_cities
                        ))[:5],
                    }
                elif len(exact) == 1:
                    location = exact[0]
                elif len(results) == 1:
                    location = results[0]
                else:
                    candidates = list(dict.fromkeys(
                        "，".join(filter(None, (item.get("name"), item.get("admin1"), item.get("country"))))
                        for item in results
                    ))
                    return {
                        "mode": "ambiguous_location", **request, "provider": "Open-Meteo",
                        "candidates": candidates[:5],
                    }
                location_name = "，".join(filter(None, (
                    location.get("name"), location.get("admin1"), location.get("country"),
                )))
                if start >= today and end <= today + timedelta(days=15):
                    response = await client.get(_FORECAST_URL, params={
                        "latitude": location["latitude"], "longitude": location["longitude"],
                        "daily": _DAILY_FORECAST, "timezone": "auto",
                        "start_date": start_text, "end_date": end_text,
                    })
                    response.raise_for_status()
                    context = self._forecast_context(response.json(), request, location_name)
                else:
                    context = await self._climate_context(client, location, request, location_name, today)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as err:
            logger.warning("Open-Meteo 查询失败，退回通用出行清单：%s", err)
            return {"mode": "unavailable", **request, "provider": "Open-Meteo"}

        self._cache[key] = (time.monotonic() + 6 * 60 * 60, context)
        return dict(context)

    @staticmethod
    def _forecast_context(data: dict, request: dict, location_name: str) -> dict:
        daily = data.get("daily") or {}
        dates = daily.get("time") or []
        if not dates:
            raise ValueError("forecast response has no daily data")
        rain = _rain_values(daily)
        snow = _values(daily, "snowfall_sum")
        return {
            "mode": "forecast", "provider": "Open-Meteo", "location": location_name,
            "timezone": data.get("timezone"),
            "start_date": request["start_date"], "end_date": request["end_date"],
            "temperature_min_c": min(_values(daily, "temperature_2m_min"), default=None),
            "temperature_max_c": max(_values(daily, "temperature_2m_max"), default=None),
            "precipitation_probability_max_pct": max(_values(daily, "precipitation_probability_max"), default=None),
            "precipitation_mm_sum": round(sum(_values(daily, "precipitation_sum")), 1),
            "rainfall_mm_sum": round(sum(rain), 1) if rain else None,
            "rain_days": sum(value >= 0.1 for value in rain) if rain else None,
            "snowfall_cm_sum": round(sum(snow), 1) if snow else None,
            "snow_days": sum(value >= 0.1 for value in snow) if snow else None,
            "wind_speed_kmh_max": max(_values(daily, "wind_speed_10m_max"), default=None),
            "uv_index_max": max(_values(daily, "uv_index_max"), default=None),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _climate_context(
        self, client: httpx.AsyncClient, location: dict, request: dict,
        location_name: str, today: date,
    ) -> dict:
        start, end = date.fromisoformat(request["start_date"]), date.fromisoformat(request["end_date"])
        periods = [(_shift_year(start, years), _shift_year(end, years), years) for years in range(1, 6)]
        periods = [(first, last, years) for first, last, years in periods if last < today - timedelta(days=2)][:3]
        if not periods:
            return {"mode": "unavailable", **request, "provider": "Open-Meteo"}

        async def fetch(first: date, last: date) -> dict:
            response = await client.get(_ARCHIVE_URL, params={
                "latitude": location["latitude"], "longitude": location["longitude"],
                "daily": _DAILY_ARCHIVE, "timezone": "auto",
                "start_date": first.isoformat(), "end_date": last.isoformat(),
            })
            response.raise_for_status()
            return response.json().get("daily") or {}

        histories = await asyncio.gather(*(fetch(first, last) for first, last, _ in periods))
        lows = [value for data in histories for value in _values(data, "temperature_2m_min")]
        highs = [value for data in histories for value in _values(data, "temperature_2m_max")]
        rain_values = [_rain_values(data) for data in histories]
        snow_values = [_values(data, "snowfall_sum") for data in histories]
        precipitation_by_year = [sum(_values(data, "precipitation_sum")) for data in histories]
        rain_by_year = [sum(values) for values in rain_values if values]
        rain_days_by_year = [sum(value >= 0.1 for value in values) for values in rain_values if values]
        snow_by_year = [sum(values) for values in snow_values if values]
        snow_days_by_year = [sum(value >= 0.1 for value in values) for values in snow_values if values]
        winds = [value for data in histories for value in _values(data, "wind_speed_10m_max")]
        if not lows or not highs:
            raise ValueError("archive response has no daily temperature data")
        return {
            "mode": "climate_reference", "provider": "Open-Meteo", "location": location_name,
            "timezone": location.get("timezone"),
            "start_date": request["start_date"], "end_date": request["end_date"],
            "sample_years": [start.year - years for _, _, years in periods],
            "temperature_min_c": _mean_or_none(lows), "temperature_max_c": _mean_or_none(highs),
            "precipitation_probability_max_pct": None,
            "precipitation_mm_sum": _mean_or_none(precipitation_by_year),
            "rainfall_mm_sum": _mean_or_none(rain_by_year),
            "rain_days": _mean_or_none(rain_days_by_year),
            "snowfall_cm_sum": _mean_or_none(snow_by_year),
            "snow_days": _mean_or_none(snow_days_by_year),
            "wind_speed_kmh_max": _mean_or_none(winds), "uv_index_max": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
