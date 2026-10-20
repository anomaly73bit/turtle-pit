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
import datetime, html, json, pathlib, sys, urllib.request, zoneinfo
from tennis_model import State, Names, predict_b, as_date

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "docs" / "tennis" / "index.html"
STATE, MODEL, BETS, BT = HERE / "state.json", HERE / "model.json", HERE / "bets.json", HERE / "backtest.json"
SOGLIA = 0.05          # valore minimo per la scommessa finta: (prob. modello x quota) - 1
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


# ---------- 4. scommesse finte ----------
def paper(bets, matches, results):
    known = {b["id"] for b in bets}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes")
    for m in matches:
        if "pm1" not in m or m["id"] in known:
            continue
        for side in (1, 2):
            v, q = m[f"v{side}"], m[f"q{side}"]
            if v >= SOGLIA and ODDS_RANGE[0] <= q <= ODDS_RANGE[1]:
                pick, opp = (m["k1"], m["k2"]) if side == 1 else (m["k2"], m["k1"])
                bets.append({"id": m["id"], "placed": now, "start": m["start"], "tour": m["tour"],
                             "tournament": m["tournament"], "pick": m[f"p{side}"], "opp": m[f"p{3 - side}"],
                             "kpick": pick, "kopp": opp, "odds": q, "value": round(v, 4),
                             "pmodel": round(m["pm1"] if side == 1 else 1 - m["pm1"], 4),
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
def pnl_txt(b):
    return "" if b["status"] == "aperta" else ("%+.2f" % b.get("pnl", 0)).replace(".", ",")


def build_page(matches, bets, bt, model):
    now = datetime.datetime.now(PRAGUE)
    esc = html.escape
    def t(iso):
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(PRAGUE).strftime("%d/%m %H:%M")
    def pct(x, sign=False):
        return (f"{x * 100:+.1f}%" if sign else f"{x * 100:.0f}%").replace(".", ",")
    def odds(x):
        return f"{x:.2f}".replace(".", ",")
    rows = []
    for m in matches:
        if "pm1" in m:
            best = 1 if m["v1"] >= m["v2"] else 2
            v = m[f"v{best}"]
            tag = "val" if v >= SOGLIA else ("near" if v >= 0 else "neg")
            def cell(side):
                p = m["pm1"] if side == 1 else 1 - m["pm1"]
                ib = m["imp1"] if side == 1 else 1 - m["imp1"]
                vv = m[f"v{side}"]
                fair = 1 / p
                r = m[f"r{side}"]
                return (f'<div class="pl{" pick" if side == best and tag == "val" else ""}"><span class="nm">{esc(m[f"p{side}"])}</span>'
                        f'<span class="meta">#{int(r) if r else "—"} · forma {esc(m[f"f{side}"]) or "—"}</span></div>'
                        f'<div class="num q">{odds(m[f"q{side}"])}</div><div class="num">{pct(ib)}</div>'
                        f'<div class="num">{pct(p)}</div><div class="num">{odds(fair)}</div>'
                        f'<div class="num {"pos" if vv > 0 else "neg"}">{pct(vv, True)}</div>')
            badge = {"val": '<span class="chip val">Valore</span>', "near": '<span class="chip near">Quasi</span>',
                     "neg": '<span class="chip neg">Nessun valore</span>'}[tag]
            rows.append(f'<article class="match {tag}"><header><span class="when">{t(m["start"])}</span>'
                        f'<span class="tn">{esc(m["tour"])} · {esc(m["tournament"])} · {esc(m["surface"])}</span>{badge}'
                        f'<span class="h2h">Scontri diretti {m["h2h"][0]}–{m["h2h"][1]}</span></header>'
                        f'<div class="grid"><div class="hd">Giocatore</div><div class="hd r">Quota</div><div class="hd r">Unibet</div>'
                        f'<div class="hd r">Modello</div><div class="hd r">Giusta</div><div class="hd r">Valore</div>'
                        f'{cell(1)}{cell(2)}</div></article>')
        else:
            rows.append(f'<article class="match off"><header><span class="when">{t(m["start"])}</span>'
                        f'<span class="tn">{esc(m["tour"])} · {esc(m["tournament"])}</span><span class="chip neg">{esc(m.get("note", ""))}</span></header>'
                        f'<p class="offline">{esc(m["p1"])} {odds(m["q1"])} — {esc(m["p2"])} {odds(m["q2"])}</p></article>')
    closed = [b for b in bets if b["status"] in ("vinta", "persa")]
    n, pnl = len(closed), sum(b["pnl"] for b in closed)
    won = sum(1 for b in closed if b["status"] == "vinta")
    roi = pnl / n if n else 0
    open_b = [b for b in bets if b["status"] == "aperta"]
    brows = "".join(
        f'<tr><td>{t(b["start"])}</td><td>{esc(b["tour"])} · {esc(b["tournament"])}</td><td><b>{esc(b["pick"])}</b><br><span class="meta">contro {esc(b["opp"])}</span></td>'
        f'<td class="r">{odds(b["odds"])}</td><td class="r">{pct(b["value"], True)}</td>'
        f'<td><span class="chip {"val" if b["status"] == "vinta" else "neg" if b["status"] == "persa" else "near"}">{esc(b["status"])}</span></td>'
        f'<td class="r {"pos" if b.get("pnl", 0) > 0 else "neg" if b.get("pnl", 0) < 0 else ""}">{pnl_txt(b)}</td></tr>'
        for b in sorted(bets, key=lambda b: b["start"], reverse=True)[:150])
    btrows = "".join(f'<tr><td>valore ≥ {pct(r["thr"])}</td><td class="r">{r["bets"]}</td>'
                     f'<td class="r {"pos" if r["roi"] > 0 else "neg"}">{pct(r["roi"], True)}</td><td class="r">±{pct(r["se"])}</td></tr>'
                     for r in bt["rows"])
    nval = sum(1 for m in matches if "pm1" in m and max(m["v1"], m["v2"]) >= SOGLIA)
    nmod = sum(1 for m in matches if "pm1" in m)
    vals = dict(
        updated=now.strftime("%d/%m/%Y alle %H:%M"), nmatch=len(matches), nmod=nmod, nval=nval,
        rows="".join(rows) or '<p class="empty">Nessuna partita ATP o WTA non iniziata su Unibet in questo momento.</p>',
        n=n, won=won, open=len(open_b), pnl=f"{pnl:+.2f}".replace(".", ","), roi=pct(roi, True) if n else "—",
        roicls="pos" if pnl > 0 else "neg" if pnl < 0 else "",
        brows=brows or '<tr><td colspan="7" class="empty">Ancora nessuna scommessa finta: il robot ne registra una quando trova valore ≥ 5%.</td></tr>',
        btrows=btrows, bttrain=f'{bt["train"][0]} – {bt["train"][1]}', bttest=f'{bt["test"][0]} – {bt["test"][1]}',
        btn=bt["test"][2], soglia=pct(SOGLIA))
    page = TEMPLATE
    for k, v in vals.items():
        page = page.replace("%%" + k + "%%", str(v))
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
    bets = paper(bets, matches, results)
    out = st.to_json()
    out.update(surfaces=st.surfaces, espn_done=st.espn_done, base_date=st.base_date)
    STATE.write_text(json.dumps(out, separators=(",", ":")))
    BETS.write_text(json.dumps(bets, ensure_ascii=False, indent=1))
    build_page(matches, bets, bt, model)
    print("Pagina aggiornata:", OUT)


if __name__ == "__main__":
    main()
