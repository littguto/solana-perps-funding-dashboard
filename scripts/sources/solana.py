"""Funding rates de exchanges de perpétuos do ecossistema Solana.

Cada função retorna uma lista de dicts no formato padronizado:

    {
        "exchange": "velocity",
        "market": "SOL-PERP",
        "rate_hourly": 0.0000125,   # fração por hora (0.0000125 = 0,00125%/h)
        "apr": 0.1095,              # rate_hourly * 24 * 365, sem capitalização
    }

Todas as taxas são o último funding liquidado (não a estimativa do próximo),
com sinal positivo = longs pagam shorts.
"""

from __future__ import annotations

import time

import requests

HOURS_PER_YEAR = 24 * 365
TIMEOUT = 15

VELOCITY_CONTRACTS_URL = "https://data.velocity.exchange/external/coingecko/contracts"
PACIFICA_PRICES_URL = "https://api.pacifica.fi/api/v1/info/prices"
PHOENIX_FUNDING_URL = "https://perp-api.phoenix.trade/v1/funding/overview"


def _row(exchange: str, symbol: str, rate_hourly: float) -> dict:
    base = symbol.upper().removesuffix("-PERP")
    return {
        "exchange": exchange,
        "market": f"{base}-PERP",
        "rate_hourly": rate_hourly,
        "apr": rate_hourly * HOURS_PER_YEAR,
    }


def _get_json(url: str, **params) -> dict | list:
    resp = requests.get(url, params=params or None, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_velocity() -> list[dict]:
    """Velocity (ex-Drift). Funding horário.

    `funding_rate` vem em PERCENTUAL por hora. A unidade não é documentada;
    foi validada contra /market/{symbol}/fundingRates, onde
    fundingRate / oraclePriceTwap * 100 == funding_rate.
    """
    data = _get_json(VELOCITY_CONTRACTS_URL)
    return [
        _row("velocity", c["ticker_id"], float(c["funding_rate"]) / 100)
        for c in data["contracts"]
        if c.get("product_type") == "PERP" and c.get("funding_rate") is not None
    ]


def fetch_pacifica() -> list[dict]:
    """Pacifica. Funding horário; `funding` já é fração por hora."""
    data = _get_json(PACIFICA_PRICES_URL)
    if not data.get("success"):
        raise RuntimeError(f"Pacifica error: {data.get('error')}")
    return [_row("pacifica", m["symbol"], float(m["funding"])) for m in data["data"]]


def fetch_phoenix() -> list[dict]:
    """Phoenix. Funding calculado de hora em hora (liquidado a cada 24h).

    O campo `fundingRate` da resposta está em percentual, apesar da doc dizer
    "decimal"; por isso a taxa é calculada como fundingAmountPerUnit / markPrice.
    Os pontos vêm do mais antigo para o mais recente.
    """
    start_ms = int((time.time() - 3 * 3600) * 1000)
    data = _get_json(PHOENIX_FUNDING_URL, startTime=start_ms)
    rows = []
    for series in data["series"]:
        if not series["points"]:
            continue
        last = series["points"][-1]
        mark = float(last["markPrice"])
        if mark <= 0:
            continue
        rows.append(_row("phoenix", series["symbol"], float(last["fundingAmountPerUnit"]) / mark))
    return rows


FETCHERS = {
    "velocity": fetch_velocity,
    "pacifica": fetch_pacifica,
    "phoenix": fetch_phoenix,
}


def fetch_all() -> list[dict]:
    """Roda todos os fetchers; uma exchange fora do ar não derruba as outras."""
    rows: list[dict] = []
    for name, fetch in FETCHERS.items():
        try:
            rows.extend(fetch())
        except (requests.RequestException, KeyError, ValueError, RuntimeError) as exc:
            print(f"[solana] {name} falhou: {exc}")
    return rows


if __name__ == "__main__":
    for r in fetch_all():
        if r["market"] in {"SOL-PERP", "BTC-PERP", "ETH-PERP"}:
            print(f"{r['exchange']:<9} {r['market']:<9} {r['rate_hourly']*100:+.5f}%/h  APR {r['apr']*100:+.2f}%")
