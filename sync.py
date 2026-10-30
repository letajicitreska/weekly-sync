"""Týdenní export (výstup se hned šifruje, nešifrovaný soubor se necommituje).
Nic z dat nevypisuje do logu (jen počty). Klíče bere z proměnných prostředí (GitHub Secrets)."""
import base64, json, os, sys, urllib.parse, urllib.request

CID = os.environ["FAKTUROID_CLIENT_ID"]
SEC = os.environ["FAKTUROID_CLIENT_SECRET"]
SLUG = os.environ.get("FAKTUROID_SLUG", "").strip()
UA = "Financni prehled (petr.holik@faktaoklimatu.cz)"
BASE = "https://app.fakturoid.cz/api/v3"
SINCE = os.environ.get("FAKTUROID_SINCE", "2023-01-01")

def req(url, data=None, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(r, timeout=60) as f:
        return json.loads(f.read().decode())

tok = req(BASE + "/oauth/token",
          data=json.dumps({"grant_type": "client_credentials"}).encode(),
          headers={"Authorization": "Basic " + base64.b64encode(f"{CID}:{SEC}".encode()).decode(),
                   "Content-Type": "application/json"})["access_token"]
AUTH = {"Authorization": "Bearer " + tok}

if not SLUG:
    accs = req(BASE + "/user.json", headers=AUTH).get("accounts", [])
    if len(accs) != 1:
        sys.exit(f"Účtů ve Fakturoidu je {len(accs)}, nastav secret FAKTUROID_SLUG.")
    SLUG = accs[0]["slug"]

def initials(name):
    parts = [p for p in name.replace(",", " ").split() if p[:1].isalpha()]
    return " ".join(p[0].upper() + "." for p in parts[:2]) or "?"

import re
def clean(t):
    # jména účastníků za názvem kurzu ("Úvod (...), Jana Nováková") se neukládají
    t = re.sub(r"\),.*$", ")", t.strip(), flags=re.S)
    return t[:200]

out, page = [], 1
while True:
    q = urllib.parse.urlencode({"page": page, "since": SINCE + "T00:00:00+01:00"})
    batch = req(f"{BASE}/accounts/{SLUG}/invoices.json?{q}", headers=AUTH)
    if not batch:
        break
    for i in batch:
        name = i.get("client_name") or ""
        is_org = bool(i.get("client_registration_no"))
        pays = []
        if i.get("status") not in ("paid", "cancelled") and i.get("id"):
            try:
                pj = req(f"{BASE}/accounts/{SLUG}/invoices/{i['id']}/payments.json", headers=AUTH)
                pays = [{"paid_on": p.get("paid_on"), "amount": p.get("amount")} for p in (pj or [])]
            except Exception:
                pays = None
        out.append({
            "payments": pays,
            "number": i.get("number"),
            "type": i.get("document_type"),
            "issued_on": i.get("issued_on"),
            "duzp": i.get("taxable_fulfillment_due"),
            "due_on": i.get("due_on"),
            "paid_on": i.get("paid_on"),
            "status": i.get("status"),
            "total": i.get("total"),
            "paid_amount": i.get("paid_amount"),
            "remaining_amount": i.get("remaining_amount"),
            "currency": i.get("currency"),
            "client": name if is_org else initials(name),
            "items": [{"name": clean(l.get("name") or ""), "qty": float(l.get("quantity") or 0), "price": float(l.get("unit_price") or 0)}
                      for l in (i.get("lines") or []) if l.get("name")],
        })
    page += 1
    if page > 200:
        break

out.sort(key=lambda x: (x["issued_on"] or "", x["number"] or ""))
os.makedirs("data", exist_ok=True)
with open("data/out.json", "w", encoding="utf-8") as f:
    json.dump({"since": SINCE, "count": len(out), "invoices": out}, f, ensure_ascii=False, indent=1)
print(f"OK: {len(out)} dokladů")
