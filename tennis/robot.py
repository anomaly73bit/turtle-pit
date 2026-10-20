#!/usr/bin/env python3
"""
Robot tennis Unibet: ogni 30 minuti (GitHub Actions)
  1. aggiorna lo stato dei giocatori con i risultati ATP/WTA appena conclusi (ESPN);
  2. legge le quote Unibet di tutte le partite ATP/WTA di singolare non ancora iniziate (Kambi);
  3. calcola per ogni partita la probabilita' del modello e il valore della quota;
  4. registra come scommessa finta (1 unita') ogni partita con valore >= SOGLIA e la chiude
     quando arriva il risultato;
  5. rigenera la pagina docs/tennis/index.html.
Nessuna scommessa vera viene piazzata.
"""
import datetime, html, json, os, pathlib, sys, unicodedata, urllib.request, zoneinfo
from tennis_model import State, Names, predict_b, as_date, norm
import sharp

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "docs" / "tennis" / "index.html"
STATE, MODEL, BETS, BT = HERE / "state.json", HERE / "model.json", HERE / "bets.json", HERE / "backtest.json"
SHARP_FILE = HERE / "sharp.json"
SOGLIA = 0.03          # valore minimo: (probabilita' giusta dalle quote sharp x quota Unibet) - 1
SHARP_MAX_AGE_H = 8    # oltre questa eta' le quote sharp non si usano
MIN_MATCHES = 10       # partite minime nei dati per entrambi i giocatori
ODDS_RANGE = (1.20, 6.0)
PRAGUE = zoneinfo.ZoneInfo("Europe/Prague")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0", "Accept": "application/json"}
KAMBI = ("https://eu-offering-api.kambicdn.com/offering/v2018/ub/listView/tennis/all/all/all/matches.json"
         "?lang=en_GB&market=GB&useCombined=true")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={d}"


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.loads(r.read())


def surface_of(st, month, *names):
    """Superficie del torneo nello stesso periodo dell'anno (stesso mese o quello vicino); altrimenti cemento."""
    for n in names:
        n = (n or "").lower()
        for key in (n, n.split(",")[0].strip()):
            months = st.surfaces.get(key)
            if not months:
                continue
            for dm in (0, -1, 1):
                m = str((month - 1 + dm) % 12 + 1)
                if m in months:
                    return months[m]
    return "Hard"


def short_key(full, short):
    """'F. Cina' -> 'Cina F.' (formato dei dati storici) per i giocatori nuovi."""
    if short and ". " in short:
        ini, sur = short.split(". ", 1)
        return f"{sur} {ini[0]}."
    parts = full.split()
    return f"{' '.join(parts[1:])} {parts[0][0]}." if len(parts) > 1 else full


# ---------- 1. risultati ESPN ----------
def update_results(st, names):
    done = set(st.espn_done)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    results, new = [], 0
    for tour in ("atp", "wta"):
        for d in (today - datetime.timedelta(days=1), today):
            try:
                data = get(ESPN.format(tour=tour, d=d.strftime("%Y%m%d")))
            except Exception as e:
                print(f"  ESPN {tour} {d}: {e}")
                continue
            for ev in data.get("events", []):
                for g in ev.get("groupings", []):
                    for c in g.get("competitions", []):
                        slug = c.get("type", {}).get("slug", "")
                        rnd = (c.get("round") or {}).get("displayName", "")
                        status = c.get("status", {}).get("type", {}).get("name", "")
                        if not slug.endswith("singles") or rnd.startswith("Qualifying"):
                            continue
                        if status not in ("STATUS_FINAL", "STATUS_RETIRED"):
                            continue
                        comp = c.get("competitors", [])
                        if len(comp) != 2 or not any(x.get("winner") for x in comp):
                            continue
                        T = tour.upper()
                        w = next(x for x in comp if x.get("winner"))
                        l = next(x for x in comp if not x.get("winner"))
                        def key(x):
                            a = x.get("athlete", {})
                            return names.find(T, a.get("displayName", "")) or short_key(a.get("displayName", ""), a.get("shortName", ""))
                        wk, lk = key(w), key(l)
                        day = c.get("date", "")[:10]
                        results.append({"id": c.get("id"), "tour": T, "day": day, "w": wk, "l": lk,
                                        "retired": status == "STATUS_RETIRED"})
                        if c.get("id") in done or day <= st.base_date:
                            continue
                        surf = surface_of(st, int(day[5:7]), (c.get("venue") or {}).get("fullName"), ev.get("name"))
                        st.update(T, wk, lk, surf, day)
                        done.add(c.get("id"))
                        new += 1
    st.espn_done = sorted(done)[-5000:]
    print(f"Risultati nuovi elaborati: {new}")
    return results


# ---------- 2-3. quote Unibet e modello ----------
def scan(st, names, model):
    data = get(KAMBI)
    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    for e in data.get("events", []):
        ev = e.get("event", {})
        path = [p.get("englishName") or p.get("name") for p in ev.get("path", [])]
        if len(path) < 2 or path[1] not in ("ATP", "WTA") or ev.get("state") != "NOT_STARTED":
            continue
        if "/" in ev.get("homeName", "") or "/" in ev.get("awayName", ""):
            continue
        bo = next((b for b in e.get("betOffers", []) if (b.get("criterion", {}).get("englishLabel") == "Match Odds")
                   or "MAIN" in b.get("tags", [])), None)
        if not bo or len(bo.get("outcomes", [])) != 2:
            continue
        o1, o2 = bo["outcomes"]
        if o1.get("status") != "OPEN" or o2.get("status") != "OPEN":
            continue
        q1, q2 = o1["odds"] / 1000, o2["odds"] / 1000
        T, tourn = path[1], path[2] if len(path) > 2 else ev.get("group", "")
        n1, n2 = o1.get("participant") or o1.get("label"), o2.get("participant") or o2.get("label")
        k1, k2 = names.find(T, n1), names.find(T, n2)
        start = ev.get("start")
        surf = surface_of(st, int(start[5:7]), tourn, ev.get("group"))
        imp1 = (1 / q1) / (1 / q1 + 1 / q2)
        row = {"id": ev.get("id"), "start": start, "tour": T, "tournament": tourn, "surface": surf,
               "p1": n1, "p2": n2, "k1": k1, "k2": k2, "q1": q1, "q2": q2, "imp1": imp1,
               "margin": 1 / q1 + 1 / q2 - 1}
        if k1 and k2:
            day = datetime.datetime.fromisoformat(start.replace("Z", "+00:00")).date()
            f = st.features(T, k1, k2, surf, day, False)
            if f["nmin"] >= MIN_MATCHES:
                pm = predict_b(model, f, imp1)
                row.update(pm1=pm, pelo1=f["p_mix"], v1=pm * q1 - 1, v2=(1 - pm) * q2 - 1,
                           r1=st.rank.get(f"{T}|{k1}"), r2=st.rank.get(f"{T}|{k2}"),
                           f1=form_str(st, T, k1), f2=form_str(st, T, k2),
                           h2h=[st.h2h[f"{T}|{k1}|{k2}"], st.h2h[f"{T}|{k2}|{k1}"]])
            else:
                row["note"] = "Pochi dati su uno dei giocatori"
        else:
            row["note"] = "Giocatore non trovato nei dati"
        out.append(row)
    out.sort(key=lambda r: -max(r.get("v1", -9), r.get("v2", -9)))
    print(f"Partite ATP/WTA su Unibet non iniziate: {len(out)}, con modello: {sum('pm1' in r for r in out)}")
    return out


def form_str(st, T, k):
    h = st.hist.get(f"{T}|{k}", [])[-10:]
    return "".join("V" if w else "S" for _, w in h)


# ---------- quote sharp ----------
def pkey(full):
    t = norm(full)
    return f"{t[-1]}|{t[0][0]}" if len(t) > 1 else (t[0] if t else "")


def sharp_update():
    """Scarica le quote sharp quando serve, rispettando le 500 richieste gratuite al mese."""
    snap = json.loads(SHARP_FILE.read_text()) if SHARP_FILE.exists() else {}
    if not os.environ.get("ODDS_API_KEY"):
        print("Quote sharp: chiave ODDS_API_KEY assente")
        return snap
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        keys = sharp.tennis_keys()
    except Exception as e:
        print("Quote sharp: elenco tornei non disponibile:", e)
        return snap
    last = datetime.datetime.fromisoformat(snap["time"]) if snap.get("time") else None
    same_month = snap.get("month") == now.strftime("%Y-%m")
    remaining = int(snap.get("remaining") or 500) if same_month else 500
    days_left = (datetime.date(now.year + now.month // 12, now.month % 12 + 1, 1) - now.date()).days or 1
    per_snap = max(1, len(keys))
    interval_h = max(3.0, 24 * per_snap * days_left / max(remaining - 10, 1))
    due = last is None or (now - last).total_seconds() / 3600 >= interval_h or os.environ.get("FORCE_SHARP") == "1"
    print(f"Quote sharp: {len(keys)} tornei attivi, richieste rimaste {remaining}, intervallo {interval_h:.1f} h, "
          f"{'scarico adesso' if due else 'uso le quote delle ' + last.astimezone(PRAGUE).strftime('%H:%M')}")
    if not due or remaining - per_snap < 5:
        return snap
    events, rem = sharp.fetch(keys, budget_left_min=5)
    out = []
    for ev in events:
        p, used = sharp.fair(ev)
        if p is None:
            continue
        out.append({"home": ev["home_team"], "away": ev["away_team"], "start": ev["commence_time"],
                    "key": ev["sport_key"], "p_home": round(p, 4), "src": used})
    snap = {"time": now.isoformat(timespec="minutes"), "month": now.strftime("%Y-%m"),
            "remaining": int(rem) if rem is not None else remaining, "events": out}
    SHARP_FILE.write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    print(f"Quote sharp salvate: {len(out)} partite con Pinnacle/Betfair")
    return snap


def attach_sharp(matches, snap):
    if not snap.get("time"):
        return
    age = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(snap["time"])).total_seconds() / 3600
    if age > SHARP_MAX_AGE_H:
        return
    idx = {}
    for e in snap.get("events", []):
        idx.setdefault(frozenset((pkey(e["home"]), pkey(e["away"]))), []).append(e)
    n = 0
    for m in matches:
        cands = idx.get(frozenset((pkey(m["p1"]), pkey(m["p2"]))), [])
        t0 = datetime.datetime.fromisoformat(m["start"].replace("Z", "+00:00"))
        for e in cands:
            if abs((datetime.datetime.fromisoformat(e["start"].replace("Z", "+00:00")) - t0).total_seconds()) > 6 * 3600:
                continue
            p1 = e["p_home"] if pkey(e["home"]) == pkey(m["p1"]) else 1 - e["p_home"]
            m.update(fair1=p1, src=e["src"], sv1=p1 * m["q1"] - 1, sv2=(1 - p1) * m["q2"] - 1, sharp_age=age)
            n += 1
            break
    print(f"Partite Unibet abbinate alle quote sharp: {n}")


# ---------- 4. scommesse finte ----------
def paper(bets, matches, results):
    known = {b["id"] for b in bets}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes")
    for m in matches:
        if "fair1" not in m or m["id"] in known:
            continue
        for side in (1, 2):
            v, q = m[f"sv{side}"], m[f"q{side}"]
            if v >= SOGLIA and ODDS_RANGE[0] <= q <= ODDS_RANGE[1]:
                k1 = m["k1"] or short_key(m["p1"], "")
                k2 = m["k2"] or short_key(m["p2"], "")
                pick, opp = (k1, k2) if side == 1 else (k2, k1)
                bets.append({"id": m["id"], "placed": now, "start": m["start"], "tour": m["tour"],
                             "tournament": m["tournament"], "pick": m[f"p{side}"], "opp": m[f"p{3 - side}"],
                             "kpick": pick, "kopp": opp, "odds": q, "value": round(v, 4),
                             "pmodel": round(m["fair1"] if side == 1 else 1 - m["fair1"], 4), "src": m["src"],
                             "pbook": round(m["imp1"] if side == 1 else 1 - m["imp1"], 4), "status": "aperta"})
                print(f"  SCOMMESSA FINTA: {m[f'p{side}']} a {q} (valore {v:+.1%})")
                break
    today = datetime.datetime.now(datetime.timezone.utc).date()
    for b in bets:
        if b["status"] != "aperta":
            continue
        d0 = as_date(b["start"])
        for r in results:
            if r["tour"] != b["tour"] or abs((as_date(r["day"]) - d0).days) > 2:
                continue
            if {r["w"], r["l"]} == {b["kpick"], b["kopp"]}:
                if r["retired"]:
                    b.update(status="annullata", pnl=0.0, note="ritiro")
                else:
                    won = r["w"] == b["kpick"]
                    b.update(status="vinta" if won else "persa", pnl=round(b["odds"] - 1 if won else -1.0, 3))
                b["settled"] = r["day"]
                break
        if b["status"] == "aperta" and (today - d0).days > 5:
            b.update(status="annullata", pnl=0.0, note="risultato non trovato")
    return bets


# ---------- 5. pagina ----------
BANK0 = 200.0   # conto simulato iniziale in euro
STAKE = 0.02    # puntata: 2% del conto del momento


def bankroll(items):
    """items: [(data, quota, esito)] in ordine; esito 1 vinta, 0 persa, None aperta/annullata.
    Ritorna serie del conto, puntate, conto finale."""
    bank, curve, stakes = BANK0, [], []
    for d, q, res in items:
        st = round(bank * STAKE, 2)
        stakes.append(st)
        if res == 1:
            bank += st * (q - 1)
        elif res == 0:
            bank -= st
        if res is not None:
            curve.append((d, round(bank, 2)))
    return curve, stakes, bank


def spark(curve, w=640, h=150):
    """Grafico SVG del conto, con la linea di partenza a 200 €."""
    if len(curve) < 2:
        return '<div class="flat">Il grafico comparirà dopo le prime partite concluse.</div>'
    vals = [BANK0] + [v for _, v in curve]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or 10
    lo, hi = lo - pad, hi + pad
    X = lambda i: 8 + (w - 16) * i / (len(vals) - 1)
    Y = lambda v: 8 + (h - 30) * (1 - (v - lo) / (hi - lo))
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
    area = f"8,{h - 22} " + pts + f" {X(len(vals) - 1):.1f},{h - 22}"
    y0 = Y(BANK0)
    first, last = curve[0][0], curve[-1][0]
    fd = lambda d: f"{d[8:10]}/{d[5:7]}/{d[2:4]}"
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Andamento del conto">'
            f'<line x1="8" x2="{w - 8}" y1="{y0:.1f}" y2="{y0:.1f}" class="base"/>'
            f'<text x="{w - 8}" y="{y0 - 5:.1f}" text-anchor="end" class="ax">200 €</text>'
            f'<polygon points="{area}" class="area"/><polyline points="{pts}" class="ln"/>'
            f'<circle cx="{X(len(vals) - 1):.1f}" cy="{Y(vals[-1]):.1f}" r="4.5" class="dot"/>'
            f'<text x="8" y="{h - 6}" class="ax">{fd(first)}</text><text x="{w - 8}" y="{h - 6}" text-anchor="end" class="ax">{fd(last)}</text></svg>')


def build_page(matches, bets, bt, model):
    now = datetime.datetime.now(PRAGUE)
    esc = html.escape
    def t(iso):
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PRAGUE)
        day = "oggi" if dt.date() == now.date() else "domani" if dt.date() == now.date() + datetime.timedelta(days=1) else dt.strftime("%d/%m")
        return f"{day} {dt.strftime('%H:%M')}"
    pct = lambda x: f"{x * 100:.0f}%"
    num = lambda x, d=2: f"{x:,.{d}f}".replace(",", " ").replace(".", ",")
    eur = lambda x: ("−" if x < 0 else "") + num(abs(x)) + " €"
    surname = lambda n: n.split()[-1] if n else n

    # --- da giocare ---
    picks = []
    for m in matches:
        if "fair1" not in m:
            continue
        side = 1 if m["sv1"] >= m["sv2"] else 2
        v, q = m[f"sv{side}"], m[f"q{side}"]
        if v >= SOGLIA and ODDS_RANGE[0] <= q <= ODDS_RANGE[1]:
            picks.append((v, m, side, q, m["fair1"] if side == 1 else 1 - m["fair1"]))
    picks.sort(key=lambda x: -x[0])
    tips = "".join(
        f'<li class="tip"><span class="no">{i}</span>'
        f'<div class="who"><span class="pn">{esc(m[f"p{side}"])}</span>'
        f'<span class="vs">vs {esc(m[f"p{3 - side}"])}</span>'
        f'<span class="tn">{esc(m["tournament"])} · {t(m["start"])}</span></div>'
        f'<div class="fig"><span class="big">{pct(p)}</span><span class="cap">vittoria</span></div>'
        f'<div class="fig"><span class="big q">{num(q)}</span><span class="cap">quota</span></div></li>'
        for i, (v, m, side, q, p) in enumerate(picks, 1))
    if not tips:
        tips = '<li class="nothing">Nessuna partita da giocare adesso. Si aggiorna ogni 30 minuti.</li>'

    # --- tutte le partite ---
    prob = lambda m: m.get("fair1", m.get("pm1"))
    allrows = "".join(
        f'<tr><td class="mono">{t(m["start"])}</td><td>{esc(m["p1"])}<br>{esc(m["p2"])}</td>'
        f'<td class="r mono">{num(m["q1"])}<br>{num(m["q2"])}</td>'
        f'<td class="r mono">{pct(prob(m)) if prob(m) is not None else "—"}<br>{pct(1 - prob(m)) if prob(m) is not None else "—"}</td></tr>'
        for m in sorted(matches, key=lambda m: m["start"]))

    # --- simulazione dal vivo ---
    live = sorted([b for b in bets if b["status"] != "annullata"], key=lambda b: b["start"])
    items = [(b["start"][:10], b["odds"], 1 if b["status"] == "vinta" else 0 if b["status"] == "persa" else None) for b in live]
    lcurve, lstakes, lbank = bankroll(items)
    lrows = []
    for b, st in sorted(zip(live, lstakes), key=lambda x: x[0]["start"], reverse=True)[:40]:
        if b["status"] == "aperta":
            res, cls = "in attesa", "wait"
        elif b["status"] == "vinta":
            res, cls = "+" + eur(st * (b["odds"] - 1)), "win"
        else:
            res, cls = "−" + eur(st), "lose"
        lrows.append(f'<tr><td class="mono">{t(b["start"])}</td><td><b>{esc(b["pick"])}</b> <span class="dim">vs {esc(b["opp"])}</span></td>'
                     f'<td class="r mono">{num(b["odds"])}</td><td class="r mono">{eur(st)}</td><td class="r mono {cls}">{res}</td></tr>')
    lclosed = [b for b in live if b["status"] in ("vinta", "persa")]
    lwon = sum(b["status"] == "vinta" for b in lclosed)

    # --- simulazione sul passato ---
    pb = bt.get("bets_sharp", bt.get("bets", []))
    pcurve, pstakes, pbank = bankroll([(r[0], r[5], r[7]) for r in pb])
    prows = "".join(
        f'<tr><td class="mono">{r[0][8:10]}/{r[0][5:7]}/{r[0][2:4]}</td><td><b>{esc(r[3])}</b> <span class="dim">vs {esc(r[4])}</span></td>'
        f'<td class="r mono">{num(r[5])}</td><td class="r mono">{eur(st)}</td>'
        f'<td class="r mono {"win" if r[7] else "lose"}">{("+" + eur(st * (r[5] - 1))) if r[7] else ("−" + eur(st))}</td></tr>'
        for r, st in list(zip(pb, pstakes))[::-1][:30])
    pwon = sum(r[7] for r in pb)

    def head(bank, n, won, pend=None):
        ch = bank - BANK0
        extra = f'<div><span class="k">In attesa</span><span class="v">{pend}</span></div>' if pend is not None else ""
        return (f'<div class="bank"><div><span class="k">Conto</span><span class="bal">{eur(bank)}</span>'
                f'<span class="chg {"win" if ch >= 0 else "lose"}">{"+" if ch >= 0 else "−"}{eur(abs(ch))} · {"+" if ch >= 0 else "−"}{abs(ch) / BANK0 * 100:.1f}%</span></div>'
                f'<div class="stats"><div><span class="k">Giocate</span><span class="v">{n}</span></div>'
                f'<div><span class="k">Vinte</span><span class="v">{won}{f" · {won / n * 100:.0f}%" if n else ""}</span></div>{extra}</div></div>')

    vals = {
        "updated": now.strftime("%d/%m %H:%M"), "ntips": str(len(picks)), "tips": tips,
        "nall": str(len(matches)), "allrows": allrows or '<tr><td colspan="4" class="dim">Nessuna partita in programma.</td></tr>',
        "livehead": head(lbank, len(lclosed), lwon, len(live) - len(lclosed)), "livechart": spark(lcurve),
        "liverows": "".join(lrows) or '<tr><td colspan="5" class="dim">Le prime giocate compariranno qui.</td></tr>',
        "livestart": (live[0]["start"][8:10] + "/" + live[0]["start"][5:7]) if live else now.strftime("%d/%m"),
        "pasthead": head(pbank, len(pb), pwon), "pastchart": spark(pcurve), "pastrows": prows,
        "pastrange": (f'{pb[0][0][8:10]}/{pb[0][0][5:7]}/{pb[0][0][:4]} – {pb[-1][0][8:10]}/{pb[-1][0][5:7]}/{pb[-1][0][:4]}' if pb else ""),
    }
    page = TEMPLATE
    for k, v in vals.items():
        page = page.replace("%%" + k + "%%", v)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")


TEMPLATE = (HERE / "page.html").read_text(encoding="utf-8") if (HERE / "page.html").exists() else ""


def main():
    st_raw = json.loads(STATE.read_text())
    st = State.from_json(st_raw)
    st.surfaces = st_raw.get("surfaces", {})
    st.espn_done = st_raw.get("espn_done", [])
    st.base_date = st_raw.get("base_date") or st_raw["last_date"]
    model = json.loads(MODEL.read_text())
    bt = json.loads(BT.read_text())
    bets = json.loads(BETS.read_text()) if BETS.exists() else []
    names = Names(list(st.elo.keys()))
    results = update_results(st, names)
    names = Names(list(st.elo.keys()))
    matches = scan(st, names, model)
    attach_sharp(matches, sharp_update())
    bets = paper(bets, matches, results)
    out = st.to_json()
    out.update(surfaces=st.surfaces, espn_done=st.espn_done, base_date=st.base_date)
    STATE.write_text(json.dumps(out, separators=(",", ":")))
    BETS.write_text(json.dumps(bets, ensure_ascii=False, indent=1))
    build_page(matches, bets, bt, model)
    print("Pagina aggiornata:", OUT)


if __name__ == "__main__":
    main()
