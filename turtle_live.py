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
            df = df[["Open", "High", "Low", "Close"]].dropna()
            df = df[(df > 0).all(axis=1)]
            if len(df) < 120:
                print(f"  {name}: dati insufficienti, saltato")
                continue
            r4 = lambda col: [round(float(v), 4) for v in df[col].values]
            o, h, l, c = r4("Open"), r4("High"), r4("Low"), r4("Close")
            # coerenza OHLC: massimo e minimo devono contenere apertura e chiusura
            h = [max(x, y, z) for x, y, z in zip(h, o, c)]
            l = [min(x, y, z) for x, y, z in zip(l, o, c)]
            raw[tk.replace("=F", "").lower()] = [
                [d.strftime("%Y-%m-%d") for d in df.index], c, name, unit, h, l, o]
            print(f"  {name:16s} {len(df):5d} giorni, ultimo {df.index[-1]:%Y-%m-%d}  {c[-1]:.2f}")
        except Exception as e:
            print(f"  {name}: errore {e}")
    return raw


# ---------- indicatori ----------
def indicators(v):
    d, c = v[0], v[1]
    h = v[4] if len(v) > 4 else c
    l = v[5] if len(v) > 5 else c
    o = v[6] if len(v) > 6 else c
    n = len(c)
    N, atr = [None] * n, None
    for i in range(1, n):
        tr = max(h[i], c[i - 1]) - min(l[i], c[i - 1])
        atr = (atr or 0) + tr / 19 if i < 20 else (atr * 19 + tr) / 20
        if i >= 20:
            N[i] = atr

    def chan(L):
        hi, lo = [None] * n, [None] * n
        for i in range(L, n):
            hi[i], lo[i] = max(h[i - L:i]), min(l[i - L:i])
        return hi, lo

    return {"N": N, "e": chan(ENTRY), "x": chan(EXIT), "o": o, "h": h, "l": l, "c": c,
            "idx": {x: i for i, x in enumerate(d)}}


# ---------- robot ----------
# Ordini automatici come i Turtles: entra appena il prezzo tocca il massimo/minimo a 20 giorni,
# esce appena tocca lo stop 2N o il minimo/massimo a 10 giorni. Se il mercato apre gia' oltre
# il livello, l'ordine si esegue all'apertura. Il valore del portafoglio si calcola alla chiusura.
def new_state(start):
    return {"start": start, "capital": CAPITAL, "cash": CAPITAL, "positions": {},
            "trades": [], "equity": [], "last": None, "lastpx": {}}


def equity(st):
    e = st["cash"]
    for k, P in st["positions"].items():
        e += P["dir"] * P["units"] * (st["lastpx"].get(k, P["entry"]) - P["entry"])
    return e


def step_day(st, day, raw, ind):
    for k, v in raw.items():
        name = v[2]
        I = ind[k]
        i = I["idx"].get(day)
        if i is None:
            continue
        o, h, l, c = I["o"][i], I["h"][i], I["l"][i], I["c"][i]
        P = st["positions"].get(k)
        if P and P["day"] != day:
            why = fill = None
            if P["dir"] > 0:
                lx = I["x"][1][i]
                trig = max(P["stop"], lx) if lx is not None else P["stop"]
                if l <= trig:
                    fill = min(o, trig)
                    why = "Stop 2N" if trig == P["stop"] else f"Minimo {EXIT} gg"
            else:
                hx = I["x"][0][i]
                trig = min(P["stop"], hx) if hx is not None else P["stop"]
                if h >= trig:
                    fill = max(o, trig)
                    why = "Stop 2N" if trig == P["stop"] else f"Massimo {EXIT} gg"
            if why:
                gross = P["dir"] * P["units"] * (fill - P["entry"])
                exit_cost = abs(P["units"] * fill) * COST
                st["cash"] += gross - exit_cost
                net = gross - exit_cost - P["cost"]
                st["trades"].append({
                    "day": day, "k": k, "kind": "close", "dir": P["dir"], "why": why,
                    "act": f"Chiude {'long' if P['dir'] > 0 else 'short'} · {why}",
                    "px": round(fill, 4), "pnl": round(net, 2), "units": P["units"],
                    "notional": round(P["notional"], 2), "entryDay": P["day"], "entryPx": P["entry"],
                    "ret": round(net / P["notional"], 4) if P["notional"] else 0})
                del st["positions"][k]
                print(f"  {day} CHIUDE {name} a {fill:.4f} ({why}) netto {net:+.2f} €")
        Nk = I["N"][i - 1] if i > 0 else None
        if k not in st["positions"] and Nk and I["e"][0][i] is not None:
            up, dn = I["e"][0][i], I["e"][1][i]
            lh, sh = h > up, l < dn
            direction = (1 if c >= (up + dn) / 2 else -1) if (lh and sh) else 1 if lh else -1 if sh else 0
            if direction:
                fill = max(o, up) if direction > 0 else min(o, dn)
                st["lastpx"].setdefault(k, fill)
                eq = equity(st)
                units = min(eq * RISK / (2 * Nk), eq * CAPX / max(abs(fill), 0.01))
                if units > 0 and eq > 0:
                    notional = abs(units * fill)
                    cost = notional * COST
                    st["cash"] -= cost
                    st["positions"][k] = {"dir": direction, "units": units, "entry": round(fill, 4),
                                          "stop": round(fill - direction * 2 * Nk, 4), "cost": cost,
                                          "notional": round(notional, 2), "day": day}
                    st["trades"].append({"day": day, "k": k, "kind": "open", "dir": direction,
                                         "px": round(fill, 4), "pnl": None, "units": units,
                                         "notional": round(notional, 2),
                                         "act": "Compra (long)" if direction > 0 else "Vende allo scoperto (short)"})
                    print(f"  {day} APRE {name} {'LONG' if direction > 0 else 'SHORT'} a {fill:.4f}, investito {notional:.2f} €")
        st["lastpx"][k] = c
    st["equity"].append([day, round(equity(st), 2)])
    st["last"] = day


def run_robot(raw):
    ind = {k: indicators(v) for k, v in raw.items()}
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
