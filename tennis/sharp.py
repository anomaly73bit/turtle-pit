"""
Quote "sharp" (Pinnacle, Betfair Exchange, Matchbook) e Unibet da The Odds API (https://the-odds-api.com).
La chiave arriva dal Secret ODDS_API_KEY. Piano gratuito: 500 richieste al mese; ogni torneo
interrogato costa 1 richiesta (fino a 10 bookmaker = 1 regione). L'elenco dei tornei e' gratuito.
"""
import json, os, urllib.error, urllib.parse, urllib.request

BASE = "https://api.the-odds-api.com/v4"
SHARP = ["pinnacle", "betfair_ex_eu", "matchbook"]
SOFT = ["unibet_nl", "unibet_it", "unibet_fr", "unibet_se"]


def _get(path, params):
    params = dict(params, apiKey=os.environ["ODDS_API_KEY"].strip())
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=30) as r:
        return json.loads(r.read()), {k.lower(): v for k, v in r.headers.items()}


ALL_TENNIS = []


def tennis_keys():
    """Tornei di tennis ATP/WTA attivi adesso (chiamata gratuita)."""
    data, _ = _get("/sports", {"all": "true"})
    ALL_TENNIS[:] = [[s["key"], s.get("title"), s.get("active")] for s in data if s.get("group") == "Tennis"]
    return [s["key"] for s in data if s.get("group") == "Tennis" and s.get("active")
            and (s["key"].startswith("tennis_atp") or s["key"].startswith("tennis_wta"))]


def fetch(keys, budget_left_min=0):
    """Quote testa a testa per i tornei indicati. Ritorna (eventi, richieste_rimaste)."""
    events, remaining = [], None
    for k in keys:
        if remaining is not None and int(remaining) <= budget_left_min:
            print(f"  richieste quasi finite ({remaining}): salto {k}")
            continue
        try:
            data, h = _get(f"/sports/{k}/odds", {"markets": "h2h", "oddsFormat": "decimal", "dateFormat": "iso",
                                                  "bookmakers": ",".join(SHARP + SOFT)})
        except urllib.error.HTTPError as e:
            print(f"  {k}: HTTP {e.code} {e.read()[:200]!r}")
            continue
        remaining = h.get("x-requests-remaining", remaining)
        for ev in data:
            ev["sport_key"] = k
            events.append(ev)
        print(f"  {k}: {len(data)} partite (richieste rimaste {remaining})")
    return events, remaining


def fair(ev):
    """Probabilita' 'giusta' di vittoria di home/away dalle quote sharp, tolto il margine.
    Media delle fonti sharp disponibili. Ritorna (p_home, fonti) oppure (None, [])."""
    ps, used = [], []
    for b in ev.get("bookmakers", []):
        if b["key"] not in SHARP:
            continue
        m = next((m for m in b.get("markets", []) if m["key"] == "h2h"), None)
        if not m or len(m["outcomes"]) != 2:
            continue
        o = {x["name"]: x["price"] for x in m["outcomes"]}
        if ev["home_team"] not in o or ev["away_team"] not in o:
            continue
        ih, ia = 1 / o[ev["home_team"]], 1 / o[ev["away_team"]]
        ps.append(ih / (ih + ia))
        used.append(b["key"])
    if not ps:
        return None, []
    # Pinnacle pesa il doppio se presente
    w = [2 if u == "pinnacle" else 1 for u in used]
    return sum(p * x for p, x in zip(ps, w)) / sum(w), used
