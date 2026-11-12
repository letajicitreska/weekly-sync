"""Jídelníček Beru krabiček na zítřek -> push notifikace přes ntfy.

Spouští .github/workflows/krabicky.yml (NE–ČT, tři běhy mezi 11. a 14. hodinou UTC).
GitHub cron chodí i o hodiny později, proto běh naběhne s rezervou, počká do 16:40
pražského času (zimní i letní čas řeší ZoneInfo) a pošle okamžitou zprávu. ntfy zprávy
s odkladem ("delay") se na iPhonu jako upozornění neukážou, jen v appce (ověřeno 12. 11. 2026).
Každý běh se nejdřív (a znovu po čekání) podívá do tématu ntfy; když tam zpráva na zítřek
už je, nic neposílá. Ruční spuštění posílá vždy a hned (test).
Zdroj: veřejné API objednávkového systému, které používá berukrabicky.cz/jidelnicek.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

API = "https://europe-central2-food-checkout-1.cloudfunctions.net/api-storefront/menu?weekNumber={}"
PROGRAM = "pp"      # Protein (sm = s masem, bm = bez masa)
KCAL = "2500"
TOPIC = os.environ["NTFY_TOPIC"]  # tajná hodnota v nastavení repa, nikdy nevypisovat
LINK = "https://www.berukrabicky.cz/jidelnicek"
DNY = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
KEYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
TZ = ZoneInfo("Europe/Prague")
DELIVER_AT = dtime(16, 40)


def fetch(week):
    req = urllib.request.Request(API.format(week), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def find_day(target):
    datum = f"{target.day}.{target.month}.{target.year}"
    key = KEYS[target.weekday()]
    for week in range(1, 5):
        try:
            data = fetch(week)
        except Exception as e:  # noqa: BLE001
            print(f"week {week}: {e}", file=sys.stderr)
            continue
        for w in data.values():
            day = (w or {}).get("days", {}).get(key)
            if day and day.get("date") == datum:
                return day
    return None


def already_queued(label):
    """Je v tématu (odeslaná nebo naplánovaná) zpráva na tentýž den? Pozná se podle data
    v titulku. Odpověď obsahuje téma, nevypisovat."""
    url = f"https://ntfy.sh/{TOPIC}/json?poll=1&since=20h"
    with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
        for line in r.read().decode("utf-8").splitlines():
            try:
                if f"({label})" in json.loads(line).get("title", ""):
                    return True
            except ValueError:
                continue
    return False


def notify(title, body):
    payload = {
        "topic": TOPIC,
        "title": title,
        "message": body,
        "priority": 4,
        "tags": ["bento"],
        "click": LINK,
    }
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print("ntfy přijalo:", r.status)  # odpověď obsahuje téma, nevypisovat


def main():
    now = datetime.now(TZ)
    manual = not os.environ.get("SCHEDULE")
    tomorrow = (now + timedelta(days=1)).date()
    label = f"{DNY[tomorrow.weekday()]} {tomorrow.day}. {tomorrow.month}."

    if not manual:
        if already_queued(label):
            print("Zpráva na zítřek už je v ntfy, přeskakuji.")
            return
        deliver = datetime.combine(now.date(), DELIVER_AT, TZ)
        if now.hour < 6 or now > deliver + timedelta(hours=5):
            print("Běh přišel moc pozdě (GitHub zpoždění), přeskakuji.")
            return
        wait = (deliver - now).total_seconds()
        if wait > 0:
            print(f"Čekám {int(wait)} s do 16:40")
            time.sleep(wait)
            if already_queued(label):  # mezitím mohl poslat jiný běh
                print("Zpráva na zítřek už je v ntfy, přeskakuji.")
                return

    day = find_day(tomorrow)
    meals = (day or {}).get("meals", {}).get(KCAL, {}).get(PROGRAM)
    if not meals:
        notify(f"Krabičky na zítřek ({label})",
               f"Jídelníček na zítřek se nepodařilo zjistit.\n{LINK}")
        print("Menu nenalezeno.")
        return
    lines = [f"{m['mealType']}: {m['food'].strip()}" for _, m in sorted(meals.items(), key=lambda kv: int(kv[0]))]
    notify(f"Zítra ({label}) – Protein {KCAL} kcal", "\n".join(lines))
    print("Menu nalezeno, počet jídel:", len(lines))


if __name__ == "__main__":
    main()
