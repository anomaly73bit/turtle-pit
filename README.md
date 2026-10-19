# Turtle Pit

Robot di trading automatico sulle materie prime con il metodo Turtle, in **paper trading**: prezzi veri, soldi finti.

- Parte con 500 € il primo giorno in cui gira.
- Ogni sera feriale, dopo la chiusura dei mercati USA, GitHub Actions scarica i prezzi di chiusura reali di 19 futures (oro, argento, platino, palladio, rame, petrolio WTI e Brent, gas naturale, gasolio, benzina, grano, mais, soia, caffè, zucchero, cacao, cotone, bovini, suini) da Yahoo Finance.
- Il robot apre, chiude e sposta gli stop da solo, poi salva il portafoglio in `state.json` e aggiorna la dashboard in `docs/index.html` (GitHub Pages).

## Regole

- Entrata long sopra il massimo a 20 giorni, short sotto il minimo a 20 giorni.
- Uscita sulla rottura opposta a 10 giorni, oppure allo stop a 2N (N = volatilità media a 20 giorni).
- Rischio 2% del capitale per trade, esposizione totale massima 3× il capitale.
- Costi: 0,15% per ogni entrata e uscita.

## Comandi

- Avvio manuale: tab **Actions** → *Robot Turtle giornaliero* → **Run workflow**.
- Ripartire da zero: cancella `state.json`.

Non è una consulenza finanziaria. I risultati passati e simulati non garantiscono quelli futuri.
