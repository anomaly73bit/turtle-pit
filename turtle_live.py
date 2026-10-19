#!/usr/bin/env python3
"""
Turtle Pit Live - trading simulato in avanti su prezzi reali.

Ogni sera (GitHub Actions o a mano):
  1. scarica i prezzi di chiusura reali delle materie prime da Yahoo Finance;
  2. il robot Turtle elabora i giorni nuovi: apre, chiude, sposta gli stop;
  3. salva il portafoglio in state.json e rigenera la dashboard docs/index.html.

Il portafoglio parte con CAPITAL euro dal primo giorno in cui lo script gira.
Nessun soldo vero: e' paper trading su dati reali.
"""
import json, datetime, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent
STATE = ROOT / "state.json"
TEMPLATE = ROOT / "template.html"
OUT = ROOT / "docs" / "index.html"

CAPITAL = 500.0
RISK = 0.02          # rischio per trade (perdita se scatta lo stop)
COST = 0.0015        # spread + commissioni per lato
ENTRY, EXIT = 20, 10 # canali Donchian in giorni
HIST_START = "2019-06-01"

COMMODITIES = {
    "GC=F": ("Oro", "$/oncia"),
    "SI=F": ("Argento", "$/oncia"),
    "PL=F": ("Platino", "$/oncia"),
    "PA=F": ("Palladio", "$/oncia"),
    "HG=F": ("Rame", "$/libbra"),
    "CL=F": ("Petrolio WTI", "$/barile"),
    "BZ=F": ("Petrolio Brent", "$/barile"),
    "NG=F": ("Gas naturale", "$/MMBtu"),
    "HO=F": ("Gasolio", "$/gallone"),
    "RB=F": ("Benzina", "$/gallone"),
    "ZW=F": ("Grano", "¢/bushel"),
    "ZC=F": ("Mais", "¢/bushel"),
    "ZS=F": ("Soia", "¢/bushel"),
    "KC=F": ("Caffè", "¢/libbra"),
    "SB=F": ("Zucchero", "¢/libbra"),
    "CC=F": ("Cacao", "$/tonnellata"),
    "CT=F": ("Cotone", "¢/libbra"),
    "LE=F": ("Bovini vivi", "¢/libbra"),
    "HE=F": ("Suini", "¢/libbra"),
}
CAPX = min(1.0, 3.0 / len(COMMODITIES))  # esposizione totale massima 3x


# ---------- dati ----------
def download():
    import yfinance as yf
    raw = {}
    for tk, (name, unit) in COMMODITIES.items():
        try:
            df = yf.Ticker(tk).history(start=HIST_START, interval="1d", auto_adjust=False)
            s = df["Close"].dropna()
            s = s[s > 0]
            if len(s) < 120:
                print(f"  {name}: dati insufficienti, saltato")
                continue
            raw[tk.replace("=F", "").lower()] = [
                [d.strftime("%Y-%m-%d") for d in s.index],
                [round(float(v), 4) for v in s.values], name, unit]
            print(f"  {name:16s} {len(s):5d} giorni, ultimo {s.index[-1]:%Y-%m-%d}  {float(s.iloc[-1]):.2f}")
        except Exception as e:
            print(f"  {name}: errore {e}")
    return raw


# ---------- indicatori ----------
def indicators(d, p):
    n = len(p)
    N, atr = [None] * n, None
    for i in range(1, n):
        tr = abs(p[i] - p[i - 1])
        atr = (atr or 0) + tr / 19 if i < 20 else (atr * 19 + tr) / 20
        if i >= 20:
            N[i] = atr

    def chan(L):
        hi, lo = [None] * n, [None] * n
        for i in range(L, n):
            w = p[i - L:i]
            hi[i], lo[i] = max(w), min(w)
        return hi, lo

    return {"N": N, "e": chan(ENTRY), "x": chan(EXIT), "idx": {x: i for i, x in enumerate(d)}}


# ---------- robot ----------
def new_state(start):
    return {"start": start, "capital": CAPITAL, "cash": CAPITAL, "positions": {},
            "trades": [], "equity": [], "last": None, "lastpx": {}}


def equity(st):
    e = st["cash"]
    for k, P in st["positions"].items():
        e += P["dir"] * P["units"] * (st["lastpx"][k] - P["entry"])
    return e


def step_day(st, day, raw, ind):
    for k, (d, p, name, unit) in raw.items():
        I = ind[k]
        i = I["idx"].get(day)
        if i is None:
            continue
        px = p[i]
        st["lastpx"][k] = px
        P = st["positions"].get(k)
        if P:
            why = None
            if (P["dir"] > 0 and px < P["stop"]) or (P["dir"] < 0 and px > P["stop"]):
                why = "Stop 2N"
            elif P["dir"] > 0 and I["x"][1][i] is not None and px < I["x"][1][i]:
                why = f"Minimo {EXIT} gg"
            elif P["dir"] < 0 and I["x"][0][i] is not None and px > I["x"][0][i]:
                why = f"Massimo {EXIT} gg"
            if why:
                pnl = P["dir"] * P["units"] * (px - P["entry"]) - abs(P["units"] * px) * COST
                st["cash"] += pnl
                st["trades"].append({"day": day, "k": k, "act": f"Chiude {'long' if P['dir'] > 0 else 'short'} · {why}",
                                     "px": px, "pnl": round(pnl - P["cost"], 2)})
                del st["positions"][k]
                print(f"  {day} CHIUDE {name} a {px} ({why}) risultato {pnl - P['cost']:.2f} €")
        if k not in st["positions"] and I["N"][i] and I["e"][0][i] is not None:
            direction = 1 if px > I["e"][0][i] else -1 if px < I["e"][1][i] else 0
            if direction:
                eq = equity(st)
                units = min(eq * RISK / (2 * I["N"][i]), eq * CAPX / max(abs(px), 0.01))
                if units > 0 and eq > 0:
                    cost = abs(units * px) * COST
                    st["cash"] -= cost
                    st["positions"][k] = {"dir": direction, "units": units, "entry": px,
                                          "stop": px - direction * 2 * I["N"][i], "cost": cost, "day": day}
                    st["trades"].append({"day": day, "k": k, "px": px, "pnl": None,
                                         "act": "Compra (long)" if direction > 0 else "Vende allo scoperto (short)"})
                    print(f"  {day} APRE {name} {'LONG' if direction > 0 else 'SHORT'} a {px}")
    st["equity"].append([day, round(equity(st), 2)])
    st["last"] = day


def run_robot(raw):
    ind = {k: indicators(v[0], v[1]) for k, v in raw.items()}
    # Usa solo sedute chiuse: prima delle 21:00 UTC la candela di oggi e' ancora parziale.
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.strftime("%Y-%m-%d")
    cutoff = today if now.hour >= 21 else (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    days = sorted({x for v in raw.values() for x in v[0] if x <= cutoff})
    if STATE.exists():
        st = json.loads(STATE.read_text())
    else:
        # primo avvio: il robot parte dalla seduta di oggi (elaborata alla chiusura)
        st = new_state(today)
        print(f"Nuovo portafoglio da {CAPITAL:.0f} € a partire dalla seduta del {today}")
    todo = [x for x in days if x >= st["start"] and (st["last"] is None or x > st["last"])]
    for day in todo:
        step_day(st, day, raw, ind)
    if not todo:
        print("Nessuna seduta nuova da elaborare.")
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    return st


# ---------- dashboard ----------
def build(raw, st):
    last = max(v[0][-1] for v in raw.values())
    source = ("Dati reali di chiusura giornaliera dei futures continui da Yahoo Finance, aggiornati il "
              f"{datetime.datetime.now(datetime.timezone.utc):%d/%m/%Y %H:%M} UTC (ultima seduta {last}). "
              "I futures continui hanno piccoli salti al cambio di scadenza.")
    live = {k: st[k] for k in ("start", "capital", "cash", "positions", "trades", "equity", "last", "lastpx")}
    html = (TEMPLATE.read_text(encoding="utf-8")
            .replace("__DATA__", json.dumps(raw, separators=(",", ":")))
            .replace("__LIVE__", json.dumps(live, ensure_ascii=False, separators=(",", ":")))
            .replace("__SOURCE__", source))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text('<!doctype html><html lang="it"><head><meta charset="utf-8">'
                   '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                   '<style>[hidden]{display:none!important}body{margin:0}</style></head><body>'
                   + html + "</body></html>", encoding="utf-8")
    print(f"Dashboard aggiornata: {OUT}")


def main():
    print("Scarico i prezzi...")
    raw = download()
    if not raw:
        sys.exit("Nessun dato scaricato.")
    st = run_robot(raw)
    eq = st["equity"][-1][1] if st["equity"] else CAPITAL
    print(f"Capitale: {eq:.2f} €  ({(eq / CAPITAL - 1) * 100:+.1f}%)  posizioni aperte: {len(st['positions'])}")
    build(raw, st)


if __name__ == "__main__":
    main()
