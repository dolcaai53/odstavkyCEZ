from __future__ import annotations

import fcntl
import hashlib
import html as html_module
import json
import logging
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import IO
from zoneinfo import ZoneInfo

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


import os

SCRIPT_DIR = Path(__file__).parent
PRAGUE_TZ = ZoneInfo("Europe/Prague")
FINGERPRINT_VERSION = 3

API_BASE = "https://api.bezstavy.cz"
# townCode je součástí URL path: GET /cezd/api/inspecttown/{townCode}
ENDPOINT_INSPECT_TOWN = f"{API_BASE}/cezd/api/inspecttown"
BEZSTAVY_WEB = "https://www.bezstavy.cz"

REQUEST_TIMEOUT = 15

# Paths — Docker-friendly (env override)
DATA_DIR = Path(os.getenv("DATA_DIR", SCRIPT_DIR))
DB_PATH = DATA_DIR / "seen_outages.sqlite"
CONFIG_PATH = DATA_DIR / "config.yaml"
LOG_PATH = Path(os.getenv("LOG_PATH", SCRIPT_DIR / "cez_monitor.log"))
LOCK_PATH = Path("/tmp/cez-monitor.lock")

USER_AGENT = "cez-monitor/1.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pomocné funkce
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Trim, collapse whitespace, unescape HTML entity."""
    return " ".join(html_module.unescape(s).split())


def _fmt_utc_prg(iso_utc: str) -> tuple[datetime | None, str]:
    """ISO 8601 UTC → (datetime v Prague TZ, textový DD.MM.YYYY HH:MM)."""
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(PRAGUE_TZ)
        return dt, dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, AttributeError):
        return None, iso_utc


# ---------------------------------------------------------------------------
# Datové modely
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Town:
    """Sledovaná obec s RUIAN kódem."""
    name: str
    code: int


@dataclass(frozen=True)
class Outage:
    """Neměnný záznam jedné plánované odstávky."""
    outage_id: str          # stabilní "id" z API
    town_code: int          # RUIAN kód obce, přes kterou jsme odstávku detekovali
    town_name: str
    date_from: str          # ISO 8601 UTC (zdrojový formát z API)
    date_to: str            # ISO 8601 UTC
    kind: str               # "planned" | "fault"
    announcement_pdf: str   # absolutní URL PDF oznámení, nebo prázdný řetězec
    affected_towns: str     # CSV názvů všech dotčených obcí
    source_url: str

    def __post_init__(self) -> None:
        for field in ("town_name", "affected_towns", "kind"):
            object.__setattr__(self, field, _norm(getattr(self, field)))

    def fingerprint(self) -> str:
        # Primárně outage_id — stabilní napříč obcemi i běhy.
        # Fallback pro případ chybějícího id: kombinace klíčových polí.
        key = self.outage_id or f"{self.town_code}|{self.date_from}|{self.date_to}|{self.kind}"
        payload = {"v": FINGERPRINT_VERSION, "outage_id": key}
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def to_telegram(self) -> str:
        dt_from, s_from = _fmt_utc_prg(self.date_from)
        dt_to, s_to = _fmt_utc_prg(self.date_to)
        if dt_from and dt_to and dt_from.date() == dt_to.date():
            time_range = f"{s_from} — {dt_to.strftime('%H:%M')}"
        else:
            time_range = f"{s_from} — {s_to}"

        pdf_line = f'📄 Oznámení: <a href="{self.announcement_pdf}">PDF</a>\n' if self.announcement_pdf else ""
        return (
            f"⚡ Plánovaná odstávka ČEZ\n"
            f"📍 {self.town_name} (kód {self.town_code})\n"
            f"🕒 {time_range} (Europe/Prague)\n"
            f"🗺️ Dotčené obce: {self.affected_towns}\n"
            f"{pdf_line}"
            f'🔗 <a href="{self.source_url}">Detail na bezstavy.cz</a>'
        )


# ---------------------------------------------------------------------------
# Databáze
# ---------------------------------------------------------------------------

def init_db(conn: sqlite3.Connection) -> None:
    """Inicializuje schéma. Idempotentní."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            fingerprint         TEXT PRIMARY KEY,
            fingerprint_version INTEGER NOT NULL DEFAULT 1,
            obec                TEXT NOT NULL,
            datum_od            TEXT,
            datum_do            TEXT,
            notified_at         TEXT NOT NULL
        )
    """)
    conn.commit()


def is_new(conn: sqlite3.Connection, outage: Outage) -> bool:
    """Vrací True, pokud odstávka ještě nebyla oznámena."""
    return conn.execute(
        "SELECT 1 FROM seen WHERE fingerprint = ?", (outage.fingerprint(),)
    ).fetchone() is None


def mark_seen(conn: sqlite3.Connection, outage: Outage) -> None:
    """Zaznamená odstávku jako oznámenou. Volat vždy až po úspěšném send_telegram."""
    conn.execute(
        """INSERT OR IGNORE INTO seen
               (fingerprint, fingerprint_version, obec, datum_od, datum_do, notified_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            outage.fingerprint(),
            FINGERPRINT_VERSION,
            outage.town_name,
            outage.date_from,
            outage.date_to,
            datetime.now(PRAGUE_TZ).isoformat(),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _post_telegram(bot_token: str, chat_id: str, text: str) -> bool:
    """Odešle zprávu přes Telegram API, s retry při rate limitu (429).
    Pokud bot_token == 'DRY_RUN', jen zaloguje zprávu a vrátí True."""
    if bot_token == "DRY_RUN":
        log.info("[DRY_RUN] Telegram → chat %s:\n%s", chat_id, text)
        return True

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            log.error("Telegram request selhal (pokus %d/3): %s", attempt + 1, exc)
            return False
        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 30)
            log.warning("Telegram rate limit, čekám %ds", retry_after)
            time.sleep(retry_after)
            continue
        if resp.ok:
            return True
        log.error("Telegram chyba %d: %s", resp.status_code, resp.text[:200])
        return False
    return False


def send_telegram(cfg: dict, text: str) -> bool:
    """Pošle notifikaci o odstávce do hlavního botu."""
    tg = cfg["telegram"]
    return _post_telegram(tg["bot_token"], tg["chat_id"], text)


def send_heartbeat(cfg: dict, text: str) -> bool:
    """Pošle heartbeat nebo chybovou zprávu do dedikovaného botu."""
    try:
        tg = cfg["telegram"]
        return _post_telegram(tg["heartbeat_bot_token"], tg["heartbeat_chat_id"], text)
    except Exception as exc:
        log.error("Heartbeat selhal: %s", exc)
        return False


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    """Vytvoří session s retry na 5xx a connection errors."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ---------------------------------------------------------------------------
# Získání dat z bezstavy.cz API
# ---------------------------------------------------------------------------

def _parse_outages(town: Town, data: dict) -> list[Outage]:
    """Parsuje odpověď z /inspecttown/{code}."""
    log.debug("outages (poruchy) pro %s: %s", town.name, data.get("outages"))

    items = data.get("outages_in_town")
    if not items:
        return []

    outages: list[Outage] = []
    for item in items:
        try:
            outage_id = item.get("id", "")
            date_from = item.get("opened_at", "")
            date_to = item.get("fix_expected_at", "")
            announcement_key = item.get("announcement_key") or ""

            announcement_pdf = f"{API_BASE}/{announcement_key}" if announcement_key else ""

            towns_raw = (item.get("addresses") or {}).get("towns") or []
            affected_towns = ", ".join(t["name"] for t in towns_raw if t.get("name"))

            outages.append(Outage(
                outage_id=outage_id,
                town_code=town.code,
                town_name=town.name,
                date_from=date_from,
                date_to=date_to,
                kind="planned",
                announcement_pdf=announcement_pdf,
                affected_towns=affected_towns or town.name,
                source_url=BEZSTAVY_WEB,
            ))
        except Exception as exc:
            log.debug("Přeskakuji položku (parsování selhalo): %s — %s", item, exc)

    return outages


def fetch_outages(session: requests.Session, town: Town) -> list[Outage]:
    """Načte plánované odstávky pro danou obec z bezstavy.cz API."""
    url = f"{ENDPOINT_INSPECT_TOWN}/{town.code}"
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Chyba při načítání odstávek pro %s (kód %d): %s", town.name, town.code, exc)
        return []

    try:
        data = resp.json()
    except ValueError as exc:
        log.warning(
            "Neplatný JSON z API pro %s: %.500s (%s)",
            town.name, resp.text, exc,
        )
        return []

    return _parse_outages(town, data)


# ---------------------------------------------------------------------------
# Konfigurace
# ---------------------------------------------------------------------------

def load_config() -> tuple[dict, list[Town]]:
    """Načte a validuje config.yaml. Při chybě ukončí process."""
    if not CONFIG_PATH.exists():
        log.error("config.yaml nenalezen v %s — zkopírujte config.example.yaml", SCRIPT_DIR)
        sys.exit(1)

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    errors: list[str] = []
    tg = cfg.get("telegram") or {}
    for key in ("bot_token", "chat_id", "heartbeat_bot_token", "heartbeat_chat_id"):
        if not tg.get(key):
            errors.append(f"telegram.{key}")

    obce_raw = cfg.get("obce")
    if not isinstance(obce_raw, list) or not obce_raw:
        errors.append("obce (musí být neprázdný seznam)")
        if errors:
            for e in errors:
                log.error("Config: chybí nebo je neplatné pole '%s'", e)
            sys.exit(1)

    towns: list[Town] = []
    for i, item in enumerate(obce_raw):
        if not isinstance(item, dict) or not item.get("name") or not item.get("code"):
            errors.append(f"obce[{i}] — musí mít 'name' a 'code'")
        else:
            towns.append(Town(name=str(item["name"]), code=int(item["code"])))

    delay = cfg.get("delay_between_requests", 2)
    if not isinstance(delay, (int, float)) or delay < 1:
        errors.append("delay_between_requests (musí být >= 1)")

    if errors:
        for e in errors:
            log.error("Config: chybí nebo je neplatné pole '%s'", e)
        sys.exit(1)

    return cfg, towns


# ---------------------------------------------------------------------------
# Process lock
# ---------------------------------------------------------------------------

def _acquire_lock() -> IO:
    """Zabraňuje souběžnému spuštění více instancí."""
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.warning("Jiná instance cez-monitor již běží, ukončuji")
        sys.exit(0)
    return lock_file


# ---------------------------------------------------------------------------
# Hlavní logika
# ---------------------------------------------------------------------------

def _run(cfg: dict, towns: list[Town]) -> tuple[int, list[str]]:
    """Provede kontrolu všech obcí. Vrací (počet nových odstávek, obce s chybou)."""
    session = _make_session()
    delay: float = cfg.get("delay_between_requests", 2)

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    total_new = 0
    errors: list[str] = []

    for i, town in enumerate(towns):
        if i > 0:
            time.sleep(delay)

        log.info("Kontroluji obec: %s (kód %d)", town.name, town.code)
        outages = fetch_outages(session, town)

        for outage in outages:
            if is_new(conn, outage):
                if send_telegram(cfg, outage.to_telegram()):
                    mark_seen(conn, outage)
                    total_new += 1
                    log.info(
                        "Nová odstávka oznámena: %s id=%s %s–%s",
                        outage.town_name, outage.outage_id,
                        outage.date_from, outage.date_to,
                    )
                else:
                    log.error(
                        "Nepodařilo se odeslat notifikaci pro %s id=%s",
                        outage.town_name, outage.outage_id,
                    )
            else:
                log.debug("Již oznámeno, přeskakuji: %s id=%s", outage.town_name, outage.outage_id)

    conn.close()
    return total_new, errors


def main() -> None:
    lock = _acquire_lock()
    cfg, towns = load_config()
    try:
        total_new, errors = _run(cfg, towns)
        parts = [
            f"Kontrola dokončena.",
            f"Obcí: {len(towns)}, nových odstávek: {total_new}.",
        ]
        if errors:
            parts.append(f"Chyby při načítání: {', '.join(errors)}")
        send_heartbeat(cfg, " ".join(parts))
        log.info("Hotovo. Nových odstávek: %d", total_new)
    except Exception as exc:
        log.exception("Fatální chyba: %s", exc)
        send_heartbeat(cfg, f"CHYBA: cez-monitor spadl\n{type(exc).__name__}: {exc}")
        sys.exit(1)
    finally:
        lock.close()


if __name__ == "__main__":
    main()
