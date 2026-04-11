#!/usr/bin/env python3

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
FX_RATES_PATH = REPO_ROOT / "data" / "fx_rates.json"

README_START_MARKER = "<!-- FX_RATES_TABLE:START -->"
README_END_MARKER = "<!-- FX_RATES_TABLE:END -->"

NATION_CURRENCY_MAP = [
    ("Australia", "AUD"),
    ("Brazil", "BRL"),
    ("Britain", "GBP"),
    ("Canada", "CAD"),
    ("China", "CNY"),
    ("Denmark", "DKK"),
    ("Dubai", "AED"),
    ("Finland", "EUR"),
    ("France", "EUR"),
    ("Germany", "EUR"),
    ("India", "INR"),
    ("Ireland", "EUR"),
    ("Israel", "ILS"),
    ("Japan", "JPY"),
    ("Luxembourg", "EUR"),
    ("Netherlands", "EUR"),
    ("Portugal", "EUR"),
    ("Russia", "RUB"),
    ("Singapore", "SGD"),
    ("South Korea", "KRW"),
    ("Spain", "EUR"),
    ("Switzerland", "CHF"),
    ("Taiwan", "TWD"),
    ("UAE", "AED"),
    ("USA", "USD"),
]


def load_fx_payload() -> dict:
    with FX_RATES_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_table_section(payload: dict) -> str:
    last_complete_update = payload.get("last_complete_update") or "Pending first complete generated update"
    last_partial_update = payload.get("last_partial_update") or "No partial update recorded"
    source_url = payload.get("provider") or "https://open.er-api.com/v6/latest/USD"
    nation_rates = payload.get("nation_rates") or []
    is_complete_update = payload.get("is_complete_update", False)
    missing_live_currencies = payload.get("missing_live_currencies") or []

    lines = [
        README_START_MARKER,
        "## Live FX Conversion Rates",
        "",
        f"Rates are fetched from live market data during deployment and used to normalize non-USD deal amounts into USD-equivalent values for dashboard cards and filters.",
        "",
        f"**Last Complete Update:** {last_complete_update}",
        "",
        f"**Last Partial Update:** {last_partial_update}",
        "",
        f"**Source:** {source_url}",
        "",
    ]

    if is_complete_update:
        lines.extend([
            "The most recent pipeline run refreshed every nation in the FX table with fresh live-market values.",
            "",
        ])
    elif missing_live_currencies:
        lines.extend([
            "The most recent pipeline run was partial, so the table is currently mixing fresh live-market values with previously stored or fallback values for:",
            "",
            f"`{', '.join(missing_live_currencies)}`",
            "",
        ])

    lines.extend([
        "| Nation | Currency | USD per 1 unit of local currency |",
        "|--------|----------|----------------------------------|",
    ])

    rates_by_nation = {
        entry.get("nation"): entry
        for entry in nation_rates
        if isinstance(entry, dict) and entry.get("nation")
    }

    for nation, currency in NATION_CURRENCY_MAP:
        entry = rates_by_nation.get(nation, {})
        rate = entry.get("usd_rate")
        rate_label = f"{rate:.8f}" if isinstance(rate, (int, float)) else "N/A"
        lines.append(f"| {nation} | {currency} | {rate_label} |")

    lines.extend(["", README_END_MARKER])
    return "\n".join(lines)


def update_readme(section: str) -> None:
    content = README_PATH.read_text(encoding="utf-8")

    start = content.find(README_START_MARKER)
    end = content.find(README_END_MARKER)

    if start == -1 or end == -1 or end < start:
        raise RuntimeError("README FX markers not found.")

    end += len(README_END_MARKER)
    updated = content[:start] + section + content[end:]
    README_PATH.write_text(updated, encoding="utf-8")


def main() -> None:
    payload = load_fx_payload()
    update_readme(build_table_section(payload))


if __name__ == "__main__":
    main()
