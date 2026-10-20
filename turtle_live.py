#!/usr/bin/env python3
"""
Turtle Pit Live - trading simulato come un conto reale XTB, su prezzi reali.

Ogni ora a mercati aperti e ogni notte (GitHub Actions):
  1. scarica da Yahoo Finance i prezzi giornalieri (apertura, massimo, minimo, chiusura)
     delle materie prime e il cambio EUR/USD;
  2. il robot Turtle elabora le sedute chiuse: apre, chiude, paga spread, swap e cambio
     come su un conto CFD XTB in euro. Le sedute chiuse vengono salvate in state.json;
  3. la seduta in corso viene simulata a parte come "provvisoria" e non viene salvata;
  4. rigenera la dashboard docs/index.html.

Nessun soldo vero: e' paper trading su dati reali con costi e vincoli reali.
"""
import copy, json, datetime, math, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent
STATE = ROOT / "state.json"
TEMPLATE = ROOT / "template.html"
OUT = ROOT / "docs" / "index.html"

CAPITAL = 500.0      # euro
RISK = 0.02          # rischio previsto per trade: 2% del capitale se scatta lo stop
MAX_RISK = 0.04      # se il lotto minimo XTB rischia piu' del 4%, il trade si salta
ENTRY, EXIT = 20, 10 # canali Donchian in giorni
SLIP_N = 0.05        # slittamento degli ordini stop: 5% della volatilita' giornaliera N
SWAP_LONG = 0.000235 # costo notturno XTB, frazione del valore al giorno (esempi ufficiali XTB)
SWAP_SHORT = 0.00003
FX_FEE = 0.005       # 0,5% di ricarico XTB sulla conversione USD -> EUR del risultato
START_FX = 1.16      # cambio di riserva se manca il dato EUR/USD
HIST_START = "2019-06-01"

# Ticker Yahoo -> nome, unita' di prezzo Yahoo, scheda CFD XTB.
# xtb: nome dello strumento XTB; mult: valore nominale di 1 lotto per 1 unita' di prezzo XTB;
# f: fattore da prezzo Yahoo a prezzo XTB; minlot/step: lotto minimo e passo; spread in unita'
# XTB; lev: leva massima ESMA per clienti privati (oro 20:1, altre materie prime 10:1).
# Fonte: Specification Table XTB (CFD commodities), valori indicativi; "ok" = verificato sul conto XTB.
# swapL/swapS: swap giornaliero dello strumento, se verificato (altrimenti SWAP_LONG/SWAP_SHORT).
COMMODITIES = {
    "GC=F": ("Oro", "$/oncia", dict(xtb="GOLD", mult=100, f=1, minlot=0.003, step=0.001, spread=0.35, lev=20)),
    "SI=F": ("Argento", "$/oncia", dict(xtb="SILVER", mult=5000, f=1, minlot=0.003, step=0.001, spread=0.035, lev=10)),
    "PL=F": ("Platino", "$/oncia", dict(xtb="PLATINUM", mult=150, f=1, minlot=0.005, step=0.001, spread=7.6, lev=10)),
    "PA=F": ("Palladio", "$/oncia", dict(xtb="PALLADIUM", mult=100, f=1, minlot=0.005, step=0.001, spread=20.5, lev=10)),
    "HG=F": ("Rame", "$/libbra", dict(xtb="COPPER", mult=30, f=2204.62, minlot=0.005, step=0.001, spread=23, lev=10)),
    "CL=F": ("Petrolio WTI", "$/barile", dict(xtb="OIL.WTI", mult=1000, f=1, minlot=0.005, step=0.005, spread=0.04, lev=10)),
    "BZ=F": ("Petrolio Brent", "$/barile", dict(xtb="OIL", mult=1000, f=1, minlot=0.005, step=0.005, spread=0.04, lev=10)),
    "NG=F": ("Gas naturale", "$/MMBtu", dict(xtb="NATGAS", mult=30000, f=1, minlot=0.005, step=0.005, spread=0.005, lev=10)),
    "RB=F": ("Benzina", "$/gallone", dict(xtb="GASOLINE", mult=420, f=100, minlot=0.005, step=0.005, spread=0.5, lev=10)),
    "ZW=F": ("Grano", "¢/bushel", dict(xtb="WHEAT", mult=400, f=1, minlot=0.005, step=0.001, spread=1.26, lev=10,
                                         swapL=0.00023278, swapS=0.00001722, ok="20/10/2026")),
    "ZC=F": ("Mais", "¢/bushel", dict(xtb="CORN", mult=500, f=1, minlot=0.005, step=0.001, spread=0.56, lev=10,
                                       swapL=0.00023278, swapS=0.00001722, ok="20/10/2026")),
    "ZS=F": ("Soia", "¢/bushel", dict(xtb="SOYBEAN", mult=300, f=1, minlot=0.005, step=0.001, spread=1.0, lev=10,
                                       swapL=0.00023278, swapS=0.00001722, ok="20/10/2026")),
    "KC=F": ("Caffè", "¢/libbra", dict(xtb="COFFEE", mult=2000, f=1, minlot=0.001, step=0.001, spread=0.25, lev=10,
                                         swapL=0.00034389, swapS=0.00012833, ok="20/10/2026")),
    "SB=F": ("Zucchero", "¢/libbra", dict(xtb="SUGAR", mult=1120, f=1, minlot=0.01, step=0.01, spread=0.06, lev=10)),
    "CC=F": ("Cacao", "$/tonnellata", dict(xtb="COCOA", mult=10, f=1, minlot=0.005, step=0.005, spread=13, lev=10)),
    "CT=F": ("Cotone", "¢/libbra", dict(xtb="COTTON", mult=500, f=1, minlot=0.01, step=0.005, spread=0.19, lev=10)),
    "LE=F": ("Bovini vivi", "¢/libbra", dict(xtb="CATTLE", mult=400, f=1, minlot=0.005, step=0.005, spread=0.1, lev=10)),
    "HE=F": ("Suini", "¢/libbra", dict(xtb="LEANHOGS", mult=400, f=1, minlot=0.01, step=0.005, spread=0.1, lev=10)),
}
CAPX = min(1.0, 3.0 / len(COMMODITIES))  # esposizione prevista totale massima 3x il capitale


# ---------- dati ----------
def download():
    import yfinance as yf
    raw = {}
    for tk, (name, unit, spec) in COMMODITIES.items():
        try:
            df = yf.Ticker(tk).history(start=HIST_START, interval="1d", auto_adjust=False)
            df = df[["Open", "High", "Low", "Close"]].dropna()
            df = df[(df > 0).all(axis=1)]
            if len(df) < 120:
                print(f"  {name}: dati insufficienti, saltato")
                continue
            r4 = lambda col: [round(float(v), 4) for v in df[col].values]
            o, h, l, c = r4("Open"), r4("High"), r4("Low"), r4("Close")
            h = [max(x, y, z) for x, y, z in zip(h, o, c)]
            l = [min(x, y, z) for x, y, z in zip(l, o, c)]
            raw[tk.replace("=F", "").lower()] = [
                [d.strftime("%Y-%m-%d") for d in df.index], c, name, unit, h, l, o, spec]
            print(f"  {name:16s} {len(df):5d} giorni, ultimo {df.index[-1]:%Y-%m-%d}  {c[-1]:.2f}")
        except Exception as e:
            print(f"  {name}: errore {e}")
    fx = [[], []]
    try:
        df = yf.Ticker("EURUSD=X").history(start=HIST_START, interval="1d", auto_adjust=False)["Close"].dropna()
        fx = [[d.strftime("%Y-%m-%d") for d in df.index], [round(float(v), 5) for v in df.values]]
        print(f"  EUR/USD          {len(fx[0]):5d} giorni, ultimo {fx[0][-1]}  {fx[1][-1]}")
    except Exception as e:
        print(f"  EUR/USD: errore {e}, uso {START_FX}")
    return raw, fx


class FX:
    """Cambio EUR/USD (dollari per 1 euro) all'ultima data disponibile <= giorno."""
    def __init__(self, fx):
        self.d, self.v, self.i = fx[0], fx[1], 0

    def at(self, day):
        if not self.d:
            return START_FX
        import bisect
        j = bisect.bisect_right(self.d, day) - 1
        return self.v[j] if j >= 0 else self.v[0]


# ---------- indicatori ----------
def indicators(v):
    d, c, h, l, o, spec = v[0], v[1], v[4], v[5], v[6], v[7]
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
            "cu": spec["mult"] * spec["f"], "hs": spec["spread"] / spec["f"] / 2, "spec": spec,
            "idx": {x: i for i, x in enumerate(d)}}


# ---------- robot ----------
# Ordini automatici come i Turtles: entra appena il prezzo tocca il massimo/minimo a 20 giorni,
# esce appena tocca lo stop 2N o il minimo/massimo a 10 giorni. Gli ordini stop slittano di
# SLIP_N*N; se il mercato apre gia' oltre il livello si esegue all'apertura. Si compra al prezzo
# lettera e si vende al denaro (meta' spread per lato). Il conto e' in euro.
def new_state(start):
    return {"start": start, "capital": CAPITAL, "cash": CAPITAL, "positions": {},
            "trades": [], "skipped": [], "equity": [], "last": None, "lastpx": {}, "lastfx": START_FX}


def unreal_eur(st, ind):
    e = 0.0
    for k, P in st["positions"].items():
        px = st["lastpx"].get(k, P["entry"])
        e += P["dir"] * P["lots"] * ind[k]["cu"] * (px - P["entry"]) / st["lastfx"]
    return e


def used_margin(st, ind):
    m = 0.0
    for k, P in st["positions"].items():
        px = st["lastpx"].get(k, P["entry"])
        m += P["lots"] * ind[k]["cu"] * px / ind[k]["spec"]["lev"] / st["lastfx"]
    return m


def step_day(st, day, raw, ind, fx):
    rate = fx.at(day)
    prev_day = st["last"]
    for k, v in raw.items():
        name = v[2]
        I = ind[k]
        i = I["idx"].get(day)
        if i is None:
            continue
        o, h, l, c = I["o"][i], I["h"][i], I["l"][i], I["c"][i]
        cu, hs, spec = I["cu"], I["hs"], I["spec"]
        Nk = I["N"][i - 1] if i > 0 else None
        slip = SLIP_N * Nk if Nk else 0.0
        P = st["positions"].get(k)
        # costo notturno (swap) per le notti passate dall'ultima seduta
        if P and P["day"] != day:
            nights = (datetime.date.fromisoformat(day) - datetime.date.fromisoformat(P["swapDay"])).days
            px_prev = st["lastpx"].get(k, P["entry"])
            sw = P["lots"] * cu * px_prev * (spec.get("swapL", SWAP_LONG) if P["dir"] > 0 else spec.get("swapS", SWAP_SHORT)) * nights / rate
            st["cash"] -= sw
            P["swap"] = P.get("swap", 0.0) + sw
            P["swapDay"] = day
        # uscite
        if P and P["day"] != day:
            why = raw_fill = None
            if P["dir"] > 0:
                lx = I["x"][1][i]
                trig = max(P["stop"], lx) if lx is not None else P["stop"]
                if l <= trig:
                    raw_fill = o if o <= trig else trig - slip
                    why = "Stop 2N" if trig == P["stop"] else f"Minimo {EXIT} gg"
            else:
                hx = I["x"][0][i]
                trig = min(P["stop"], hx) if hx is not None else P["stop"]
                if h >= trig:
                    raw_fill = o if o >= trig else trig + slip
                    why = "Stop 2N" if trig == P["stop"] else f"Massimo {EXIT} gg"
            if why:
                fill = raw_fill - P["dir"] * hs
                pnl_usd = P["dir"] * P["lots"] * cu * (fill - P["entry"])
                pnl_eur = pnl_usd / rate
                fee = abs(pnl_eur) * FX_FEE
                st["cash"] += pnl_eur - fee
                net = pnl_eur - fee - P.get("swap", 0.0)
                spread_eur = P["lots"] * cu * 2 * hs / rate
                st["trades"].append({
                    "day": day, "k": k, "kind": "close", "dir": P["dir"], "why": why,
                    "act": f"Chiude {'long' if P['dir'] > 0 else 'short'} · {why}",
                    "px": round(fill, 4), "pnl": round(net, 2), "lots": P["lots"],
                    "notional": P["notional"], "margin": P["margin"], "entryDay": P["day"],
                    "entryTime": P.get("time"), "entryPx": P["entry"],
                    "costs": {"spread": round(spread_eur, 2), "swap": round(P.get("swap", 0.0), 2), "fx": round(fee, 2)},
                    "ret": round(net / P["margin"], 4) if P["margin"] else 0})
                del st["positions"][k]
                print(f"  {day} CHIUDE {name} a {fill:.4f} ({why}) netto {net:+.2f} €")
        # entrate
        if k not in st["positions"] and Nk and I["e"][0][i] is not None:
            up, dn = I["e"][0][i], I["e"][1][i]
            lh, sh = h > up, l < dn
            direction = (1 if c >= (up + dn) / 2 else -1) if (lh and sh) else 1 if lh else -1 if sh else 0
            if direction:
                if direction > 0:
                    raw_fill = o if o > up else up + slip
                else:
                    raw_fill = o if o < dn else dn - slip
                fill = raw_fill + direction * hs
                st["lastpx"].setdefault(k, fill)
                st["lastfx"] = rate
                eq = st["cash"] + unreal_eur(st, ind)
                lots_r = eq * RISK * rate / (2 * Nk * cu)
                lots_c = eq * rate * CAPX / (cu * fill)
                lots = math.floor(min(lots_r, lots_c) / spec["step"] + 1e-9) * spec["step"]
                reason = None
                if lots < spec["minlot"]:
                    risk_min = spec["minlot"] * 2 * Nk * cu / rate
                    if risk_min <= eq * MAX_RISK:
                        lots = spec["minlot"]
                    else:
                        reason = f"Lotto minimo XTB ({spec['minlot']}) rischierebbe {risk_min:.0f} € ({risk_min / eq * 100:.0f}% del capitale)"
                if not reason:
                    margin = lots * cu * fill / spec["lev"] / rate
                    if used_margin(st, ind) + margin > eq:
                        reason = f"Margine insufficiente: servono {margin:.0f} € in più"
                if reason or eq <= 0:
                    st["skipped"].append({"day": day, "k": k, "dir": direction, "px": round(fill, 4), "why": reason or "Capitale esaurito"})
                    print(f"  {day} SALTA {name} {'LONG' if direction > 0 else 'SHORT'}: {reason}")
                else:
                    notional = lots * cu * fill / rate
                    st["positions"][k] = {"dir": direction, "lots": round(lots, 4), "entry": round(fill, 4),
                                          "stop": round(raw_fill - direction * 2 * Nk, 4), "day": day,
                                          "swapDay": day, "swap": 0.0, "notional": round(notional, 2),
                                          "margin": round(margin, 2)}
                    st["trades"].append({"day": day, "k": k, "kind": "open", "dir": direction,
                                         "px": round(fill, 4), "pnl": None, "lots": round(lots, 4),
                                         "notional": round(notional, 2), "margin": round(margin, 2),
                                         "act": "Compra (long)" if direction > 0 else "Vende allo scoperto (short)"})
                    print(f"  {day} APRE {name} {'LONG' if direction > 0 else 'SHORT'} {lots} lotti a {fill:.4f}, "
                          f"valore {notional:.0f} €, margine {margin:.0f} €")
        st["lastpx"][k] = c
    st["lastfx"] = rate
    st["equity"].append([day, round(st["cash"] + unreal_eur(st, ind), 2)])
    st["last"] = day


# ---------- orari esatti (ora di Praga) ----------
# Il robot scarica i prezzi a 5 minuti (Yahoo li tiene circa 60 giorni) e cerca la prima barra
# della seduta in cui il prezzo ha toccato il livello. Una seduta dei futures USA va dalle 18:00
# (New York) del giorno prima alle 17:00 del giorno stesso.
INTRADAY_DAYS = 55
_intraday = {}


def _bars(k):
    if k not in _intraday:
        try:
            import yfinance as yf
            df = yf.Ticker(k.upper() + "=F").history(period="60d", interval="5m", auto_adjust=False)
            df = df[["Open", "High", "Low"]].dropna()
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            _intraday[k] = df.tz_convert("America/New_York")
        except Exception as e:
            print(f"  orari {k}: non disponibili ({e})")
            _intraday[k] = None
    return _intraday[k]


def find_time(k, day, px, touch_up):
    """Ora di Praga (YYYY-MM-DD HH:MM) della prima barra della seduta `day` che tocca `px`."""
    df = _bars(k)
    if df is None or df.empty:
        return None
    sess = (df.index + datetime.timedelta(hours=6)).strftime("%Y-%m-%d")
    d = df[sess == day]
    if d.empty:
        return None
    tol = abs(px) * 5e-4
    hit = d[d["High"] >= px - tol] if touch_up else d[d["Low"] <= px + tol]
    if hit.empty:
        return None
    return hit.index[0].tz_convert("Europe/Prague").strftime("%Y-%m-%d %H:%M")


def annotate_times(st, ind):
    limit = (datetime.date.today() - datetime.timedelta(days=INTRADAY_DAYS)).isoformat()
    hs = lambda k: ind[k]["hs"] if k in ind else 0
    # i prezzi salvati includono meta' spread: per cercare il tocco si usa il prezzo di mercato
    for t in st["trades"]:
        if t["day"] < limit:
            continue
        if t["kind"] == "open" and not t.get("time"):
            t["time"] = find_time(t["k"], t["day"], t["px"] - t["dir"] * hs(t["k"]), t["dir"] > 0)
        if t["kind"] == "close" and not t.get("time"):
            t["time"] = find_time(t["k"], t["day"], t["px"] + t["dir"] * hs(t["k"]), t["dir"] < 0)
    open_time = {(t["k"], t["day"]): t.get("time") for t in st["trades"] if t["kind"] == "open"}
    for t in st["trades"]:
        if t["kind"] == "close" and not t.get("entryTime"):
            t["entryTime"] = open_time.get((t["k"], t["entryDay"]))
    for k, P in st["positions"].items():
        if not P.get("time"):
            P["time"] = open_time.get((k, P["day"]))


def run_robot(raw, fx):
    ind = {k: indicators(v) for k, v in raw.items()}
    fxs = FX(fx)
    # Una seduta e' chiusa dopo le 21:00 UTC; quella in corso si simula solo come provvisoria.
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.strftime("%Y-%m-%d")
    cutoff = today if now.hour >= 21 else (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    all_days = sorted({x for v in raw.values() for x in v[0]})
    days = [x for x in all_days if x <= cutoff]
    if STATE.exists():
        st = json.loads(STATE.read_text())
    else:
        st = new_state(today)
        print(f"Nuovo portafoglio da {CAPITAL:.0f} € a partire dalla seduta del {today}")
    todo = [x for x in days if x >= st["start"] and (st["last"] is None or x > st["last"])]
    for day in todo:
        step_day(st, day, raw, ind, fxs)
    if not todo:
        print("Nessuna seduta chiusa nuova da elaborare.")
    annotate_times(st, ind)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    # seduta in corso: simulazione provvisoria, non salvata
    view = st
    prov = [x for x in all_days if x > cutoff and x >= st["start"]]
    if prov:
        view = copy.deepcopy(st)
        print(f"Seduta in corso {prov[-1]} (provvisoria):")
        step_day(view, prov[-1], raw, ind, fxs)
        annotate_times(view, ind)
        view["prov"] = prov[-1]
    return st, view


# ---------- dashboard ----------
def build(raw, fx, view):
    last = max(v[0][-1] for v in raw.values())
    now = datetime.datetime.now(datetime.timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo("Europe/Prague"))
    source = ("Prezzi reali dei futures continui da Yahoo Finance (ritardo circa 15 minuti), aggiornati il "
              f"{now:%d/%m/%Y alle %H:%M} ora di Praga (ultima seduta {last}). Condizioni CFD indicative XTB: "
              "lotti minimi, spread, swap notturno, 0,5% sul cambio, leva ESMA.")
    live = {k: view.get(k) for k in ("start", "capital", "cash", "positions", "trades", "skipped",
                                     "equity", "last", "lastpx", "lastfx", "prov")}
    cfg = {"risk": RISK, "maxRisk": MAX_RISK, "slipN": SLIP_N, "swapL": SWAP_LONG, "swapS": SWAP_SHORT,
           "fxFee": FX_FEE, "startFx": START_FX, "capx": CAPX}
    html = (TEMPLATE.read_text(encoding="utf-8")
            .replace("__DATA__", json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
            .replace("__FX__", json.dumps(fx, separators=(",", ":")))
            .replace("__CFG__", json.dumps(cfg, separators=(",", ":")))
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
    raw, fx = download()
    if not raw:
        sys.exit("Nessun dato scaricato.")
    st, view = run_robot(raw, fx)
    eq = view["equity"][-1][1] if view["equity"] else CAPITAL
    print(f"Capitale: {eq:.2f} €  ({(eq / CAPITAL - 1) * 100:+.1f}%)  posizioni aperte: {len(view['positions'])}"
          + (f"  [provvisorio {view['prov']}]" if view.get("prov") else ""))
    build(raw, fx, view)


if __name__ == "__main__":
    main()
