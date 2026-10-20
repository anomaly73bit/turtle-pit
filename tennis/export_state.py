#!/usr/bin/env python3
"""
Prepara il robot a partire dai file storici di tennis-data.co.uk (quote e risultati ATP/WTA).
Uso:  python export_state.py cartella_con_i_file_xlsx

Produce:
  state.json     stato dei giocatori aggiornato all'ultima partita dei file
  model.json     modello B (correttore delle quote) addestrato su tutto il periodo
  backtest.json  risultato della prova onesta: addestrato fino al 2024, provato su 2025-2026
"""
import glob, json, math, pathlib, sys
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from tennis_model import State, FEAT_A, FEAT_B, logit

HERE = pathlib.Path(__file__).resolve().parent
ODDS = ['B365W', 'B365L', 'PSW', 'PSL', 'MaxW', 'MaxL', 'AvgW', 'AvgL']
RORDER = {'1st Round': 1, '2nd Round': 2, '3rd Round': 3, '4th Round': 4, 'Round Robin': 4,
          'Quarterfinals': 5, 'Semifinals': 6, 'The Final': 7}


def load(folder):
    fr = []
    for f in glob.glob(str(pathlib.Path(folder) / '*.xlsx')):
        df = pd.read_excel(f)
        df['tour'] = 'WTA' if 'WTA' in df.columns else 'ATP'
        fr.append(df[['tour', 'Date', 'Location', 'Tournament', 'Surface', 'Round', 'Best of', 'Winner', 'Loser',
                      'WRank', 'LRank', 'WPts', 'LPts', 'Comment'] + ODDS])
    D = pd.concat(fr)
    for c in ODDS + ['WRank', 'LRank', 'WPts', 'LPts']:
        D[c] = pd.to_numeric(D[c], errors='coerce')
    D['ro'] = D['Round'].map(RORDER).fillna(3)
    return D.sort_values(['Date', 'ro']).reset_index(drop=True)


def main(folder):
    D = load(folder)
    rng = np.random.default_rng(7)
    st, rows = State(), []
    for r in D.itertuples(index=False):
        comment = str(r.Comment)
        swap = rng.random() < 0.5
        a, b = (r.Loser, r.Winner) if swap else (r.Winner, r.Loser)
        ra, rb = (r.LRank, r.WRank) if swap else (r.WRank, r.LRank)
        pa, pb = (r.LPts, r.WPts) if swap else (r.WPts, r.LPts)
        oa, ob = (r.B365L, r.B365W) if swap else (r.B365W, r.B365L)
        sa, sb = (r.PSL, r.PSW) if swap else (r.PSW, r.PSL)
        f = st.features(r.tour, a, b, r.Surface, r.Date, getattr(r, '_6') == 5, ra, rb, pa, pb)
        f.update(date=r.Date, tour=r.tour, y=0 if swap else 1, completed='Completed' in comment, oa=oa, ob=ob, sa=sa, sb=sb,
                 pa_name=a, pb_name=b, tourn=r.Tournament)
        rows.append(f)
        if 'Completed' in comment or 'Retired' in comment:
            st.update(r.tour, r.Winner, r.Loser, r.Surface, r.Date, r.WRank, r.LRank, r.WPts, r.LPts)
    X = pd.DataFrame(rows)
    X = X[X.completed & (X.nmin >= 10) & (X.oa > 1) & (X.ob > 1)].copy()
    ia, ib = 1 / X.oa, 1 / X.ob
    X['imp'] = ia / (ia + ib)
    X['lim'] = X.imp.map(logit)

    # prova onesta: addestrato fino al 2024, provato dopo
    tr, te = X[(X.date >= '2023-04-01') & (X.date < '2025-01-01')], X[X.date >= '2025-01-01']
    mb = LogisticRegression(C=0.5, max_iter=3000).fit(tr[FEAT_B], tr.y)
    pt = mb.predict_proba(te[FEAT_B])[:, 1]
    bt = {"train": [str(tr.date.min().date()), str(tr.date.max().date()), len(tr)],
          "test": [str(te.date.min().date()), str(te.date.max().date()), len(te)], "rows": []}
    for thr in (0.02, 0.05, 0.10):
        pnl = []
        for p, oa, ob, y in zip(pt, te.oa, te.ob, te.y):
            ea, eb = p * oa - 1, (1 - p) * ob - 1
            if ea > thr and ea >= eb:
                pnl.append(oa - 1 if y == 1 else -1)
            elif eb > thr:
                pnl.append(ob - 1 if y == 0 else -1)
        bt["rows"].append({"thr": thr, "bets": len(pnl), "roi": float(np.mean(pnl)) if pnl else 0,
                           "se": float(np.std(pnl) / math.sqrt(len(pnl))) if len(pnl) > 1 else 0})
    # elenco delle scommesse della prova, con la regola usata dal robot (valore >= 5%, quota 1,20-6,00)
    bt["bets"] = []
    for p, oa, ob, y, d, tour, tn, na, nb in zip(pt, te.oa, te.ob, te.y, te.date, te.tour, te.tourn, te.pa_name, te.pb_name):
        ea, eb = p * oa - 1, (1 - p) * ob - 1
        side = None
        if ea >= 0.05 and ea >= eb and 1.2 <= oa <= 6:
            side = 1
        elif eb >= 0.05 and 1.2 <= ob <= 6:
            side = 2
        if side:
            bt["bets"].append([str(d.date()), tour, tn, na if side == 1 else nb, nb if side == 1 else na,
                               float(oa if side == 1 else ob), round(float(p if side == 1 else 1 - p), 4),
                               int((y == 1) == (side == 1))])
    # metodo delle quote sharp: probabilita' giusta da Pinnacle (senza margine), si gioca Bet365 se paga di piu'
    bt["bets_sharp"] = []
    for oa, ob, sa, sb, y, d, tour, tn, na, nb in zip(te.oa, te.ob, te.sa, te.sb, te.y, te.date, te.tour, te.tourn, te.pa_name, te.pb_name):
        if not (sa > 1 and sb > 1):
            continue
        p = (1 / sa) / (1 / sa + 1 / sb)
        ea, eb = p * oa - 1, (1 - p) * ob - 1
        side = 1 if (ea >= 0.03 and ea >= eb and 1.2 <= oa <= 6) else 2 if (eb >= 0.03 and 1.2 <= ob <= 6) else None
        if side:
            bt["bets_sharp"].append([str(d.date()), tour, tn, na if side == 1 else nb, nb if side == 1 else na,
                                     float(oa if side == 1 else ob), round(p if side == 1 else 1 - p, 4),
                                     int((y == 1) == (side == 1))])
    ll = lambda y, p: float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    bt["logloss_model"], bt["logloss_bookmaker"] = ll(te.y, pt), ll(te.y, te.imp)
    print(json.dumps(bt, indent=1))

    # modello definitivo su tutto il periodo
    full = X[X.date >= '2023-04-01']
    m = LogisticRegression(C=0.5, max_iter=3000).fit(full[FEAT_B], full.y)
    model = {"intercept": float(m.intercept_[0]), "coef": dict(zip(FEAT_B, map(float, m.coef_[0]))),
             "trained_on": [str(full.date.min().date()), str(full.date.max().date()), len(full)],
             "bookmaker": "Bet365 (tennis-data.co.uk)"}
    surfaces = {}  # luogo o torneo -> {mese: superficie}
    for r in D.itertuples(index=False):
        for key in (str(r.Location).lower(), str(r.Tournament).lower()):
            surfaces.setdefault(key, {})[str(r.Date.month)] = r.Surface
    st_json = st.to_json()
    st_json["surfaces"] = surfaces
    st_json["espn_done"] = []
    (HERE / "state.json").write_text(json.dumps(st_json, separators=(",", ":")))
    (HERE / "model.json").write_text(json.dumps(model, indent=1))
    (HERE / "backtest.json").write_text(json.dumps(bt, indent=1))
    print("stato aggiornato al", st.last_date, "giocatori", len(st.elo))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../data")
