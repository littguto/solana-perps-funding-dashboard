"""Gera public/data.json comparando funding de perps Solana com CEXs.

Uso (a partir da raiz do repo):
    python scripts/build_data.py

Para cada ativo listado nos dois lados, calcula o maior spread de APR entre
uma exchange Solana e uma CEX: long onde o APR é menor (recebe ou paga menos
funding), short onde é maior. Exchanges que falham são registradas em
`failed_exchanges` e não impedem as demais.
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from sources import cex, solana

OUTPUT = Path(__file__).resolve().parent.parent / "public" / "data.json"


def collect(fetchers: dict, venue: str, failed: list[dict], *args) -> list[dict]:
    """Roda cada fetcher isoladamente; erros e respostas vazias vão para `failed`."""
    rows = []
    for name, fetch in fetchers.items():
        try:
            result = fetch(*args)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            failed.append({"exchange": name, "venue": venue, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not result:
            failed.append({"exchange": name, "venue": venue, "error": "nenhum mercado retornado"})
            continue
        print(f"[{venue}] {name}: {len(result)} mercados", file=sys.stderr)
        rows.extend({**r, "venue": venue} for r in result)
    return rows


def best_spread(sol_rows: list[dict], cex_rows: list[dict]) -> dict:
    """Maior spread entre um lado Solana e um lado CEX, em qualquer direção."""
    sol_lo = min(sol_rows, key=lambda r: r["apr"])
    sol_hi = max(sol_rows, key=lambda r: r["apr"])
    cex_lo = min(cex_rows, key=lambda r: r["apr"])
    cex_hi = max(cex_rows, key=lambda r: r["apr"])

    # Opção A: long na CEX, short na Solana. Opção B: o contrário.
    long_, short = (cex_lo, sol_hi) if sol_hi["apr"] - cex_lo["apr"] >= cex_hi["apr"] - sol_lo["apr"] \
        else (sol_lo, cex_hi)
    return {
        "long": {"exchange": long_["exchange"], "venue": long_["venue"], "apr": long_["apr"]},
        "short": {"exchange": short["exchange"], "venue": short["venue"], "apr": short["apr"]},
        "spread_apr": short["apr"] - long_["apr"],
    }


def build_assets(rows: list[dict]) -> list[dict]:
    by_asset: dict[str, list[dict]] = {}
    for r in rows:
        by_asset.setdefault(r["market"].removesuffix("-PERP"), []).append(r)

    assets = []
    for asset, rs in by_asset.items():
        sol_rows = [r for r in rs if r["venue"] == "solana"]
        cex_rows = [r for r in rs if r["venue"] == "cex"]
        if not sol_rows or not cex_rows:
            continue
        assets.append({
            "asset": asset,
            "market": f"{asset}-PERP",
            "rates": sorted(
                ({k: r[k] for k in ("exchange", "venue", "rate_hourly", "apr")} for r in rs),
                key=lambda r: r["apr"],
            ),
            "best_spread": best_spread(sol_rows, cex_rows),
        })
    return sorted(assets, key=lambda a: a["best_spread"]["spread_apr"], reverse=True)


def write_json(payload: dict, path: Path) -> None:
    """Escreve em arquivo temporário e troca, para nunca deixar um JSON pela metade."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    failed: list[dict] = []

    sol_rows = collect(solana.FETCHERS, "solana", failed)

    # Ativos das CEXs: núcleo fixo + o que a Velocity (ex-Drift) lista hoje.
    velocity = {r["market"].removesuffix("-PERP") for r in sol_rows if r["exchange"] == "velocity"}
    bases = cex.CORE_BASES | velocity
    cex_rows = collect(cex.FETCHERS, "cex", failed, bases)

    if not sol_rows or not cex_rows:
        side = "Solana" if not sol_rows else "CEX"
        print(f"Nenhuma exchange {side} respondeu; {OUTPUT.name} não foi alterado.", file=sys.stderr)
        return 1

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "failed_exchanges": failed,
        "assets": build_assets(sol_rows + cex_rows),
    }
    write_json(payload, OUTPUT)

    print(f"{len(payload['assets'])} ativos salvos em {OUTPUT}", file=sys.stderr)
    if failed:
        print("Falharam: " + ", ".join(f["exchange"] for f in failed), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
