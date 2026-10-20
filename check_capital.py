#!/usr/bin/env python3
"""
Verifica del collegamento a Capital.com. SOLO LETTURA: non apre e non chiude nessun ordine.

Controlla login, conto, e per ogni materia prima trova lo strumento su Capital.com e ne legge
le condizioni reali (lotto minimo, passo, margine, costo notturno, spread, orari) e gli
ultimi prezzi giornalieri. Il risultato va nel log di GitHub Actions e in capital_check.json.
"""
import json, pathlib, sys, traceback
from broker_capital import Capital, CapitalError

ROOT = pathlib.Path(__file__).resolve().parent

# nome -> parole da cercare su Capital.com
SEARCH = {
    "Oro": ["Gold"], "Argento": ["Silver"], "Platino": ["Platinum"], "Palladio": ["Palladium"],
    "Rame": ["Copper"], "Petrolio WTI": ["US Crude Oil", "Crude Oil"], "Petrolio Brent": ["Brent"],
    "Gas naturale": ["Natural Gas"], "Benzina": ["Gasoline"], "Gasolio": ["Heating Oil", "Gasoil"],
    "Grano": ["Wheat"], "Mais": ["Corn"], "Soia": ["Soybean"], "Caffè": ["Coffee"],
    "Zucchero": ["Sugar"], "Cacao": ["Cocoa"], "Cotone": ["Cotton"], "Bovini vivi": ["Live Cattle", "Cattle"],
    "Suini": ["Lean Hogs", "Hogs"],
}


def short(d, keys):
    return {k: d.get(k) for k in keys if k in d}


def main():
    out = {"ok": False}
    try:
        cap = Capital()
        print(f"Ambiente: {cap.env.upper()}  ({cap.base})")
        cap.login()
        print("Login: OK")
        accs = cap.accounts()
        out["accounts"] = [{"id": a.get("accountId"), "type": a.get("accountType"), "currency": a.get("currency"),
                            "preferred": a.get("preferred"), "balance": a.get("balance")} for a in accs]
        for a in out["accounts"]:
            print(f"Conto {a['type']} {a['currency']} preferito={a['preferred']} saldo={a['balance']}")

        out["markets"] = {}
        for name, terms in SEARCH.items():
            found = []
            for t in terms:
                try:
                    found += [m for m in cap.search(t) if (m.get("instrumentType") or "").upper() == "COMMODITIES"]
                except CapitalError as e:
                    print(f"  ricerca {t}: {e}")
            seen, cands = set(), []
            for m in found:
                if m.get("epic") not in seen:
                    seen.add(m.get("epic"))
                    cands.append(short(m, ["epic", "instrumentName", "expiry", "marketStatus", "bid", "offer"]))
            print(f"\n== {name}: {len(cands)} strumenti")
            for c in cands[:6]:
                print("   ", c)
            info = {"candidates": cands[:6]}
            if cands:
                epic = cands[0]["epic"]
                try:
                    md = cap.market(epic)
                    ins, rules, snap = md.get("instrument", {}), md.get("dealingRules", {}), md.get("snapshot", {})
                    info["detail"] = {
                        "epic": epic,
                        "instrument": short(ins, ["name", "symbol", "expiry", "lotSize", "currency", "marginFactor",
                                                  "marginFactorUnit", "overnightFee", "type", "openingHours"]),
                        "dealingRules": rules,
                        "snapshot": short(snap, ["marketStatus", "bid", "offer", "high", "low", "updateTime",
                                                 "decimalPlacesFactor", "scalingFactor"]),
                    }
                    print("   regole:", json.dumps(rules)[:400])
                    print("   strumento:", json.dumps(info["detail"]["instrument"], default=str)[:400])
                    print("   prezzo:", info["detail"]["snapshot"])
                    pr = cap.prices(epic, "DAY", 5)
                    bars = pr.get("prices", [])
                    info["sample_prices"] = bars[-3:]
                    info["price_keys"] = sorted(pr.keys())
                    print(f"   ultime barre giornaliere: {len(bars)}  esempio: {json.dumps(bars[-1])[:300] if bars else '-'}")
                except CapitalError as e:
                    info["error"] = str(e)
                    print("   errore:", e)
            out["markets"][name] = info
        out["ok"] = True
        print("\nVerifica completata: nessun ordine e' stato aperto.")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        print("ERRORE:", out["error"])
        traceback.print_exc()
    (ROOT / "capital_check.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    sys.exit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
