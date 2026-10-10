"""EKİP PERFORMANSI — saf hesap modülü (veritabanı / MCP çağrısı YOK).

Kaynak yalnızca panelin KENDİ kayıtları: analizler (user_query_log), pazar kararları (market_decision), kontrol
listeleri, eğitim tamamlamaları ve forum katkıları. Tasarımdaki "görev / departman / günlük rapor / hedef %" verisi
sistemde olmadığı için uydurulmaz; kompozit "performans skoru" da yok (ağırlıklar keyfi olurdu).

Dönem [since, until) ile, aynı uzunluktaki ÖNCEKİ dönem [since − len, since) karşılaştırılır. Gün/saat kovaları
istemcinin saat dilimi farkıyla (tz_min, dakika; Türkiye = 180) hesaplanır.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

DECISIONS = ("Uygun", "Sınırda", "Elenmiş")
QUIET_DAYS = 14          # bu kadar gündür hiçbir etkinliği olmayan → "İlgi gerekiyor"
STALE_LIST_DAYS = 7      # açık kontrol listesi bu kadar gündür güncellenmediyse "bekleyen liste"
MAX_RANGE_DAYS = 366

_ASIN_TITLE_RE = re.compile(r"^(B0[A-Z0-9]{8})\s+—\s", re.I)


def product_key(keyword: str) -> str:
    """index._analysis_key ile aynı birleştirme ("B0.. — başlık" → "ASIN:B0..") + küçük harf (eşleştirme için)."""
    kw = str(keyword or "").strip()
    m = _ASIN_TITLE_RE.match(kw)
    if m:
        kw = f"ASIN:{m.group(1).upper()}"
    elif kw[:5].upper() == "ASIN:":
        kw = "ASIN:" + kw[5:].strip().upper()
    return " ".join(kw.split()).lower()


def _pct(a, b):
    return round(a / b, 4) if b else None


def _verdict_before(rows, at):
    """Kararın ön önerisi: karar anına kadarki SON sorgunun önerisi (önce sorgu yoksa en son sorgununki)."""
    if not rows:
        return None
    before = [r for r in rows if r[0] <= at]
    return (before or rows)[-1][1]


def build(users: list[dict], raw: dict, lessons: list[dict], verdict_log: list[dict], team_labels: dict,
          since: int, until: int, tz_min: int, now: int) -> dict:
    """users: list_users() satırları (yalnızca gösterilecek kişiler). lessons: list_lessons_all() satırları.
    raw: database.team_performance_raw(prev_since, until)."""
    span = until - since
    prev_since = since - span
    days = max(1, -(-span // 86400))
    tz = timedelta(minutes=tz_min)
    uids = {u["id"] for u in users}

    def local(ts):
        return datetime.fromtimestamp(ts, timezone.utc) + tz

    def day_idx(ts):
        return min(days - 1, max(0, (ts - since) // 86400))

    cur = lambda ts: ts is not None and since <= ts < until            # noqa: E731
    prv = lambda ts: ts is not None and prev_since <= ts < since        # noqa: E731

    P = {u["id"]: {
        "analyses": 0, "products": set(), "decisions": 0, "mix": {d: 0 for d in DECISIONS},
        "agree": 0, "agree_n": 0, "lists_started": 0, "lists_approved": 0, "lists_open": 0, "lists_stale": 0,
        "lessons_done_period": 0, "threads": 0, "replies": 0, "solutions": 0,
        "days": set(), "series": [0] * days,
        "prev": {"analyses": 0, "decisions": 0, "days": set(), "posts": 0}} for u in users}

    heat = [[0] * 24 for _ in range(7)]          # haftanın günü (Pzt=0) × saat
    daily = [{"analyses": 0, "decisions": 0, "posts": 0, "active": set()} for _ in range(days)]

    def touch(uid, ts, kind):
        p = P[uid]
        d = day_idx(ts)
        p["days"].add(d)
        daily[d]["active"].add(uid)
        daily[d][kind] += 1
        if kind != "posts":
            p["series"][d] += 1
        lt = local(ts)
        heat[lt.weekday()][lt.hour] += 1

    # --- Analizler
    for q in raw["queries"]:
        uid, ts = q["user_id"], q["queried_at"]
        if uid not in uids:
            continue
        if cur(ts):
            P[uid]["analyses"] += 1
            P[uid]["products"].add((product_key(q["keyword"]), q["marketplace"]))
            touch(uid, ts, "analyses")
        elif prv(ts):
            P[uid]["prev"]["analyses"] += 1
            P[uid]["prev"]["days"].add((ts - prev_since) // 86400)

    # --- Kararlar (tamamı gelir: dönüşüm, dönem içinde analiz edilen ürüne HERHANGİ bir zamanda verilen karar)
    vindex: dict = {}
    for r in verdict_log:
        vindex.setdefault((r["user_id"], product_key(r["keyword"]), r["marketplace"]), []).append(
            (r["queried_at"], r["verdict"]))
    latest_decision: dict = {}       # (uid, key, mp) -> en son karar
    for d in raw["decisions"]:
        uid, ts = d["user_id"], d["decided_at"]
        if uid not in uids:
            continue
        key = (uid, product_key(d["keyword"]), d["marketplace"])
        if ts < until:                   # geçmiş bir dönem, sonradan verilen kararla değişmesin
            latest_decision[key] = d["decision"]
        p = P[uid]
        if cur(ts):
            p["decisions"] += 1
            if d["decision"] in p["mix"]:
                p["mix"][d["decision"]] += 1
            v = _verdict_before(vindex.get(key), ts)
            if v in DECISIONS:
                p["agree_n"] += 1
                p["agree"] += int(v == d["decision"])
            touch(uid, ts, "decisions")
        elif prv(ts):
            p["prev"]["decisions"] += 1
            p["prev"]["days"].add((ts - prev_since) // 86400)

    # --- Kontrol listeleri
    lists_by_key: dict = {}
    for c in raw["checklists"]:
        uid = c["user_id"]
        if uid not in uids:
            continue
        key = (uid, product_key(c["analysis_key"]), c["marketplace"])
        if c["created_at"] < until:
            lists_by_key.setdefault(key, []).append(c)
        p = P[uid]
        if cur(c["created_at"]):
            p["lists_started"] += 1
        if c["status"] == "locked" and cur(c.get("locked_at")):
            p["lists_approved"] += 1
        if c["status"] == "open":
            p["lists_open"] += 1
            if (c.get("updated_at") or c["created_at"]) < now - STALE_LIST_DAYS * 86400:
                p["lists_stale"] += 1

    # --- Forum
    for kind, rows in (("threads", raw["threads"]), ("replies", raw["replies"])):
        for r in rows:
            uid, ts = r["user_id"], r["created_at"]
            if uid not in uids:
                continue
            if cur(ts):
                P[uid][kind] += 1
                touch(uid, ts, "posts")
            elif prv(ts):
                P[uid]["prev"]["posts"] += 1
                P[uid]["prev"]["days"].add((ts - prev_since) // 86400)
    for r in raw["solutions"]:
        if r["user_id"] in uids and cur(r["solved_at"]):
            P[r["user_id"]]["solutions"] += 1

    # --- Eğitim (atananlar DİNAMİK — /api/training/progress ile aynı kural; gecikme = son tarihi bugünden önce)
    today = local(now).strftime("%Y-%m-%d")
    done = {(c["lesson_id"], c["user_id"]): c["completed_at"] for c in raw["completions"]}
    team_of = {u["id"]: set(u.get("team_ids") or []) for u in users}
    in_team = {u["id"]: bool(u.get("team_ids")) for u in users}
    training = {u["id"]: {"assigned": 0, "done": 0, "overdue": []} for u in users}
    for l in lessons:
        mode = l.get("assign_mode")
        for u in users:
            uid = u["id"]
            if not in_team[uid]:
                continue
            if mode == "all":
                ok = True
            elif mode == "teams":
                ok = bool(team_of[uid] & set(l.get("team_ids") or []))
            else:
                ok = uid in set(l.get("assignee_ids") or [])
            if not ok:
                continue
            t = training[uid]
            t["assigned"] += 1
            at = done.get((l["id"], uid))
            if at:
                t["done"] += 1
                if cur(at):
                    P[uid]["lessons_done_period"] += 1
            elif l.get("due_date") and l["due_date"] < today:
                t["overdue"].append({"id": l["id"], "title": l["title"], "due_date": l["due_date"]})

    # --- Kişi çıktısı
    last_active = raw.get("last_active") or {}
    people = []
    for u in users:
        uid, p, t = u["id"], P[u["id"]], training[u["id"]]
        decided = sum(1 for (k, mp) in p["products"] if (uid, k, mp) in latest_decision)
        la = last_active.get(uid)
        active = bool(p["days"])
        flags = []
        if t["overdue"]:
            flags.append(f"{len(t['overdue'])} gecikmiş ders")
        if la is None or la < now - QUIET_DAYS * 86400:
            flags.append(f"{QUIET_DAYS}+ gündür etkinlik yok" if la else "Hiç etkinlik yok")
        if p["lists_stale"]:
            flags.append(f"{p['lists_stale']} liste {STALE_LIST_DAYS}+ gündür bekliyor")
        status = "attention" if flags else ("active" if active else "quiet")
        people.append({
            "user_id": uid, "name": u.get("name") or u.get("email") or "—", "title": u.get("title") or "",
            "role": u.get("role"), "teams": [team_labels[i] for i in u.get("direct_team_ids") or [] if i in team_labels],
            "analyses": p["analyses"], "products": len(p["products"]), "decided_products": decided,
            "conversion": _pct(decided, len(p["products"])),
            "decisions": p["decisions"], "mix": p["mix"],
            "agreement": _pct(p["agree"], p["agree_n"]), "agreement_n": p["agree_n"],
            "lists_started": p["lists_started"], "lists_approved": p["lists_approved"],
            "lists_open": p["lists_open"], "lists_stale": p["lists_stale"],
            "lessons_assigned": t["assigned"], "lessons_done": t["done"], "lessons_overdue": t["overdue"],
            "lessons_done_period": p["lessons_done_period"],
            "threads": p["threads"], "replies": p["replies"], "solutions": p["solutions"],
            "active_days": len(p["days"]), "series": p["series"], "last_active": la,
            "prev": {"analyses": p["prev"]["analyses"], "decisions": p["prev"]["decisions"],
                     "active_days": len(p["prev"]["days"]), "posts": p["prev"]["posts"]},
            "status": status, "flags": flags})

    # --- Ekip toplamları + önceki dönem
    def tot(key):
        return sum(x[key] for x in people)

    products = sum(x["products"] for x in people)
    decided = sum(x["decided_products"] for x in people)
    agree_n = sum(P[u["id"]]["agree_n"] for u in users)
    agree = sum(P[u["id"]]["agree"] for u in users)
    assigned = sum(t["assigned"] for t in training.values())
    done_total = sum(t["done"] for t in training.values())

    # Ürün hattı (iç içe): dönemde analiz edilen ürün → karar verilen → son kararı Uygun → liste açılan → onaylanan
    funnel = [0, 0, 0, 0, 0]
    for x in people:
        uid = x["user_id"]
        for (k, mp) in P[uid]["products"]:
            funnel[0] += 1
            dec = latest_decision.get((uid, k, mp))
            if not dec:
                continue
            funnel[1] += 1
            if dec != "Uygun":
                continue
            funnel[2] += 1
            lists = lists_by_key.get((uid, k, mp)) or []
            if lists:
                funnel[3] += 1
                if any(c["status"] == "locked" and (c.get("locked_at") or 0) < until for c in lists):
                    funnel[4] += 1

    mix = {d: sum(x["mix"][d] for x in people) for d in DECISIONS}
    labels = []
    for i in range(days):
        lt = local(since + i * 86400 + 43200)      # gün ortası: yaz saati kaymasına dayanıklı
        labels.append(lt.strftime("%Y-%m-%d"))

    return {
        "range": {"since": since, "until": until, "prev_since": prev_since, "days": days},
        "totals": {
            "members": len(people), "active_members": sum(1 for x in people if x["active_days"]),
            "analyses": tot("analyses"), "products": products, "decisions": tot("decisions"),
            "conversion": _pct(decided, products), "agreement": _pct(agree, agree_n), "agreement_n": agree_n,
            "lists_started": tot("lists_started"), "lists_approved": tot("lists_approved"),
            "lists_open": tot("lists_open"), "lists_stale": tot("lists_stale"),
            "lessons_assigned": assigned, "lessons_done": done_total, "training_rate": _pct(done_total, assigned),
            "lessons_overdue": sum(len(x["lessons_overdue"]) for x in people),
            "lessons_done_period": tot("lessons_done_period"),
            "posts": tot("threads") + tot("replies"), "solutions": tot("solutions"),
            "attention": sum(1 for x in people if x["status"] == "attention"),
        },
        "prev": {
            "active_members": sum(1 for x in people if x["prev"]["active_days"]),
            "analyses": sum(x["prev"]["analyses"] for x in people),
            "decisions": sum(x["prev"]["decisions"] for x in people),
            "posts": sum(x["prev"]["posts"] for x in people),
        },
        "daily": [{"date": labels[i], "analyses": daily[i]["analyses"], "decisions": daily[i]["decisions"],
                   "posts": daily[i]["posts"], "active": len(daily[i]["active"])} for i in range(days)],
        "heatmap": heat, "mix": mix, "funnel": funnel,
        "people": people,
    }
