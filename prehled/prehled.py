"""Pondělní přehled automatizací -> jedna zpráva přes ntfy (vždy, i když je vše v pořádku).

Spouští .github/workflows/prehled.yml: hlavně cron-job.org v pondělí 9:30 Praha (dispatch),
záložně GitHub cron. Hodnotí minulý týden (pondělí 00:00 až neděle 23:59 pražského času):
- krabičky: ve kolik dnech (NE–ČT) běh skutečně odeslal menu (log „ntfy přijalo“),
- Fakturoid sync: commit „sync …“ v pátek,
- reels (2050-autopost, plan/schedule.json): kolik mělo vyjít, kolik vyšlo, se zpožděním,
- připomínky (2050-autopost, plan/reminders.json): kolik odešlo, kolik pozdě (> 15 min), kolik visí,
- selhané běhy GitHub Actions v obou repech.
Úlohy v Claudovi (LinkedIn, finance, story) odsud vidět nejsou; ty hlásí selhání samy.
DRY=1 jen vypíše zprávu, nic neposílá. Téma ntfy je tajemství, nikdy ho nevypisovat.
"""
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Europe/Prague")
OWNER = "letajicitreska"
API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
H = {"Accept": "application/vnd.github+json"}
if TOKEN:
    H["Authorization"] = f"Bearer {TOKEN}"
DRY = os.environ.get("DRY") == "1"
DNY = ["po", "út", "st", "čt", "pá", "so", "ne"]


def week_bounds(now):
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = monday - timedelta(days=7)
    return start, monday  # [start, end)


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def runs(repo, start, end):
    out, page = [], 1
    rng = f"{start.astimezone(ZoneInfo('UTC')):%Y-%m-%dT%H:%M:%SZ}..{end.astimezone(ZoneInfo('UTC')):%Y-%m-%dT%H:%M:%SZ}"
    while True:
        r = requests.get(f"{API}/repos/{OWNER}/{repo}/actions/runs",
                         params={"per_page": 100, "page": page, "created": rng}, headers=H, timeout=30)
        r.raise_for_status()
        batch = r.json()["workflow_runs"]
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def raw(repo, path):
    r = requests.get(f"https://raw.githubusercontent.com/{OWNER}/{repo}/main/{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def krabicky(start, end, ws_runs):
    """Dny, kdy menu opravdu odešlo (podle logu běhu). Čeká se NE–ČT = 5 dní."""
    expected = [start + timedelta(days=i) for i in range(7) if (start + timedelta(days=i)).weekday() in (6, 0, 1, 2, 3)]
    sent_days = set()
    for run in ws_runs:
        if run["name"] != "krabicky" or run.get("conclusion") != "success":
            continue
        r = requests.get(run["logs_url"], headers=H, timeout=60)
        if not r.ok:
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            text = "\n".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist())
        for m in re.finditer(r"^(\S+Z) ntfy přijalo: 2\d\d", text, re.M):
            sent_days.add(parse(m.group(1)).astimezone(TZ).date())
    ok = [d for d in expected if d.date() in sent_days]
    missing = [DNY[d.weekday()] for d in expected if d.date() not in sent_days]
    line = f"Krabičky: {len(ok)}/{len(expected)}"
    if missing:
        line += " ✗ chybí " + ", ".join(missing)
    else:
        line += " ✓"
    return line, bool(missing)


def fakturoid(start, end):
    r = requests.get(f"{API}/repos/{OWNER}/weekly-sync/commits",
                     params={"since": start.isoformat(), "until": end.isoformat(), "per_page": 100}, headers=H, timeout=30)
    r.raise_for_status()
    friday = (start + timedelta(days=4)).date()
    hits = [parse(c["commit"]["committer"]["date"]).astimezone(TZ) for c in r.json()
            if c["commit"]["message"].startswith("sync ")]
    fri = [t for t in hits if t.date() == friday]
    if fri:
        t = min(fri)
        late = t.hour > 13 or (t.hour == 13 and t.minute >= 47)
        return (f"Fakturoid sync pá {t:%H:%M}" + (" ✗ po startu financí (13:47)" if late else " ✓")), late
    return "Fakturoid sync v pátek ✗ neproběhl", True


def reels(start, end):
    items = raw("2050-autopost", "plan/schedule.json")
    items = items["items"] if isinstance(items, dict) else items
    due = [i for i in items if i.get("publish_at") and start <= parse(i["publish_at"]) < end
           and i.get("status") not in ("parked",)]
    posted = [i for i in due if i.get("status") == "posted"]
    late = [i for i in posted if i.get("posted_at") and parse(i["posted_at"]) - parse(i["publish_at"]) > timedelta(hours=2)]
    bad = len(posted) < len(due) or late
    line = f"Reels: {len(posted)}/{len(due)} vyšly"
    if len(posted) < len(due):
        line += " ✗ nevyšlo: " + ", ".join(i["id"] for i in due if i not in posted)
    elif late:
        line += f" ✗ {len(late)} s velkým zpožděním"
    else:
        line += " ✓"
    return line, bad


def reminders(start, end, now):
    items = raw("2050-autopost", "plan/reminders.json").get("items", [])
    due = [i for i in items if start <= parse(i["at"]) < end]
    sent = [i for i in due if i.get("sent")]
    late = [i for i in sent if parse(i["sent"]) - parse(i["at"]) > timedelta(minutes=15)]
    hanging = [i for i in items if not i.get("sent") and parse(i["at"]) < now - timedelta(minutes=15)]
    if not due and not hanging:
        return "Připomínky: žádné nebyly v plánu", False
    line = f"Připomínky: {len(sent)}/{len(due)} odešly"
    bad = False
    if late:
        line += f", {len(late)} pozdě"
        bad = True
    if hanging:
        line += f" ✗ {len(hanging)} neodešlo"
        bad = True
    if not bad:
        line += " ✓"
    return line, bad


def failures(repo_runs):
    lines = []
    for repo, rs in repo_runs.items():
        failed = {}
        for r in rs:
            if r.get("conclusion") == "failure":
                failed[r["name"]] = failed.get(r["name"], 0) + 1
        if failed:
            lines.append(f"{repo}: " + ", ".join(f"{n} {c}×" for n, c in failed.items()))
    if not lines:
        return "Selhané běhy GitHubu: žádné ✓", False
    return "Selhané běhy ✗ " + "; ".join(lines), True


def main():
    now = datetime.now(TZ)
    start, end = week_bounds(now)
    ws = runs("weekly-sync", start, end)
    ap = runs("2050-autopost", start, end)
    checks = []
    for fn in (lambda: krabicky(start, end, ws), lambda: fakturoid(start, end), lambda: reels(start, end),
               lambda: reminders(start, end, now), lambda: failures({"weekly-sync": ws, "2050-autopost": ap})):
        try:
            checks.append(fn())
        except Exception as e:  # noqa: BLE001 — jedna kontrola nesmí shodit celý přehled
            checks.append((f"Kontrola selhala: {type(e).__name__}: {str(e)[:120]}", True))
    bad = any(b for _, b in checks)
    title = f"Automatizace {start:%-d. %-m.}–{(end - timedelta(days=1)):%-d. %-m.}: " + ("něco nesedí ✗" if bad else "vše OK ✓")
    body = "\n".join(l for l, _ in checks) + "\n(LinkedIn, finance a story hlásí selhání samy.)"
    print(title)
    print(body)
    if DRY:
        return
    topic = os.environ["NTFY_TOPIC"]
    r = requests.post("https://ntfy.sh/", json={"topic": topic, "title": title, "message": body,
                                                "priority": 4 if bad else 3, "tags": ["clipboard"]}, timeout=30)
    print("ntfy přijalo:", r.status_code)  # odpověď obsahuje téma, nevypisovat
    if not r.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
