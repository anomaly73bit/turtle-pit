"""
Modello delle partite di tennis condiviso da backtest e robot.

Stato dei giocatori (Elo generale e per superficie, storico recente, scontri diretti, ranking)
aggiornato partita per partita, e caratteristiche calcolate SOLO con dati precedenti alla partita.
I giocatori sono identificati come "TOUR|Cognome I." (formato tennis-data.co.uk).
"""
import collections, datetime, json, math, re, unicodedata

FEAT_A = ['elo_d', 'selo_d', 'lrank_d', 'lpts_d', 'form10_d', 'form30_d', 'sform_d', 'm7_d', 'm30_d',
          'rest_d', 'exp_d', 'h2h_d', 'best5']
FEAT_B = ['lim'] + FEAT_A
HIST_KEEP = 40


def kf(n):
    return 250 / ((n + 5) ** 0.4)


def pelo(ra, rb):
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def as_date(d):
    if isinstance(d, datetime.date):
        return d if not isinstance(d, datetime.datetime) else d.date()
    return datetime.date.fromisoformat(str(d)[:10])


class State:
    def __init__(s):
        s.elo, s.selo = {}, {}                    # "T|p" e "T|surf|p" -> rating
        s.n, s.sn = collections.Counter(), collections.Counter()
        s.hist = collections.defaultdict(list)    # "T|p" -> [[YYYY-MM-DD, won]]
        s.shist = collections.defaultdict(list)   # "T|surf|p" -> [won]
        s.h2h = collections.Counter()             # "T|a|b" -> vittorie di a su b
        s.rank, s.pts = {}, {}                    # "T|p" -> ultimo ranking / punti noti
        s.last_date = None

    # ---------- caratteristiche prima della partita ----------
    def features(s, tour, a, b, surf, date, best5, ra=None, rb=None, pa=None, pb=None):
        date = as_date(date)
        ka, kb, ska, skb = f"{tour}|{a}", f"{tour}|{b}", f"{tour}|{surf}|{a}", f"{tour}|{surf}|{b}"
        ra = ra if ra == ra and ra else s.rank.get(ka)
        rb = rb if rb == rb and rb else s.rank.get(kb)
        pa = pa if pa == pa and pa else s.pts.get(ka)
        pb = pb if pb == pb and pb else s.pts.get(kb)

        def form(k, m):
            h = s.hist[k][-m:]
            return (sum(w for _, w in h) + 1) / (len(h) + 2)

        def recent(k, days):
            return sum(1 for d, _ in s.hist[k] if 0 < (date - as_date(d)).days <= days)

        def rest(k):
            return min((date - as_date(s.hist[k][-1][0])).days, 60) if s.hist[k] else 60

        def sform(k):
            h = s.shist[k][-20:]
            return (sum(h) + 1) / (len(h) + 2)

        lr = lambda r: math.log(r) if r and r == r and r > 0 else math.log(1500)
        lp = lambda p: math.log1p(p) if p and p == p and p > 0 else 0.0
        ea, eb = s.elo.get(ka, 1500.0), s.elo.get(kb, 1500.0)
        sa, sb = s.selo.get(ska, 1500.0), s.selo.get(skb, 1500.0)
        return {
            'elo_d': (ea - eb) / 100, 'selo_d': (sa - sb) / 100,
            'p_mix': 0.5 * pelo(ea, eb) + 0.5 * pelo(sa, sb),
            'lrank_d': lr(rb) - lr(ra), 'lpts_d': lp(pa) - lp(pb),
            'form10_d': form(ka, 10) - form(kb, 10), 'form30_d': form(ka, 30) - form(kb, 30),
            'sform_d': sform(ska) - sform(skb),
            'm7_d': recent(ka, 7) - recent(kb, 7), 'm30_d': recent(ka, 30) - recent(kb, 30),
            'rest_d': rest(ka) - rest(kb),
            'exp_d': math.log1p(s.n[ka]) - math.log1p(s.n[kb]),
            'nmin': min(s.n[ka], s.n[kb]),
            'h2h_d': s.h2h[f"{tour}|{a}|{b}"] - s.h2h[f"{tour}|{b}|{a}"],
            'best5': int(best5),
        }

    # ---------- aggiornamento dopo la partita ----------
    def update(s, tour, w, l, surf, date, wr=None, lr=None, wp=None, lp=None):
        date = as_date(date)
        kw, kl, skw, skl = f"{tour}|{w}", f"{tour}|{l}", f"{tour}|{surf}|{w}", f"{tour}|{surf}|{l}"
        ew, el = s.elo.get(kw, 1500.0), s.elo.get(kl, 1500.0)
        p = pelo(ew, el)
        s.elo[kw], s.elo[kl] = ew + kf(s.n[kw]) * (1 - p), el - kf(s.n[kl]) * (1 - p)
        ew, el = s.selo.get(skw, 1500.0), s.selo.get(skl, 1500.0)
        p = pelo(ew, el)
        s.selo[skw], s.selo[skl] = ew + kf(s.sn[skw]) * (1 - p), el - kf(s.sn[skl]) * (1 - p)
        s.n[kw] += 1; s.n[kl] += 1; s.sn[skw] += 1; s.sn[skl] += 1
        ds = date.isoformat()
        s.hist[kw].append([ds, 1]); s.hist[kl].append([ds, 0])
        s.hist[kw] = s.hist[kw][-HIST_KEEP:]; s.hist[kl] = s.hist[kl][-HIST_KEEP:]
        s.shist[skw].append(1); s.shist[skl].append(0)
        s.shist[skw] = s.shist[skw][-20:]; s.shist[skl] = s.shist[skl][-20:]
        s.h2h[f"{tour}|{w}|{l}"] += 1
        for k, r, pt in ((kw, wr, wp), (kl, lr, lp)):
            if r and r == r:
                s.rank[k] = float(r)
            if pt and pt == pt:
                s.pts[k] = float(pt)
        if not s.last_date or ds > s.last_date:
            s.last_date = ds

    # ---------- salvataggio ----------
    def to_json(s):
        return {"elo": {k: round(v, 2) for k, v in s.elo.items()}, "selo": {k: round(v, 2) for k, v in s.selo.items()},
                "n": dict(s.n), "sn": dict(s.sn), "hist": dict(s.hist), "shist": dict(s.shist),
                "h2h": dict(s.h2h), "rank": s.rank, "pts": s.pts, "last_date": s.last_date}

    @classmethod
    def from_json(cls, d):
        s = cls()
        s.elo, s.selo = d["elo"], d["selo"]
        s.n, s.sn = collections.Counter(d["n"]), collections.Counter(d["sn"])
        s.hist = collections.defaultdict(list, d["hist"])
        s.shist = collections.defaultdict(list, d["shist"])
        s.h2h = collections.Counter(d["h2h"])
        s.rank, s.pts, s.last_date = d["rank"], d["pts"], d["last_date"]
        return s


# ---------- modello B: correttore delle quote ----------
def predict_b(model, f, implied_a):
    z = model["intercept"] + model["coef"]["lim"] * logit(implied_a)
    for k in FEAT_A:
        z += model["coef"][k] * f[k]
    return 1 / (1 + math.exp(-z))


# ---------- nomi ----------
def norm(x):
    x = unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z\- ]", " ", x).replace("-", " ").split()


class Names:
    """Collega un nome completo ("Jannik Sinner") al formato "Sinner J." dei dati storici."""
    def __init__(s, keys):
        s.by_tour = collections.defaultdict(dict)       # tour -> "cognome|iniziale" -> nome
        s.by_sur = collections.defaultdict(lambda: collections.defaultdict(set))
        for k in keys:
            tour, name = k.split("|", 1)
            m = re.match(r"^(.*)\s+([A-Za-z][A-Za-z\-\.]*)\.?$", name.strip())
            if not m:
                continue
            sur, ini = " ".join(norm(m.group(1))), norm(m.group(2))[0][0] if norm(m.group(2)) else ""
            s.by_tour[tour][f"{sur}|{ini}"] = name
            s.by_sur[tour][sur].add(name)

    def find(s, tour, full):
        t = norm(full.replace(",", " "))
        if not t:
            return None
        if "," in full:  # "Cognome, Nome"
            sur, first = full.split(",", 1)
            t = norm(first) + norm(sur)
        tries = []
        for i in range(1, len(t)):
            tries.append((" ".join(t[i:]), t[0][0]))      # Nome Cognome Cognome2
        for i in range(1, len(t)):
            tries.append((" ".join(t[:i]), t[i][0]))      # Cognome Nome (nomi asiatici)
        for sur, ini in tries:
            n = s.by_tour[tour].get(f"{sur}|{ini}")
            if n:
                return n
        for sur, _ in tries:
            c = s.by_sur[tour].get(sur)
            if c and len(c) == 1:
                return next(iter(c))
        return None
