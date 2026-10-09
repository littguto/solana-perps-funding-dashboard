"""Funding rates de CEXs (Binance, Bybit, OKX) via ccxt.

Adaptado do funding_scanner.py. Retorna o mesmo formato de sources/solana.py:

    {"exchange": "binance", "market": "SOL-PERP", "rate_hourly": 0.0000125, "apr": 0.1095}

`rate_hourly` é a taxa do período dividida pelo intervalo do contrato (1h, 4h,
8h...), para ficar comparável com as perps da Solana, que pagam de hora em hora.
Só perpétuos lineares em USDT. Positivo = longs pagam shorts.
"""

from __future__ import annotations

import re
import sys

import ccxt

from . import solana

EXCHANGES = ["binance", "bybit", "okx"]
CORE_BASES = {"SOL", "BTC", "ETH", "JUP"}
DEFAULT_INTERVAL_H = 8.0
HOURS_PER_YEAR = 24 * 365


def parse_interval_hours(value) -> float | None:
    """Converte '8h', '4h', '1h', '60m' etc. em horas. Retorna None se não souber."""
    if not value:
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([hm])\s*", str(value).lower())
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    return n if unit == "h" else n / 60


def default_bases() -> set[str]:
    """SOL/BTC/ETH/JUP + todos os mercados listados hoje na Velocity (ex-Drift)."""
    try:
        velocity = {r["market"].removesuffix("-PERP") for r in solana.fetch_velocity()}
    except Exception as exc:
        print(f"[cex] não consegui listar mercados da Velocity: {exc}", file=sys.stderr)
        velocity = set()
    return CORE_BASES | velocity


def _make_exchange(name: str):
    cls = getattr(ccxt, name)
    return cls({"enableRateLimit": True, "options": {"defaultType": "swap"}})


def _usdt_perp_markets(ex, bases: set[str]) -> dict:
    out = {}
    for sym, m in ex.load_markets().items():
        if not (m.get("swap") and m.get("linear") and m.get("quote") == "USDT"):
            continue
        if m.get("active") is False:
            continue
        if m.get("base", "").upper() not in bases:
            continue
        out[sym] = m
    return out


def fetch_exchange(name: str, bases: set[str] | None = None) -> list[dict]:
    """Funding padronizado de uma CEX para os ativos em `bases`."""
    bases = {b.upper() for b in (bases or default_bases())}
    ex = _make_exchange(name)
    markets = _usdt_perp_markets(ex, bases)
    if not markets:
        return []
    symbols = list(markets)

    # 1) Funding: tenta em lote, senão cai para um por um
    raw = {}
    if ex.has.get("fetchFundingRates"):
        try:
            raw = ex.fetch_funding_rates(symbols)
        except Exception as e:
            print(f"[cex] {name}: lote falhou ({type(e).__name__}), buscando um por um",
                  file=sys.stderr)
    if not raw:
        for sym in symbols:
            try:
                raw[sym] = ex.fetch_funding_rate(sym)
            except Exception:
                pass

    # 2) Intervalos (nem toda exchange informa no fetch_funding_rates)
    intervals = {}
    if ex.has.get("fetchFundingIntervals"):
        try:
            for sym, info in ex.fetch_funding_intervals(symbols).items():
                h = parse_interval_hours(info.get("interval"))
                if h:
                    intervals[sym] = h
        except Exception:
            pass

    rows = []
    for sym, fr in raw.items():
        rate = fr.get("fundingRate")
        if rate is None or sym not in markets:
            continue
        interval_h = (parse_interval_hours(fr.get("interval"))
                      or intervals.get(sym)
                      or DEFAULT_INTERVAL_H)
        rate_hourly = rate / interval_h
        rows.append({
            "exchange": name,
            "market": f"{markets[sym]['base'].upper()}-PERP",
            "rate_hourly": rate_hourly,
            "apr": rate_hourly * HOURS_PER_YEAR,
        })
    return rows


def fetch_binance(bases: set[str] | None = None) -> list[dict]:
    return fetch_exchange("binance", bases)


def fetch_bybit(bases: set[str] | None = None) -> list[dict]:
    return fetch_exchange("bybit", bases)


def fetch_okx(bases: set[str] | None = None) -> list[dict]:
    return fetch_exchange("okx", bases)


FETCHERS = {
    "binance": fetch_binance,
    "bybit": fetch_bybit,
    "okx": fetch_okx,
}


def fetch_all(bases: set[str] | None = None) -> list[dict]:
    """Roda todas as CEXs; uma exchange com erro (ex.: bloqueio regional) é pulada."""
    bases = bases or default_bases()
    rows: list[dict] = []
    for name in EXCHANGES:
        try:
            rows.extend(fetch_exchange(name, bases))
        except Exception as exc:
            print(f"[cex] {name} falhou: {type(exc).__name__}: {exc}", file=sys.stderr)
    return rows


if __name__ == "__main__":
    for r in sorted(fetch_all(), key=lambda r: (r["market"], r["exchange"])):
        print(f"{r['exchange']:<8} {r['market']:<9} {r['rate_hourly']*100:+.5f}%/h  APR {r['apr']*100:+.2f}%")
