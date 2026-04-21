# cez-monitor

Periodická kontrola plánovaných odstávek elektřiny (ČEZ Distribuce) pro vybrané
obce. Při nálezu nové odstávky pošle upozornění do Telegramu.

Zdroj dat: `api.bezstavy.cz` — veřejné API třetí strany.

---

## Požadavky

- Python 3.10+
- Přístup k internetu
- Telegram bot (viz níže)

---

## Instalace

```bash
git clone <repo> cez-monitor
cd cez-monitor
pip3 install -r requirements.txt
cp config.example.yaml config.yaml
```

Vyplň `config.yaml` — viz komentáře v souboru.

### Vytvoření Telegram botu

1. Napiš `/newbot` uživateli [@BotFather](https://t.me/BotFather)
2. Zkopíruj `token`
3. Přidej bota do skupiny nebo kanálu, zjisti `chat_id`  
   (např. přes `https://api.telegram.org/bot<TOKEN>/getUpdates`)

---

## Zjištění RUIAN kódu obce

```bash
python3 resolve_towns.py Název obce
```

Kód (6 číslic) vlož do `config.yaml` pod `obce`.

---

## Ruční spuštění (ověření funkčnosti)

```bash
python3 cez_monitor.py
```

Výstup jde do terminálu i do `cez_monitor.log`.

Pro testování bez Telegramu nastav v `config.yaml`:

```yaml
telegram:
  bot_token: DRY_RUN
  chat_id: '-1'
  heartbeat_bot_token: DRY_RUN
  heartbeat_chat_id: '-2'
```

---

## Pravidelné spouštění (cron)

### Doporučené nastavení

Skript stačí spouštět **1× týdně** — ČEZ musí plánované odstávky vyhlásit
min. 15 dní předem. Neposílej dotazy častěji než 2× denně.

### Postup

1. Zjisti absolutní cesty:

```bash
which python3          # např. /usr/bin/python3
pwd                    # adresář projektu, např. /home/tomas/cez-monitor
```

2. Otevři crontab:

```bash
crontab -e
```

3. Přidej řádek (zde: každé pondělí v 08:00):

```cron
0 8 * * 1 /usr/bin/python3 /home/tomas/cez-monitor/cez_monitor.py >> /home/tomas/cez-monitor/cron.log 2>&1
```

Uprav cestu k `python3` a k adresáři projektu dle svého VPS.

### Alternativní frekvence

| Cron výraz        | Frekvence                  |
|-------------------|----------------------------|
| `0 8 * * 1`       | každé pondělí v 08:00      |
| `0 8 * * 1,4`     | pondělí + čtvrtek v 08:00  |
| `0 8 1,15 * *`    | 1. a 15. v měsíci v 08:00  |

### Ověření cronu

```bash
# Zobraz aktivní záznamy
crontab -l

# Sleduj log po dalším naplánovaném běhu
tail -f /home/tomas/cez-monitor/cron.log
```

### Poznámky

- Skript používá zámek `/tmp/cez-monitor.lock` — paralelní spuštění je
  bezpečně odmítnuto, takže duplicitní cron záznamy nevadí.
- Heartbeat Telegram zpráva přijde při každém běhu — pokud přestane chodit,
  cron nebo skript nefunguje.
- `config.yaml` a `seen_outages.sqlite` **nesmí být v gitu** (`.gitignore` to hlídá).

---

## Soubory

| Soubor                | Popis                                      |
|-----------------------|--------------------------------------------|
| `cez_monitor.py`      | hlavní skript                              |
| `resolve_towns.py`    | vyhledání RUIAN kódu obce                 |
| `config.yaml`         | konfigurace (nesdílej, není v gitu)        |
| `config.example.yaml` | šablona konfigurace                        |
| `seen_outages.sqlite` | DB známých odstávek (vzniká za běhu)       |
| `cez_monitor.log`     | aplikační log                              |
| `cron.log`            | stdout/stderr z cronu                      |
