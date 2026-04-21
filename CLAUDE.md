# CLAUDE.md

> Tento soubor čte **Claude Code** automaticky při startu v rootu projektu.
> Je zároveň kontextem pro lidské vývojáře. Drž se ho. Když v něm něco chybí
> nebo je v rozporu s realitou, **nejprve se zeptej**, neimprovizuj.

---

## 1) Co tento projekt dělá

Nástroj **`cez-monitor`** periodicky (typicky 1× týdně z cronu) kontroluje
plánované odstávky elektřiny u společnosti **ČEZ Distribuce** pro vybraný
seznam obcí a při nálezu **nové** odstávky pošle upozornění do **Telegramu**.

Použití je **veřejně-monitorovací** — uživatel není vlastníkem odběrných
míst v dotčených obcích, proto nemůže využít oficiální službu ČEZ (ta je
vázaná na konkrétní EAN/adresu a souhlas majitele).

### Klíčové vlastnosti

- **Zdroj dat:** REST API `api.bezstavy.cz` — ověřeno DevTools, endpoint znám
- **Identifikace obcí:** RUIAN kód (6 číslic), zjistitelný přes `resolve_towns.py`
- **Cílové prostředí:** Linux VPS, Python 3.10+, spouštění přes cron
- **Notifikace:** Telegram bot (HTML formátování zpráv), heartbeat do odděleného botu
- **Deduplikace:** SQLite + SHA-256 otisk postavený primárně na `outage_id` z API
- **Rozsah:** Moravskoslezský kraj (ale funguje pro celé distribuční území ČEZ)

---

## 2) Architektura

```
cez-monitor/
├── cez_monitor.py          # hlavní skript (single-file)
├── resolve_towns.py        # pomocný skript pro vyhledání RUIAN kódů obcí
├── config.yaml             # runtime konfigurace (NIKDY do gitu)
├── config.example.yaml     # šablona pro uživatele
├── requirements.txt        # requests, PyYAML
├── seen_outages.sqlite     # DB známých odstávek (vzniká za běhu)
├── cez_monitor.log         # app log
└── cron.log                # stdout/stderr z cronu
```

### Datový tok

```
cron -> main() -> load_config() -> pro každou obec (Town):
                                     fetch_outages(session, town)
                                       GET api.bezstavy.cz/cezd/api/inspecttown/{townCode}
                                       _parse_outages(town, data)
                                   -> pro každou odstávku:
                                        is_new(conn, o)?
                                          ano -> send_telegram() -> mark_seen()
                                          ne  -> skip (incl. stejná odstávka z jiné obce)
```

### API — schéma odpovědi (ověřeno)

Endpoint: `GET https://api.bezstavy.cz/cezd/api/inspecttown/{townCode}`
- bez autentizace, bez query parametrů — `townCode` je součástí URL path
- referenční test: `/cezd/api/inspecttown/512745` (Těškovice, zjištěno DevTools)

```json
{
  "outages": null,
  "outages_in_town": [
    {
      "id": "110061080030",
      "announcement_key": "pdf/301228923-…pdf",
      "opened_at": "2026-04-20T05:30:00Z",
      "fix_expected_at": "2026-04-20T09:30:00Z",
      "addresses": {
        "towns": [
          { "name": "Bílovec", "code": 599247 },
          { "name": "Těškovice", "code": 512745 }
        ]
      }
    }
  ]
}
```

- `outages_in_town` — plánované odstávky (může být `null`)
- `outages` — aktuální poruchy; zatím vždy `null`, parsování naimplementováno není (viz §5.2)
- `id` — **stabilní identifikátor odstávky**, základ fingerprintu
- `opened_at` / `fix_expected_at` — časy v **UTC** (suffix `Z`)
- `announcement_key` — relativní cesta k PDF; absolutní URL = `https://api.bezstavy.cz/{key}`

### Klíčové třídy/funkce

| Symbol | Účel | Pozor na |
|---|---|---|
| `Town` (dataclass, frozen) | obec s RUIAN kódem | kód zjistit přes `resolve_towns.py` |
| `Outage` (dataclass, frozen) | neměnný záznam jedné odstávky | `fingerprint()` je zdroj pravdy pro deduplikaci |
| `fingerprint()` | SHA-256 primárně z `outage_id` | změna verze → vědomý reset DB (viz §3.1) |
| `ENDPOINT_INSPECT_TOWN` | `api.bezstavy.cz/cezd/api/inspecttown` | `{townCode}` je v URL path, ne query param |
| `fetch_outages(session, town)` | volá API, parsuje odpověď | musí být idempotentní vůči síťovým chybám |
| `_parse_outages(town, data)` | mapuje JSON → `list[Outage]` | klíče viz schéma výše |
| `is_new()` / `mark_seen()` | DB operace | **vždy volat v tomto pořadí až po úspěšném odeslání** |

---

## 3) Invarianty a pravidla (NEPORUŠOVAT)

### 3.1 Fingerprint — verze a stabilita

Fingerprint je SHA-256 z payloadu `{"v": FINGERPRINT_VERSION, "outage_id": <id>}`.

- **Primárně `outage_id`** — stabilní API identifikátor, stejný pro tutéž odstávku
  bez ohledu na to, přes kterou sledovanou obec jsme ji detekovali.
- **Fallback** (pokud API nevrátí `id`): `"{town_code}|{date_from}|{date_to}|{kind}"`.
- `FINGERPRINT_VERSION = 3` — aktuální verze. Zvýšení = vědomý reset (uživatel
  dostane všechny aktuálně známé odstávky znovu, to je OK).
- Změna verze bez migrace je přípustná jen při prázdné nebo testovací DB.
- **Nikdy nesnižuj verzi** — způsobilo by to duplicitní notifikace.

### 3.2 Notifikace → DB, nikdy opačně

```python
# SPRÁVNĚ
if send_telegram(...):
    mark_seen(conn, outage)

# ŠPATNĚ (při výpadku Telegramu se odstávka "spotřebuje" bez notifikace)
mark_seen(conn, outage)
send_telegram(...)
```

### 3.3 Ohleduplnost k serveru bezstavy.cz

- Mezi dotazy na jednotlivé obce `time.sleep(2)`. **Nesnižuj pod 1s.**
- Nepouštěj skript častěji než 2× denně. Týdenní běh je norma.
  ČEZ musí plánované odstávky vyhlásit min. 15 dní předem.
- User-Agent musí obsahovat identifikátor aplikace (`cez-monitor/1.0`).
- Deduplikace napříč obcemi funguje přirozeně: jakmile je `outage_id` jednou
  v `seen`, další obce stejnou odstávku v témže i budoucím běhu přeskočí.

### 3.4 Citlivá data

- `config.yaml` obsahuje **bot_token** — nesmí skončit v gitu.
- V logu se **nikdy neloguje** celý config ani token.
- Pokud přidáváš novou konfiguraci s tajemstvím, přidej odpovídající řádek do `.gitignore`.

### 3.5 Idempotence a resilience

- Jeden selhavší síťový dotaz **nesmí shodit celý běh** — jen se zaloguje a pokračuje se další obcí.
- Skript musí být možné spustit mnohokrát za sebou bez vedlejších efektů.

---

## 4) Běžné úkoly pro Claude Code

### 4.1 "Přidej další obec"

1. `python3 resolve_towns.py <název>` — zjistí RUIAN kód
2. Do `config.yaml` přidej:
   ```yaml
   - name: Název obce
     code: 123456
   ```
3. Kód neměň.

### 4.2 "Přidej notifikace přes e-mail vedle Telegramu"

- Přidej do configu sekci `email: { smtp_host, smtp_port, user, password, to }`.
- Vytvoř `send_email(cfg, subject, body)` vedle `send_telegram()`.
- V `main()` posílej paralelně; `mark_seen` volej **až když prošel aspoň jeden kanál**.
  Nebo rozšiř DB o sloupce `notified_telegram`, `notified_email` — viz §3.1.
- Přidej unit test pro formátování e-mailu.

### 4.3 "Rozšiř o EG.D / PREdistribuci"

- Vytvoř abstrakci `Distributor` (protocol/ABC) s metodou `fetch_outages(town) -> list[Outage]`.
- Současný kód přesuň do třídy `CezDistributor`.
- Obec v configu rozšiř o pole `distributor: cez|egd|pre` (default `cez`).
- **Fingerprint musí zahrnout identifikátor distributora**, jinak by kolidovaly obce
  se stejným názvem. Naplánuj migraci (viz §3.1).

### 4.4 "Skript nic nenachází, i když na webu ČEZ / bezstavy.cz odstávka je"

Diagnostika v tomto pořadí:

1. Spusť s `logging.DEBUG` — zkontroluj, zda `fetch_outages` vrátí prázdný seznam nebo vyhodí výjimku.
2. Ověř kód obce: `python3 resolve_towns.py <název>` — může být špatný RUIAN kód.
3. Curl ručně: `curl https://api.bezstavy.cz/cezd/api/inspecttown/<code>` — zkontroluj odpověď.
4. Pokud API vrací jinou strukturu než schéma v §2, zkontroluj klíče v `_parse_outages`.
5. Pokud API změnilo URL — viz §5.1 pro postup zjištění nového endpointu.

### 4.5 "Začínají chodit duplicitní notifikace"

```sql
sqlite3 seen_outages.sqlite \
  "SELECT obec, datum_od, fingerprint, fingerprint_version FROM seen ORDER BY notified_at DESC LIMIT 20;"
```

- Pokud vidíš záznamy s různými fingerprints pro tutéž odstávku, zkontroluj `fingerprint_version`.
- Verze 3+ používá `outage_id` — duplicity by neměly nastávat pokud API vrací stabilní `id`.
- Pokud API občas nevrátí `id` a padne na fallback, zkontroluj konzistenci
  `date_from`/`date_to` (jsou UTC, neměly by kolísat).

### 4.6 "Přidej retry při síťových chybách"

Již implementováno: `HTTPAdapter` + `urllib3.Retry` na session (3× retry, backoff 1/2/4s, jen 5xx).

### 4.7 Časy v UTC vs. zobrazení

- API vrací časy v **UTC** (suffix `Z`): `"2026-04-20T05:30:00Z"`.
- V DB se ukládají jako UTC (stabilní základ pro fingerprint).
- V Telegram zprávě se **konvertují na Europe/Prague** přes `zoneinfo` ze stdlib.
- Pozor na DST: Praha je UTC+1 (zimní) nebo UTC+2 (letní) — `zoneinfo` to řeší automaticky.
- **Nikdy neupravuj uložené UTC časy** kvůli zobrazení — ovlivnilo by to fingerprint.

---

## 5) Známá slabá místa

### 5.1 Endpoint `ENDPOINT_INSPECT_TOWN` se může změnit

`bezstavy.cz` není oficiální ČEZ API — je to třetí strana. Pokud endpoint přestane
fungovat (404, HTML místo JSON):

1. Otevři `bezstavy.cz` v prohlížeči a přejdi na stránku s odstávkami
2. DevTools → Network → filtr Fetch/XHR
3. Vyhledej obec, sleduj requesty
4. Najdi request vracející JSON s `outages_in_town`
5. Zkopíruj URL, metodu, parametry, hlavičky
6. Uprav `ENDPOINT_INSPECT_TOWN` a případně `_parse_outages()` pro novou strukturu

### 5.2 Pole `outages` (poruchy) není parsováno

API vrací i `outages` (aktuální poruchy — nezaviněné výpadky). V době vývoje bylo vždy
`null`. Až bude non-null, parsování je potřeba doimplementovat — struktura bude
pravděpodobně podobná `outages_in_town`. Nedělej proaktivně, řeš jen na vyžádání.

### 5.3 Zrušené odstávky

Skript detekuje jen **výskyt** odstávky v seznamu. Když ČEZ odstávku zruší, nepošle
notifikaci o zrušení. Řešení by vyžadovalo ukládat *aktuální stav* a porovnávat
s předchozím během. Je to rozšíření, ne oprava — nedělej proaktivně.

### 5.4 Silent failure

Pokud API přestane vracet data (bez chyby), skript tiše nenajde nic. Heartbeat
notifikace při každém běhu slouží jako detekce: pokud přestane chodit, něco je špatně.

---

## 6) Jak vývoj/změny ověřovat

### 6.1 Rychlá sanity kontrola

```bash
python3 test_suite.py
```

Nebo manuálně:

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
import cez_monitor as m
o = m.Outage(
    outage_id='TEST123', town_code=512745, town_name='Těškovice',
    date_from='2026-04-20T05:30:00Z', date_to='2026-04-20T09:30:00Z',
    kind='planned', announcement_pdf='', affected_towns='Těškovice',
    source_url='https://www.bezstavy.cz'
)
print(o.fingerprint()[:16])
print(o.to_telegram())
"
```

### 6.2 Integrační test (DRY_RUN)

```yaml
# config.yaml pro testování
telegram:
  bot_token: DRY_RUN
  chat_id: '-1'
  heartbeat_bot_token: DRY_RUN
  heartbeat_chat_id: '-2'
obce:
  - name: Těškovice
    code: 512745
```

```bash
python3 cez_monitor.py
```

Skript se připojí k reálnému API, ale Telegram zprávy jen zaloguje. Zkontroluj log.

### 6.3 Vyhledání RUIAN kódu pro novou obec

```bash
python3 resolve_towns.py Název obce
```

### 6.4 Git hygiena

- Před commitem: `git status` a zkontroluj, že `config.yaml` a `*.sqlite` nejsou ve stage.
- `test_suite.py` není produkční kód — nevadí ho commitovat.

---

## 7) Co Claude Code **nemá** dělat bez dotazu

- Měnit pole dataclassy `Outage` nebo `Town` bez naplánování migrace (viz §3.1).
- Přidávat parsování pole `outages` (poruchy) — viz §5.2.
- Refaktorovat do více souborů — single-file je záměr. Výjimka: §4.3.
- Přidávat závislosti nad rámec `requirements.txt` bez odůvodnění.
- Logovat tajemství (token, `chat_id` je ok).
- Commitovat `config.yaml` nebo SQLite databázi.
- Snižovat `FINGERPRINT_VERSION` — viz §3.1.

---

## 8) Styl kódu

- Python 3.10+ typehinty (`list[str]`, `X | None`, `from __future__ import annotations`).
- Dataclassy `frozen=True` tam, kde jde o hodnotové objekty.
- Docstringy česky (zbytek projektu je česky) — krátké, věcné.
- Logování přes `logging`, ne `print()`.
- Žádné `except: pass` — vždy logovat, vždy uvést, co se stalo.

---

## 9) Kontakt / provenience

- Uživatel je v Moravskoslezském kraji, provozuje skript na vlastním VPS.
- Cílem je monitoring odstávek ve vesnicích, kde uživatel **není vlastníkem**
  odběrného místa (jinak by stačila oficiální služba ČEZ).
- Endpoint `api.bezstavy.cz/cezd/api/inspecttown/{code}` byl zjištěn přes DevTools
  a ověřen testem na kódu `512745` (Těškovice, okres Opava).
- Pokud se projekt rozšíří mimo MSK, RUIAN kódy řeší §5.3 (každá obec má unikátní kód).
