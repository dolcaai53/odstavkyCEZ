# Prompt pro Claude Code

> Níže je kompletní zadání. Otevřete projekt v Claude Code (`claude` v rootu),
> zkopírujte vše od řádku `---` níže a vložte jako první zprávu.

---

Ahoj. V tomhle projektu je v `CLAUDE.md` popsán monitor plánovaných odstávek ČEZ. Zbývala jedna nedořešená věc — skutečný endpoint pro čtení odstávek. Tu jsem teď zjistil z DevTools, potřebuji to zapracovat.

## Co jsem zjistil

**URL vzor:** `GET https://api.bezstavy.cz/cezd/api/inspecttown/<townCode>`

- `townCode` je **součástí cesty**, ne query parametrem
- metoda `GET`, bez autentizace, bez zvláštních hlaviček
- odpovědí je JSON

## Reálná odpověď API (pro Těškovice, townCode=512745, v době testu)

```json
{
  "outages": null,
  "outages_in_town": [
    {
      "id": "110061080030",
      "announcement_key": "pdf/301228923-d7510mdct0g99dmejq8g.pdf",
      "opened_at": "2026-04-20T05:30:00Z",
      "fix_expected_at": "2026-04-20T09:30:00Z",
      "addresses": {
        "towns": [
          {
            "name": "Bílovec",
            "code": 599247,
            "district": "Nový Jičín",
            "cadastral_territories": null,
            "town_districts": [
              {
                "name": "",
                "code": 0,
                "town_parts": [
                  {
                    "name": "Lhotka",
                    "streets": [
                      {
                        "name": "",
                        "house_nums": "1-14, 16-25, 27-33, 35, 36, 37, 38",
                        "ev_nums": "",
                        "street_nums": ""
                      }
                    ],
                    "cadastral_territory": null
                  }
                ]
              }
            ]
          },
          { "name": "Bítov", "code": 554936, "...": "..." },
          { "name": "Olbramice", "code": 554049, "...": "..." },
          { "name": "Těškovice", "code": 512745, "...": "..." }
        ],
        "orphan_territories": null
      }
    }
  ]
}
```

## Sémantika polí

| pole | význam |
|---|---|
| `outages` | aktuální poruchy (v době testu `null`) |
| `outages_in_town` | seznam **plánovaných odstávek**, které zasahují zadanou obec |
| `id` | **stabilní identifikátor odstávky** — ideální jako primární složka fingerprintu |
| `announcement_key` | relativní cesta k PDF oznámení; pravděpodobně se dá sestavit URL jako `https://api.bezstavy.cz/<announcement_key>` nebo přes web `bezstavy.cz` — **ověř to dotazem do prohlížeče** při ladění |
| `opened_at` / `fix_expected_at` | začátek/konec odstávky v ISO 8601 **UTC** (koncovka `Z`) |
| `addresses.towns[]` | seznam **všech** obcí dotčených odstávkou, ne jen té, na kterou jsme se ptali |
| `addresses.towns[].code` | RÚIAN kód dotčené obce |
| `addresses.towns[].town_districts[].town_parts[].streets[]` | detail (ulice, čísla popisná) |

## Co chci, abys v projektu udělal

### 1) Konstanty a konfigurace
- Odstraň `ENDPOINT_OUTAGES` (bylo to zástupné).
- Přidej `ENDPOINT_INSPECT_TOWN = f"{API_BASE}/cezd/api/inspecttown"`.
- V komentáři u konstanty stručně popiš, že `{townCode}` je součástí cesty.

### 2) Dataclass `Outage`
Přidej pole pro skutečné ID a PDF:

```python
@dataclass(frozen=True)
class Outage:
    outage_id: str          # "id" z API — stabilní
    town_code: int          # obec, pod kterou jsme to detekovali
    town_name: str
    date_from: str          # ISO 8601 UTC
    date_to: str            # ISO 8601 UTC
    kind: str               # "planned" | "fault"
    announcement_pdf: str   # absolutní URL PDF, nebo prázdný řetězec
    affected_towns: str     # CSV názvů všech dotčených obcí pro kontext
    source_url: str
```

### 3) Fingerprint
**Primárně použij `outage_id`** — je to ten stabilní klíč. Pokud z nějakého důvodu chybí, udělej fallback na `(town_code, date_from, date_to, kind)`.

```python
payload = {
    "v": FINGERPRINT_VERSION,
    "outage_id": self.outage_id or f"{self.town_code}|{self.date_from}|{self.date_to}|{self.kind}",
}
```

**Zvyš `FINGERPRINT_VERSION` na `"v3"`** — jde o skutečnou změnu struktury otisku a při prvním běhu po deploy uživatel dostane všechny aktuálně známé odstávky znovu (to je OK, je to vědomý reset).

### 4) Deduplikace napříč obcemi (důležité)

Jedna odstávka typicky zasahuje více obcí současně (v příkladu Bílovec, Bítov, Olbramice, Těškovice). Pokud uživatel sleduje několik z nich, **neposílej tutéž odstávku vícekrát**. Protože fingerprint je postavený na `outage_id`, tohle vyřeší přirozeně sama — jakmile uvidíme `id` jednou, další obce ho v rámci téhož běhu přeskočí. Ověř, že:
- `seen` se načítá **před** smyčkou přes obce a kontroluje se pro každou odstávku,
- v `seen` se zapisuje **hned po** odeslání (už to tak je, ale ověř při refaktoru).

### 5) `fetch_outages(town)` — reálná implementace

```python
def fetch_outages(self, town: Town) -> list[Outage]:
    url = f"{ENDPOINT_INSPECT_TOWN}/{town.town_code}"
    r = self.session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return self._parse_outages(town, r.json())
```

Žádné query parametry, `townCode` je v cestě.

### 6) `_parse_outages(town, data)` — reálná struktura

- čti `data["outages_in_town"]` (může být `None` — ošetři)
- pro každý záznam vytáhni: `id`, `opened_at`, `fix_expected_at`, `announcement_key`, `addresses.towns`
- z `addresses.towns` si seber **seznam názvů** (všech obcí, které odstávka zasahuje) a dej ho do `Outage.affected_towns` jako CSV (`"Bílovec, Bítov, Olbramice, Těškovice"`)
- `announcement_pdf`: pokud je `announcement_key` neprázdný, sestav URL. **Nevím jistě, jestli je správná báze `https://api.bezstavy.cz/<key>` nebo `https://www.bezstavy.cz/<key>`** — zkus nejprve `https://api.bezstavy.cz/<announcement_key>` a pokud chceš, ověř HEAD requestem; když to nejde elegantně, nech jen `announcement_key` samotný a v Telegram zprávě komentář "PDF: <key>"
- pole `outages` (poruchy) zatím **neparsuj**, jen zaloguj `data.get("outages")` na DEBUG. Poruchy si přidáme později.

### 7) Telegram zpráva — využij nová pole

```
⚡ Plánovaná odstávka ČEZ
📍 Těškovice (kód 512745)
🕒 20.04.2026 07:30 — 09:30 (Europe/Prague)
🗺️ Dotčené obce: Bílovec, Bítov, Olbramice, Těškovice
📄 Oznámení: <odkaz na PDF>
🔗 Detail na bezstavy.cz
```

- **Konvertuj časy z UTC na Europe/Prague** pro zobrazení (zdroj nech ISO UTC kvůli fingerprintu — to je stabilní).
- Použij `zoneinfo` ze stdlib, ne `pytz`.
- Formát zobrazení: `DD.MM.YYYY HH:MM` pokud `date_to` je jiný den, jinak `DD.MM.YYYY HH:MM — HH:MM`.

### 8) Debug výstup

Když `_parse_outages` dostane neznámou strukturu (neplatný JSON, chybějící klíč `outages_in_town`), zaloguj na WARN prvních 500 znaků odpovědi — pomůže při budoucích změnách API.

### 9) Aktualizuj `CLAUDE.md`

- §2 Architektura: v tabulce přejmenuj `ENDPOINT_OUTAGES` na `ENDPOINT_INSPECT_TOWN`, doplň, že `townCode` je v URL path.
- §3.1 Fingerprint: přepiš na novou realitu — otisk postavený na `outage_id`, což je **to nejlepší co můžeme mít**, protože je stabilní napříč obcemi i běhy. Zmíň fallback pro případ chybějícího `id`.
- §3.3 town_code: stále platí, ale doplň, že pro deduplikaci **napříč obcemi** slouží `outage_id`, ne `town_code` (protože jedna odstávka může zasahovat víc obcí současně).
- §4.1 Známá slabá místa: **odstraň** celý bod "ENDPOINT_OUTAGES je None" — už máme reálný endpoint, hotovo.
- §4.2 "Schéma odpovědi je odhadované" — přepiš na pozitivní: "Schéma je známé a stabilně naparsované", a přesuň do §2 jako dokumentaci.
- Přidej nový bod §4.x: "Časy v UTC — zobrazujeme v Europe/Prague, ale v otisku ponecháváme UTC (stabilní)".
- §5 (běžné úkoly): doplň nový §5.x "Přidat poruchy" — teď víme, že pole `outages` existuje, jen je zatím `null`. Řešení: sledovat ho, a až bude non-null, naparsovat (asi podobná struktura).
- §9 Kontext: doplň informaci, že endpoint pro odstávky byl zjištěn a ověřen; reference test: `/cezd/api/inspecttown/512745` (Těškovice).

### 10) README

- Sekci "Endpoint discovery" přepiš — už není nutná pro základní provoz (endpoint je znám), ale nech ji pro případ, že by ČEZ URL změnil. Přesuň ji na konec jako troubleshooting.
- Přidej informaci, že skript je plně funkční out-of-the-box a stačí vyplnit `config.yaml`.

## Kritéria hotovosti

1. `python3 resolve_towns.py Těškovice` vrátí kód `512745` (beze změny, ale ať to pořád funguje).
2. Po vyplnění `config.yaml` a prvním spuštění `python3 cez_monitor.py`:
   - se skript připojí k reálnému endpointu `/cezd/api/inspecttown/512745`
   - zobrazí v logu počet plánovaných odstávek
   - u každé nové odstávky pošle Telegram zprávu s časy konvertovanými do Europe/Prague
   - zapíše záznam do `seen_outages.json`
3. Druhé spuštění se stejnými daty **neposílá nic** (deduplikace funguje).
4. Když do configu přidám další obec dotčenou toutéž odstávkou (např. Bílovec, kód `599247`), druhé spuštění **nepošle stejnou odstávku znovu** (deduplikace napříč obcemi).
5. `FINGERPRINT_VERSION` je `"v3"`.
6. `CLAUDE.md` reflektuje nový stav — žádná zmínka o "uhodnutém endpointu" nebo "ENDPOINT_OUTAGES je None".

## Testování bez reálného Telegramu

Při testu nastav `bot_token: "DRY_RUN"` a udělej v `send_telegram` zkratku: pokud token je `DRY_RUN`, jen zaloguj zprávu na INFO a vrať `True`. Usnadní to ověření.

## Co NEMĚNIT bez dotazu

- Strukturu `Town` (ta je OK).
- Pořadí operací: notifikace → zápis do `seen` → `save_seen`.
- Frekvenci polling a `SLEEP_BETWEEN_REQUESTS`.
- Rozvržení do single-file (zatím projekt zůstává v `cez_monitor.py`).
- Nepřidávej nové závislosti. Časové zóny řeš přes `zoneinfo` (stdlib).

Až to budeš mít, ukaž mi diff a navrhni test plán — ještě než spustím proti reálnému Telegramu.
