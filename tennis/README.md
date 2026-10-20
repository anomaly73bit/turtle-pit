# Radar Tennis

Robot che ogni 30 minuti legge le quote Unibet delle partite ATP e WTA di singolare non ancora iniziate, le confronta con un modello statistico e pubblica la pagina `docs/tennis/index.html` (GitHub Pages: `/turtle-pit/tennis/`).

- `tennis_model.py`: stato dei giocatori (Elo generale e per superficie, forma, stanchezza, scontri diretti, ranking) e modello.
- `export_state.py`: prepara `state.json`, `model.json` e `backtest.json` dai file storici di tennis-data.co.uk (`python export_state.py cartella_xlsx`).
- `robot.py`: aggiorna lo stato con i risultati ESPN, legge le quote Unibet (Kambi), registra le scommesse finte in `bets.json`, rigenera la pagina.

Scommesse finte: 1 unità quando (probabilità del modello × quota Unibet − 1) ≥ 5%, quota tra 1,20 e 6,00, almeno 10 partite di dati per entrambi i giocatori. Nella prova onesta sul 2025-2026 il modello non ha battuto le quote dei bookmaker. Nessuna scommessa vera viene piazzata.
