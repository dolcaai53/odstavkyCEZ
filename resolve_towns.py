"""
Pomocný skript: vyhledá RUIAN kód obce podle názvu.

Použití:
    python3 resolve_towns.py Těškovice
    python3 resolve_towns.py Paskov

Výstup: nalezené obce s kódem a NUTS-LAU identifikátorem.
Kód pak vložte do config.yaml jako pole `code`.

Zdroj: RUIAN MapServer (ČÚZK), vrstva Obec (layer 12).
Veřejně dostupné, bez autentizace.
"""

from __future__ import annotations

import sys
import requests

RUIAN_OBEC_URL = (
    "https://ags.cuzk.gov.cz/arcgis/rest/services/RUIAN/MapServer/12/query"
)


def find_towns(name: str) -> list[dict]:
    resp = requests.get(
        RUIAN_OBEC_URL,
        params={
            "where": f"NAZEV='{name}'",
            "outFields": "kod,nazev,nutslau",
            "f": "json",
        },
        headers={"User-Agent": "cez-monitor/1.0"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"RUIAN API chyba: {data['error']}")

    return [
        {
            "code": int(f["attributes"]["kod"]),
            "name": f["attributes"]["nazev"],
            "nutslau": f["attributes"].get("nutslau", ""),
        }
        for f in data.get("features", [])
    ]


def main() -> None:
    if len(sys.argv) < 2:
        print("Použití: python3 resolve_towns.py <název_obce>")
        sys.exit(1)

    name = " ".join(sys.argv[1:])
    print(f"Hledám: {name!r}")

    try:
        results = find_towns(name)
    except Exception as exc:
        print(f"Chyba: {exc}", file=sys.stderr)
        sys.exit(1)

    if not results:
        print("Nenalezeno. Zkuste jiný pravopis nebo ověřte název v RUIAN.")
        sys.exit(1)

    print(f"Nalezeno {len(results)} výsledků:\n")
    for r in results:
        print(f"  kód: {r['code']}  |  {r['name']}  |  NUTS-LAU: {r['nutslau']}")

    if len(results) == 1:
        print(f"\nDo config.yaml:\n  - name: {results[0]['name']}\n    code: {results[0]['code']}")


if __name__ == "__main__":
    main()
