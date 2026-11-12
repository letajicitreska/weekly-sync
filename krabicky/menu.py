"""Jídelníček Beru krabiček na zítřek -> push notifikace přes ntfy.

Spouští .github/workflows/krabicky.yml (NE–ČT kolem 16:40 pražského času).
Zdroj: veřejné API objednávkového systému, které používá berukrabicky.cz/jidelnicek.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

API = "https://europe-central2-food-checkout-1.cloudfunctions.net/api-storefront/menu?weekNumber={}"
PROGRAM = "pp"      # Protein (sm = s masem, bm = bez masa)
KCAL = "2500"
TOPIC = os.environ.get("NTFY_TOPIC", "petr-krabicky-4e86caa27840")
LINK = "https://www.berukrabicky.cz/jidelnicek"
DNY = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
KEYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
TZ = ZoneInfo("Europe/Prague")


def should_run(now):
    """GitHub cron je v UTC: běží 14:40 i 15:40 UTC, pošle se jen ten běh,
    který odpovídá 16:40 v Praze (zimní i letní čas)."""
    sched = os.environ.get("SCHEDULE", "")
    if not sched:
        return True  # ruční spuštění
    utc_hour = int(sched.split()[1])
    offset = int(now.utcoffset().total_seconds() // 3600)
    return utc_hour + offset == 16


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


def notify(title, body):
    req = urllib.request.Request(
        f"https://ntfy.sh/{TOPIC}",
        data=body.encode("utf-8"),
        headers={"Title": title.encode("utf-8"), "Click": LINK, "Tags": "bento", "Priority": "high"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30)


def main():
    now = datetime.now(TZ)
    if not should_run(now):
        print("Tento běh nepatří k 16:40 v Praze, přeskakuji.")
        return
    tomorrow = (now + timedelta(days=1)).date()
    label = f"{DNY[tomorrow.weekday()]} {tomorrow.day}. {tomorrow.month}."
    day = find_day(tomorrow)
    meals = (day or {}).get("meals", {}).get(KCAL, {}).get(PROGRAM)
    if not meals:
        notify(f"Krabičky na zítřek ({label})", f"Jídelníček na zítřek se nepodařilo zjistit.\n{LINK}")
        print("Menu nenalezeno.")
        return
    lines = [f"{m['mealType']}: {m['food'].strip()}" for _, m in sorted(meals.items(), key=lambda kv: int(kv[0]))]
    notify(f"Zítra ({label}) – Protein {KCAL} kcal", "\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
