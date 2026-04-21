"""Sanity testy — spouštět v Dockeru, nikoli na produkci."""
import sys
import sqlite3
sys.path.insert(0, ".")

import resolve_towns
import cez_monitor as m


def ok(label):
    print(f"OK: {label}")


# --- resolve_towns ---
print("=== Kritérium #1: resolve_towns.py ===")
results = resolve_towns.find_towns("Těškovice")
assert results, "Nenalezeno nic pro Těškovice"
code = results[0]["code"]
assert code == 512745, f"Špatný kód: {code}"
ok(f"Těškovice → {code}")

# --- Outage UTC→Prague ---
print("\n=== Outage + UTC→Prague převod ===")
# 05:30Z = 07:30 CEST (UTC+2), 09:30Z = 11:30 CEST
o = m.Outage(
    outage_id="110061080030",
    town_code=512745,
    town_name="Těškovice",
    date_from="2026-04-20T05:30:00Z",
    date_to="2026-04-20T09:30:00Z",
    kind="planned",
    announcement_pdf="https://api.bezstavy.cz/pdf/test.pdf",
    affected_towns="Bílovec, Bítov, Těškovice",
    source_url="https://www.bezstavy.cz",
)
msg = o.to_telegram()
print(msg)
assert "07:30" in msg, f"07:30 chybí v: {msg}"
assert "11:30" in msg, f"11:30 chybí v: {msg}"
ok("UTC→Prague správně (07:30 — 11:30)")

# --- Různé dny ---
print("\n=== Různé dny (přes půlnoc) ===")
o_noc = m.Outage(
    outage_id="ABC",
    town_code=512745,
    town_name="Paskov",
    date_from="2026-05-15T22:00:00Z",
    date_to="2026-05-16T06:00:00Z",
    kind="planned",
    announcement_pdf="",
    affected_towns="Paskov",
    source_url="https://www.bezstavy.cz",
)
msg_noc = o_noc.to_telegram()
print(msg_noc)
assert "16.05" in msg_noc, "Druhé datum chybí při přechodu přes půlnoc"
ok("různé dny formátovány správně")

# --- Deduplikace napříč obcemi ---
print("\n=== Deduplikace napříč obcemi ===")
o_bilovec = m.Outage(
    outage_id="110061080030",   # stejné ID, jiná obec
    town_code=599247,
    town_name="Bílovec",
    date_from="2026-04-20T05:30:00Z",
    date_to="2026-04-20T09:30:00Z",
    kind="planned",
    announcement_pdf="",
    affected_towns="Bílovec",
    source_url="https://www.bezstavy.cz",
)
fp1 = o.fingerprint()
fp2 = o_bilovec.fingerprint()
assert fp1 == fp2, f"Stejné outage_id → různý fingerprint!\n  {fp1}\n  {fp2}"
ok("fingerprint nezávisí na town_code/url")

# --- SQLite ---
print("\n=== SQLite: is_new / mark_seen ===")
conn = sqlite3.connect(":memory:")
m.init_db(conn)
assert m.is_new(conn, o)
m.mark_seen(conn, o)
assert not m.is_new(conn, o)
assert not m.is_new(conn, o_bilovec)   # stejný otisk → přeskočí
ok("deduplikace v DB funguje napříč obcemi")

# --- DRY_RUN ---
print("\n=== DRY_RUN ===")
cfg = {
    "telegram": {
        "bot_token": "DRY_RUN",
        "chat_id": "-1",
        "heartbeat_bot_token": "DRY_RUN",
        "heartbeat_chat_id": "-2",
    }
}
assert m.send_telegram(cfg, "test") is True
assert m.send_heartbeat(cfg, "heartbeat") is True
ok("DRY_RUN vrací True, nepadá")

# --- frozen + FINGERPRINT_VERSION ---
print("\n=== frozen + FINGERPRINT_VERSION ===")
try:
    o.outage_id = "hack"
    print("FAIL: frozen dovolila mutaci")
except Exception:
    ok("frozen=True funguje")
assert m.FINGERPRINT_VERSION == 3, f"Očekáváno 3, je {m.FINGERPRINT_VERSION}"
ok("FINGERPRINT_VERSION == 3")

print("\n=== VŠECHNY TESTY PROŠLY ===")
