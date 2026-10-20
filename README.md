# Turtle Pit

Robot di trading automatico sulle materie prime con il metodo Turtle, in **paper trading**: prezzi veri, soldi finti.

- Parte con 500 € il primo giorno in cui gira.
- Ogni ora a mercati aperti (seduta in corso, provvisoria) e ogni sera dopo la chiusura dei mercati USA (seduta definitiva), GitHub Actions scarica i prezzi di chiusura reali di 19 futures (oro, argento, platino, palladio, rame, petrolio WTI e Brent, gas naturale, gasolio, benzina, grano, mais, soia, caffè, zucchero, cacao, cotone, bovini, suini) da Yahoo Finance.
- Il robot apre, chiude e sposta gli stop da solo, poi salva il portafoglio in `state.json` e aggiorna la dashboard in `docs/index.html` (GitHub Pages).

## Conto simulato

Conto CFD XTB in euro con condizioni indicative reali: lotti minimi e passi di ogni strumento, spread, costo notturno (swap), 0,5% sulla conversione USD→EUR del risultato, slittamento degli ordini stop, margine con leva ESMA (oro 20:1, altre 10:1). Se il lotto minimo rischia più del 4% del capitale, il segnale viene saltato e registrato. Il gasolio è escluso perché XTB non lo offre.

## Regole

- Entrata long sopra il massimo a 20 giorni, short sotto il minimo a 20 giorni.
- Uscita sulla rottura opposta a 10 giorni, oppure allo stop a 2N (N = volatilità media a 20 giorni).
- Rischio 2% del capitale per trade, esposizione totale massima 3× il capitale.
- Orari di entrata e uscita in ora di Praga, dai prezzi a 5 minuti.

## Comandi

- Avvio manuale: tab **Actions** → *Robot Turtle giornaliero* → **Run workflow**.
- Ripartire da zero: cancella `state.json`.

Non è una consulenza finanziaria. I risultati passati e simulati non garantiscono quelli futuri.
