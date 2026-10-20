"""
Collegamento a Capital.com tramite la sua API REST ufficiale (https://open-api.capital.com/).

Le credenziali NON sono nel codice: arrivano dalle variabili d'ambiente, che su GitHub
vengono dai "Secrets" cifrati del repository:
  CAPITAL_API_KEY      chiave API creata in Impostazioni -> API integrations
  CAPITAL_IDENTIFIER   email di accesso a Capital.com
  CAPITAL_PASSWORD     password personalizzata scelta quando si crea la chiave API
  CAPITAL_ENV          "demo" (predefinito) oppure "live" per il conto reale
"""
import json, os, time, urllib.error, urllib.parse, urllib.request

URLS = {
    "demo": "https://demo-api-capital.backend-capital.com",
    "live": "https://api-capital.backend-capital.com",
}


class CapitalError(Exception):
    pass


class Capital:
    def __init__(self, env=None):
        self.env = (env or os.environ.get("CAPITAL_ENV", "demo")).strip().lower()
        if self.env not in URLS:
            raise CapitalError(f"CAPITAL_ENV deve essere 'demo' o 'live', non '{self.env}'")
        self.base = URLS[self.env]
        self.key = os.environ.get("CAPITAL_API_KEY", "").strip()
        self.ident = os.environ.get("CAPITAL_IDENTIFIER", "").strip()
        self.pwd = os.environ.get("CAPITAL_PASSWORD", "").strip()
        missing = [n for n, v in (("CAPITAL_API_KEY", self.key), ("CAPITAL_IDENTIFIER", self.ident),
                                  ("CAPITAL_PASSWORD", self.pwd)) if not v]
        if missing:
            raise CapitalError("Mancano i Secrets: " + ", ".join(missing))
        self.cst = self.token = None
        self._last = 0.0

    # ---------- HTTP ----------
    def _req(self, method, path, body=None, params=None, auth=True, retries=3):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth:
            headers.update({"CST": self.cst or "", "X-SECURITY-TOKEN": self.token or ""})
        else:
            headers["X-CAP-API-KEY"] = self.key
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries):
            wait = 0.12 - (time.time() - self._last)  # massimo 10 richieste al secondo
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read().decode() or "{}"
                    return json.loads(raw), r.headers
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors="replace")[:400]
                if e.code == 429 and attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                if e.code == 401 and auth and attempt < retries - 1:
                    self.login()
                    headers.update({"CST": self.cst, "X-SECURITY-TOKEN": self.token})
                    continue
                raise CapitalError(f"{method} {path} -> HTTP {e.code}: {msg}")
            except urllib.error.URLError as e:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                raise CapitalError(f"{method} {path} -> rete: {e}")

    # ---------- sessione ----------
    def login(self):
        body = {"identifier": self.ident, "password": self.pwd, "encryptedPassword": False}
        data, headers = self._req("POST", "/api/v1/session", body, auth=False)
        self.cst, self.token = headers.get("CST"), headers.get("X-SECURITY-TOKEN")
        if not self.cst or not self.token:
            raise CapitalError("Login riuscito ma senza token di sessione")
        return data

    def ping(self):
        return self._req("GET", "/api/v1/ping")[0]

    # ---------- conto ----------
    def accounts(self):
        return self._req("GET", "/api/v1/accounts")[0].get("accounts", [])

    # ---------- mercati e prezzi ----------
    def search(self, term):
        return self._req("GET", "/api/v1/markets", params={"searchTerm": term})[0].get("markets", [])

    def market(self, epic):
        return self._req("GET", "/api/v1/markets/" + urllib.parse.quote(epic))[0]

    def prices(self, epic, resolution="DAY", max_bars=1000, frm=None, to=None):
        params = {"resolution": resolution, "max": max_bars, "from": frm, "to": to}
        return self._req("GET", "/api/v1/prices/" + urllib.parse.quote(epic), params=params)[0]

    # ---------- posizioni ----------
    def positions(self):
        return self._req("GET", "/api/v1/positions")[0].get("positions", [])

    def confirm(self, deal_ref, tries=5):
        for i in range(tries):
            try:
                return self._req("GET", "/api/v1/confirms/" + deal_ref)[0]
            except CapitalError:
                if i == tries - 1:
                    raise
                time.sleep(0.5)

    def open_position(self, epic, direction, size, stop_level=None):
        body = {"epic": epic, "direction": direction, "size": size, "guaranteedStop": False}
        if stop_level is not None:
            body["stopLevel"] = stop_level
        ref = self._req("POST", "/api/v1/positions", body)[0].get("dealReference")
        return self.confirm(ref) if ref else None

    def update_stop(self, deal_id, stop_level):
        body = {"guaranteedStop": False, "trailingStop": False, "stopLevel": stop_level}
        ref = self._req("PUT", "/api/v1/positions/" + deal_id, body)[0].get("dealReference")
        return self.confirm(ref) if ref else None

    def close_position(self, deal_id):
        ref = self._req("DELETE", "/api/v1/positions/" + deal_id)[0].get("dealReference")
        return self.confirm(ref) if ref else None

    # ---------- storico ----------
    def activity(self, last_seconds=86400):
        return self._req("GET", "/api/v1/history/activity",
                         params={"lastPeriod": last_seconds, "detailed": "true"})[0].get("activities", [])

    def transactions(self, last_seconds=86400):
        return self._req("GET", "/api/v1/history/transactions",
                         params={"lastPeriod": last_seconds})[0].get("transactions", [])
