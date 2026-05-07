#!/usr/bin/env python3

import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
MANIFEST_PATH = DATA_DIR / "manifest.json"
LAST_UPDATED_PATH = DATA_DIR / "last_updated.txt"
OUTLIER_PATH = DATA_DIR / "outlier.json"
FX_RATES_PATH = DATA_DIR / "fx_rates.json"
MAX_REALISTIC_DEAL_AMOUNT_USD = 250_000_000_000
FX_API_URL = "https://open.er-api.com/v6/latest/USD"
CHICAGO_TZ = ZoneInfo("America/Chicago")
USD_MARKER_PATTERN = re.compile(r"\b(?:usd|us\$)\b|\$", re.IGNORECASE)
USD_PREFIX_PATTERN = re.compile(
    r"(?:US\$|USD|\$)\s*\d[\d,]*(?:\.\d+)?(?:\s*[tmbk])?(?:\s*(?:trillion|tn|billion|bn|million|mn|thousand))?",
    re.IGNORECASE,
)
USD_SUFFIX_PATTERN = re.compile(
    r"\d[\d,]*(?:\.\d+)?(?:\s*[tmbk])?(?:\s*(?:trillion|tn|billion|bn|million|mn|thousand))?\s*(?:US\$|USD)",
    re.IGNORECASE,
)
GENERIC_AMOUNT_PATTERN = re.compile(
    r"\d[\d,]*(?:\.\d+)?(?:\s*[tmbk])?(?:\s*(?:trillion|tn|billion|bn|million|mn|thousand))?",
    re.IGNORECASE,
)
NATION_CURRENCY_MAP = {
    "Australia": "AUD",
    "Brazil": "BRL",
    "Britain": "GBP",
    "Canada": "CAD",
    "China": "CNY",
    "Denmark": "DKK",
    "Dubai": "AED",
    "Finland": "EUR",
    "France": "EUR",
    "Germany": "EUR",
    "India": "INR",
    "Ireland": "EUR",
    "Israel": "ILS",
    "Japan": "JPY",
    "Luxembourg": "EUR",
    "Netherlands": "EUR",
    "Portugal": "EUR",
    "Russia": "RUB",
    "Singapore": "SGD",
    "South Korea": "KRW",
    "Spain": "EUR",
    "Switzerland": "CHF",
    "Taiwan": "TWD",
    "UAE": "AED",
    "USA": "USD",
}
FALLBACK_CURRENCY_TO_USD_RATE = {
    "USD": 1,
    "USDC": 1,
    "AED": 0.2723,
    "AUD": 0.66,
    "BRL": 0.198,
    "CAD": 0.74,
    "CHF": 1.27,
    "CNY": 0.138,
    "DKK": 0.145,
    "EUR": 1.09,
    "GBP": 1.28,
    "ILS": 0.27,
    "INR": 0.012,
    "JPY": 0.0067,
    "KRW": 0.00069,
    "RUB": 0.0129,
    "SEK": 0.094,
    "SGD": 0.74,
    "TWD": 0.031,
    "ZAR": 0.053,
}


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def clean_string(value: Any) -> str:
    return str(value or "").strip()


def format_ct_timestamp(value: datetime) -> str:
    return value.astimezone(CHICAGO_TZ).strftime("%Y-%m-%d %H:%M CT")


def load_existing_fx_registry() -> dict:
    if not FX_RATES_PATH.exists():
        return {}

    payload = load_json(FX_RATES_PATH)
    return payload if isinstance(payload, dict) else {}


def fetch_live_fx_payload() -> dict:
    with urllib.request.urlopen(FX_API_URL, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def build_fx_rate_registry(generated_at: str, generated_at_ct: str) -> dict:
    existing_registry = load_existing_fx_registry()
    previous_rates = existing_registry.get("currency_to_usd_rate", {})
    if not isinstance(previous_rates, dict):
        previous_rates = {}

    try:
        payload = fetch_live_fx_payload()
        if payload.get("result") != "success":
            raise ValueError(f"Unexpected FX API status: {payload.get('result')}")
        provider = clean_string(payload.get("provider")) or FX_API_URL
        fetched_at = clean_string(payload.get("time_last_update_utc")) or generated_at
        source_rates = payload.get("rates") or {}
        fetched_live = True
    except Exception as error:  # pragma: no cover - fallback path still intentional
        provider = f"{FX_API_URL} (fallback)"
        fetched_at = generated_at
        source_rates = {}
        fetched_live = False
        print(f"Warning: failed to fetch live FX rates, using fallback values. {error}")

    currency_rates: dict[str, float] = {}
    currency_sources: dict[str, str] = {}
    for currency, fallback_rate in FALLBACK_CURRENCY_TO_USD_RATE.items():
        if currency in {"USD", "USDC"}:
            currency_rates[currency] = 1
            currency_sources[currency] = "fixed"
            continue

        per_usd = source_rates.get(currency)
        if isinstance(per_usd, (int, float)) and per_usd:
            currency_rates[currency] = 1 / float(per_usd)
            currency_sources[currency] = "live"
        elif isinstance(previous_rates.get(currency), (int, float)) and previous_rates.get(currency):
            currency_rates[currency] = float(previous_rates[currency])
            currency_sources[currency] = "previous"
        else:
            currency_rates[currency] = fallback_rate
            currency_sources[currency] = "fallback"

    currency_rates["USD"] = 1
    currency_rates["USDC"] = 1
    currency_sources["USD"] = "fixed"
    currency_sources["USDC"] = "fixed"

    required_nation_currencies = sorted({currency for currency in NATION_CURRENCY_MAP.values()})
    missing_live_currencies = [
        currency
        for currency in required_nation_currencies
        if currency_sources.get(currency) not in {"live", "fixed"}
    ]
    is_complete_update = not missing_live_currencies

    last_complete_update = clean_string(existing_registry.get("last_complete_update"))
    last_partial_update = clean_string(existing_registry.get("last_partial_update"))

    if is_complete_update:
        last_complete_update = generated_at_ct
    else:
        last_partial_update = generated_at_ct

    nation_rates = []
    for nation, currency in sorted(NATION_CURRENCY_MAP.items()):
        nation_rates.append(
            {
                "nation": nation,
                "currency": currency,
                "usd_rate": currency_rates.get(currency, 1),
                "rate_source": currency_sources.get(currency, "fallback"),
            }
        )

    return {
        "updated_at": generated_at,
        "updated_at_ct": generated_at_ct,
        "source_updated_at": fetched_at,
        "provider": provider,
        "fetched_live": fetched_live,
        "is_complete_update": is_complete_update,
        "last_complete_update": last_complete_update,
        "last_partial_update": last_partial_update,
        "missing_live_currencies": missing_live_currencies,
        "base_currency": "USD",
        "currency_to_usd_rate": currency_rates,
        "nation_rates": nation_rates,
    }


def detect_nation_name(payload: dict, fallback_name: str) -> str:
    deals = payload.get("deals", [])
    for deal in deals:
        nation = clean_string(deal.get("Nation") or deal.get("Country"))
        if nation:
            return nation
    return fallback_name


def match_amount_candidates(value: str, pattern: re.Pattern[str]) -> list[str]:
    return [match.group(0) for match in pattern.finditer(clean_string(value))]


def parse_amount_candidate(value: str) -> float | None:
    normalized = clean_string(value).lower().replace(",", "").strip()
    number_match = re.search(r"(\d+(?:\.\d+)?)", normalized)

    if not number_match:
        return None

    numeric_value = float(number_match.group(1))

    if re.search(r"\btrillion\b|\btn\b", normalized):
        numeric_value *= 1e12
    elif re.search(r"\bbillion\b|\bbn\b", normalized):
        numeric_value *= 1e9
    elif re.search(r"\bmillion\b|\bmn\b", normalized):
        numeric_value *= 1e6
    elif re.search(r"\bthousand\b|(?<![a-z])k(?![a-z])", normalized):
        numeric_value *= 1e3
    elif re.search(r"(?<=\d)\s+m(?![a-z])", normalized, re.I):
        numeric_value *= 1e6
    elif re.search(r"(?<=\d)\s+b(?![a-z])", normalized, re.I):
        numeric_value *= 1e9
    elif re.search(r"(?<=\d)\s+t(?![a-z])", normalized, re.I):
        numeric_value *= 1e12
    elif re.search(r"(?<=\d)t(?![a-z])", normalized, re.I):
        numeric_value *= 1e12
    elif re.search(r"(?<=\d)b(?![a-z])", normalized, re.I):
        numeric_value *= 1e9
    elif re.search(r"(?<=\d)m(?![a-z])", normalized, re.I):
        numeric_value *= 1e6

    if re.search(r"\bcrore\b|\bcr\b", normalized):
        numeric_value *= 1e7
    if re.search(r"\blakh\b|\blac\b", normalized):
        numeric_value *= 1e5

    return numeric_value


def detect_amount_currency(value: Any) -> str:
    raw = clean_string(value)
    cleaned = raw.upper()

    if not cleaned:
        return ""

    if re.search(r"[\u5104]\s*[\u5186]", raw):
        return "JPY"

    if re.search(r"\b(CRORE|CR)\b", raw, re.I) or "₹" in raw or re.search(r"\bINR\b", cleaned):
        return "INR"

    if re.search(r"\u4ebf\s*\u5143", raw) or re.search(r"\u4ebf\s*\u4eba\u6c11\u5e01", raw) or re.search(r"\bRMB\b", cleaned, re.I):
        return "CNY"

    if re.search(r"(?:US\$|USD|\$)", cleaned):
        return "USD"

    currency_matchers = [
        ("AED", r"\bAED\b"),
        ("AUD", r"\bAUD\b"),
        ("CAD", r"\bCAD\b"),
        ("CNY", r"\bCNY\b"),
        ("DKK", r"\bDKK\b"),
        ("EUR", r"\bEUR\b|€"),
        ("GBP", r"\bGBP\b|£"),
        ("ILS", r"\bILS\b"),
        ("INR", r"\bINR\b|₹"),
        ("JPY", r"\bJPY\b|¥"),
        ("KRW", r"\bKRW\b|₩"),
        ("SEK", r"\bSEK\b"),
        ("SGD", r"\bSGD\b"),
        ("USDC", r"\bUSDC\b"),
        ("ZAR", r"\bZAR\b"),
    ]

    for currency, pattern in currency_matchers:
        if re.search(pattern, cleaned):
            return currency

    return ""


def convert_amount_to_usd(value: float, currency: str, currency_to_usd_rate: dict[str, float]) -> float | None:
    if currency in {"", "USD"}:
        return value

    rate = currency_to_usd_rate.get(currency)
    if rate is None:
        return None

    return value * rate


def detect_non_financial_amount_text(raw: str) -> bool:
    s = clean_string(raw).lower()
    if not s:
        return False
    if re.search(r"\(in-kind support\)|\bin-kind support\b|\bin kind support\b", s):
        return True
    if re.match(
        r"^\s*\d+\s+[^$€£¥₹]{0,80}\b(gpu|gpus)\b",
        clean_string(raw),
        re.I,
    ) and not re.search(r"\$\s*[\d,]+(?:\.\d+)?(?:[-–]|\s+)(million|billion)", raw, re.I):
        return True
    return False


def parse_parenthetical_usd_approximation(raw: str) -> dict[str, Any] | None:
    m = re.search(
        r"\(\s*approximately\s*\$?\s*([\d.,]+)\s*[–-]\s*([\d.,]+)\s*million\s*usd?\s*\)",
        clean_string(raw),
        re.I,
    )
    if not m:
        return None
    lo = float(str(m.group(1)).replace(",", ""))
    hi = float(str(m.group(2)).replace(",", ""))
    mid_usd = ((lo + hi) / 2) * 1e6
    return {
        "usd_value": mid_usd,
        "currency": "USD",
        "original_value": mid_usd,
        "is_converted": False,
        "amount_kind": "monetary",
    }


def parse_hyphenated_usd_magnitude(raw: str) -> dict[str, Any] | None:
    m = re.search(
        r"\$\s*([\d,]+(?:\.\d+)?)(?:[-–]|\s+)(million|billion|trillion|thousand)\b",
        clean_string(raw),
        re.I,
    )
    if not m:
        return None
    num = float(str(m.group(1)).replace(",", ""))
    unit = m.group(2).lower()
    if unit == "trillion":
        num *= 1e12
    elif unit == "billion":
        num *= 1e9
    elif unit == "million":
        num *= 1e6
    elif unit == "thousand":
        num *= 1e3
    return {
        "usd_value": num,
        "currency": "USD",
        "original_value": num,
        "is_converted": False,
        "amount_kind": "monetary",
    }


def parse_chinese_usd_yi_string(raw: str) -> dict[str, Any] | None:
    s = clean_string(raw)
    for pattern in (
        r"(?:超|超过)\s*(\d+(?:\.\d+)?)\s*亿\s*美元",
        r"(\d+(?:\.\d+)?)\s*亿\s*美元",
    ):
        m = re.search(pattern, s)
        if not m:
            continue
        num = float(m.group(1))
        usd = num * 1e8
        return {
            "usd_value": usd,
            "currency": "USD",
            "original_value": usd,
            "is_converted": False,
            "amount_kind": "monetary",
        }
    return None


def parse_chinese_cny_yi_string(raw: str, currency_to_usd_rate: dict[str, float]) -> dict[str, Any] | None:
    s = clean_string(raw)
    for pattern in (
        r"(?:超|超过)\s*(\d+(?:\.\d+)?)\s*亿\s*元",
        r"(\d+(?:\.\d+)?)\s*亿\s*元",
        r"(?:超|超过)\s*(\d+(?:\.\d+)?)\s*亿\s*人民币",
        r"(\d+(?:\.\d+)?)\s*亿\s*人民币",
    ):
        m = re.search(pattern, s)
        if not m:
            continue
        num = float(m.group(1))
        cny = num * 1e8
        usd_value = convert_amount_to_usd(cny, "CNY", currency_to_usd_rate)
        if usd_value is None:
            return None
        return {
            "usd_value": usd_value,
            "currency": "CNY",
            "original_value": cny,
            "is_converted": True,
            "amount_kind": "monetary",
        }
    return None


def parse_japanese_okuen_yen(raw: str, currency_to_usd_rate: dict[str, float]) -> dict[str, Any] | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*[\u5104]\s*[\u5186]", clean_string(raw))
    if not m:
        return None
    num = float(m.group(1))
    jpy = num * 1e8
    usd_value = convert_amount_to_usd(jpy, "JPY", currency_to_usd_rate)
    if usd_value is None:
        return None
    return {
        "usd_value": usd_value,
        "currency": "JPY",
        "original_value": jpy,
        "is_converted": True,
        "amount_kind": "monetary",
    }


def parse_inr_crore_from_string(raw: str, currency_to_usd_rate: dict[str, float]) -> dict[str, Any] | None:
    s = clean_string(raw).lower().replace(",", "")
    if not re.search(r"\bcrore|\bcr\b", s):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:crore|cr)\b", s)
    if not m:
        return None
    num = float(m.group(1))
    inr = num * 1e7
    usd_value = convert_amount_to_usd(inr, "INR", currency_to_usd_rate)
    if usd_value is None:
        return None
    return {
        "usd_value": usd_value,
        "currency": "INR",
        "original_value": inr,
        "is_converted": True,
        "amount_kind": "monetary",
    }


def parse_western_yen_magnitude(raw: str, currency_to_usd_rate: dict[str, float]) -> dict[str, Any] | None:
    m = re.search(
        r"[¥\u00a5]\s*([\d,]+(?:\.\d+)?)\s*(million|billion|trillion)\b",
        clean_string(raw),
        re.I,
    )
    if not m:
        return None
    num = float(str(m.group(1)).replace(",", ""))
    unit = m.group(2).lower()
    if unit == "trillion":
        num *= 1e12
    elif unit == "billion":
        num *= 1e9
    elif unit == "million":
        num *= 1e6
    usd_value = convert_amount_to_usd(num, "JPY", currency_to_usd_rate)
    if usd_value is None:
        return None
    return {
        "usd_value": usd_value,
        "currency": "JPY",
        "original_value": num,
        "is_converted": True,
        "amount_kind": "monetary",
    }


def parse_structured_amount_string(raw: str, currency_to_usd_rate: dict[str, float]) -> dict[str, Any] | None:
    if detect_non_financial_amount_text(raw):
        return {
            "usd_value": None,
            "currency": "",
            "original_value": None,
            "is_converted": False,
            "amount_kind": "non_monetary",
        }
    hit = parse_chinese_usd_yi_string(raw)
    if hit:
        return hit
    hit = parse_chinese_cny_yi_string(raw, currency_to_usd_rate)
    if hit:
        return hit
    hit = parse_japanese_okuen_yen(raw, currency_to_usd_rate)
    if hit:
        return hit
    hit = parse_western_yen_magnitude(raw, currency_to_usd_rate)
    if hit:
        return hit
    hit = parse_inr_crore_from_string(raw, currency_to_usd_rate)
    if hit:
        return hit
    hit = parse_hyphenated_usd_magnitude(raw)
    if hit:
        return hit
    hit = parse_parenthetical_usd_approximation(raw)
    if hit:
        return hit
    return None


def should_skip_trivial_usd_after_yen_lead(raw: str, candidate: str, match_index: int) -> bool:
    m_yen = re.search(r"[¥\u00a5]", raw)
    if not m_yen or match_index < m_yen.start():
        return False
    if re.search(r"\b(million|billion|trillion|thousand)\b", candidate, re.I):
        return False
    return True


def parse_amount_info(value: Any, currency_to_usd_rate: dict[str, float]) -> dict[str, Any]:
    raw = clean_string(value)
    if not raw:
        return {
            "usd_value": None,
            "currency": "",
            "original_value": None,
            "is_converted": False,
            "amount_kind": "unspecified",
        }

    normalized = raw.lower().replace(",", "").strip()
    if normalized in {"unknown", "undisclosed", "not disclosed", "n/a", "na", "-", "nil"}:
        return {
            "usd_value": None,
            "currency": "",
            "original_value": None,
            "is_converted": False,
            "amount_kind": "unspecified",
        }

    structured = parse_structured_amount_string(raw, currency_to_usd_rate)
    if structured is not None:
        return structured

    hit = parse_western_yen_magnitude(raw, currency_to_usd_rate)
    if hit:
        return hit
    hit = parse_inr_crore_from_string(raw, currency_to_usd_rate)
    if hit:
        return hit
    hit = parse_chinese_usd_yi_string(raw)
    if hit:
        return hit
    hit = parse_chinese_cny_yi_string(raw, currency_to_usd_rate)
    if hit:
        return hit
    hit = parse_hyphenated_usd_magnitude(raw)
    if hit:
        return hit

    usd_lead_pattern = re.compile(
        r"(?:US\$|USD|\$)\s*\d[\d,]*(?:\.\d+)?(?:\s*[tmbk])?(?:\s*(?:trillion|tn|billion|bn|million|mn|thousand))?",
        re.I,
    )
    for m in usd_lead_pattern.finditer(raw):
        candidate = m.group(0)
        if should_skip_trivial_usd_after_yen_lead(raw, candidate, m.start()):
            continue
        parsed_candidate = parse_amount_candidate(candidate)
        if parsed_candidate is not None:
            return {
                "usd_value": parsed_candidate,
                "currency": "USD",
                "original_value": parsed_candidate,
                "is_converted": False,
                "amount_kind": "monetary",
            }

    for m in USD_SUFFIX_PATTERN.finditer(raw):
        candidate = m.group(0)
        if should_skip_trivial_usd_after_yen_lead(raw, candidate, m.start()):
            continue
        parsed_candidate = parse_amount_candidate(candidate)
        if parsed_candidate is not None:
            return {
                "usd_value": parsed_candidate,
                "currency": "USD",
                "original_value": parsed_candidate,
                "is_converted": False,
                "amount_kind": "monetary",
            }

    currency = detect_amount_currency(raw)

    for candidate in match_amount_candidates(raw, GENERIC_AMOUNT_PATTERN):
        parsed_candidate = parse_amount_candidate(candidate)
        if parsed_candidate is None:
            continue

        usd_value = convert_amount_to_usd(parsed_candidate, currency or "USD", currency_to_usd_rate)
        return {
            "usd_value": usd_value,
            "currency": currency or "USD",
            "original_value": parsed_candidate,
            "is_converted": bool(currency and currency != "USD"),
            "amount_kind": "monetary",
        }

    return {
        "usd_value": None,
        "currency": currency,
        "original_value": None,
        "is_converted": False,
        "amount_kind": "unspecified",
    }


def parse_amount(value: Any, currency_to_usd_rate: dict[str, float]) -> float | None:
    return parse_amount_info(value, currency_to_usd_rate)["usd_value"]


def get_outlier_reason(amount_info: dict[str, Any]) -> tuple[str, float | None]:
    amount_value = amount_info["usd_value"]

    if amount_value is not None and amount_value > MAX_REALISTIC_DEAL_AMOUNT_USD:
        return (
            f"Amount exceeds safety cap of ${MAX_REALISTIC_DEAL_AMOUNT_USD / 1e9:.0f}B",
            amount_value,
        )

    return "", amount_value


def build_outlier_id(source_file: str, nation_name: str, deal: dict) -> str:
    fingerprint = "||".join(
        [
            source_file,
            clean_string(nation_name),
            clean_string(deal.get("Startup_Name")),
            clean_string(deal.get("Amount")),
            clean_string(deal.get("Round")),
            clean_string(deal.get("Date_Captured") or deal.get("Date")),
        ]
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def build_outlier_records(currency_to_usd_rate: dict[str, float]) -> list[dict]:
    records: dict[str, dict] = {}

    for json_path in sorted(DATA_DIR.glob("*.json"), key=lambda item: item.name.lower()):
        if json_path.name in {MANIFEST_PATH.name, OUTLIER_PATH.name}:
            continue

        payload = load_json(json_path)
        deals = payload.get("deals", [])
        nation_name = detect_nation_name(payload, json_path.stem)

        for deal in deals:
            amount_info = parse_amount_info(deal.get("Amount"), currency_to_usd_rate)
            reason, amount_value = get_outlier_reason(amount_info)
            if not reason:
                continue

            outlier_id = build_outlier_id(json_path.name, nation_name, deal)
            records[outlier_id] = {
                "id": outlier_id,
                "nation": nation_name,
                "startup_name": clean_string(deal.get("Startup_Name")) or "Unknown",
                "source_file": json_path.name,
                "amount": clean_string(deal.get("Amount")),
                "amount_currency": amount_info["currency"],
                "original_amount_value": amount_info["original_value"],
                "amount_was_converted": amount_info["is_converted"],
                "parsed_amount_usd": amount_value,
                "round": clean_string(deal.get("Round")),
                "date_captured": clean_string(deal.get("Date_Captured") or deal.get("Date")),
                "reason": reason,
            }

    return list(records.values())


def load_existing_outlier_records() -> list[dict]:
    if not OUTLIER_PATH.exists():
        return []

    payload = load_json(OUTLIER_PATH)
    if isinstance(payload, dict):
        records = payload.get("records")
        return records if isinstance(records, list) else []

    if isinstance(payload, list):
        return payload

    return []


def build_outlier_registry(generated_at: str, currency_to_usd_rate: dict[str, float]) -> dict:
    existing_records = load_existing_outlier_records()
    current_records = build_outlier_records(currency_to_usd_rate)
    current_ids = {record["id"] for record in current_records}
    merged_records = {
        record["id"]: record
        for record in existing_records
        if isinstance(record, dict) and record.get("id")
    }

    for record in current_records:
        existing = merged_records.get(record["id"], {})
        detection_count = int(existing.get("detection_count", 0)) + 1
        merged_records[record["id"]] = {
            **existing,
            **record,
            "first_detected_at": existing.get("first_detected_at", generated_at),
            "last_detected_at": generated_at,
            "detection_count": detection_count,
            "currently_detected": True,
        }

    for record_id, record in merged_records.items():
        if record_id not in current_ids:
            record["currently_detected"] = False

    ordered_records = sorted(
        merged_records.values(),
        key=lambda record: (
            record.get("currently_detected") is not True,
            record.get("last_detected_at") or "",
            record.get("nation") or "",
            record.get("startup_name") or "",
        ),
        reverse=False,
    )

    return {
        "updated_at": generated_at,
        "max_realistic_deal_amount_usd": MAX_REALISTIC_DEAL_AMOUNT_USD,
        "record_count": len(ordered_records),
        "currently_detected_count": sum(1 for record in ordered_records if record.get("currently_detected")),
        "records": ordered_records,
    }


def build_manifest(generated_at: str) -> dict:
    nations = []

    for json_path in sorted(DATA_DIR.glob("*.json"), key=lambda item: item.name.lower()):
        if json_path.name in {MANIFEST_PATH.name, OUTLIER_PATH.name}:
            continue

        payload = load_json(json_path)
        deals = payload.get("deals", [])
        nation_name = detect_nation_name(payload, json_path.stem)

        nations.append(
            {
                "name": nation_name,
                "file": json_path.name,
                "path": f"data/{json_path.name}",
                "deal_count": len(deals),
                "last_updated": payload.get("last_updated"),
            }
        )

    nations.sort(key=lambda item: item["name"].lower())

    last_updated_text = None
    if LAST_UPDATED_PATH.exists():
        last_updated_text = LAST_UPDATED_PATH.read_text(encoding="utf-8").strip() or None

    if not last_updated_text:
        nation_timestamps = [n["last_updated"] for n in nations if n.get("last_updated")]
        if nation_timestamps:
            last_updated_text = max(nation_timestamps)

    manifest = {
        "generated_at": generated_at,
        "last_updated": last_updated_text,
        "nations": nations,
    }

    return manifest


def main() -> None:
    generated_at_dt = datetime.now(timezone.utc)
    generated_at = generated_at_dt.isoformat(timespec="seconds")
    generated_at_ct = format_ct_timestamp(generated_at_dt)
    fx_registry = build_fx_rate_registry(generated_at, generated_at_ct)
    manifest = build_manifest(generated_at)
    outlier_registry = build_outlier_registry(generated_at, fx_registry["currency_to_usd_rate"])
    MANIFEST_PATH.write_text(f"{json.dumps(manifest, indent=2)}\n", encoding="utf-8")
    FX_RATES_PATH.write_text(f"{json.dumps(fx_registry, indent=2)}\n", encoding="utf-8")
    OUTLIER_PATH.write_text(f"{json.dumps(outlier_registry, indent=2)}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
