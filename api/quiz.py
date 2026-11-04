"""
Eğitim videosu içi sorular (yalnızca YouTube dersleri) — saf yardımcılar, veritabanı/MCP yok.

- Dersi ekleyen (owner/admin) istediği saniyeye çoktan seçmeli soru koyar (2–4 şık, tek doğru).
- İzleyici o saniyeye gelince video durur; şıkka tıklar, SUNUCU doğru/yanlış döner. Doğru şık tarayıcıya
  ancak kişi soruyu ÇÖZDÜKTEN sonra gönderilir (sayfa kaynağından okunamasın).
- Kişi doğruyu bulana kadar dener (kullanıcı isteği). Sorular "Tamamladım" şartına GİRMEZ.
- Kayıt (kişi × soru): attempts (farklı şık denemesi), wrong (yanlış denenen şıklar), first_correct (ilk denemede
  doğru mu), solved_at.
"""
from __future__ import annotations

import json
import re

MAX_QUESTIONS = 20
MIN_OPTIONS, MAX_OPTIONS = 2, 4
TEXT_MAX, OPTION_MAX, EXPLANATION_MAX = 300, 200, 500
MAX_AT = 6 * 3600.0   # watch.MAX_DURATION ile aynı

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class QuizError(ValueError):
    pass


def _clean_text(value, limit: int, what: str, required: bool = True) -> str:
    s = _CTRL_RE.sub("", str(value or "")).replace("\r\n", "\n").strip()
    s = re.sub(r"[ \t]+", " ", s)
    if required and not s:
        raise QuizError(f"{what} boş olamaz")
    if len(s) > limit:
        raise QuizError(f"{what} en fazla {limit} karakter olabilir")
    return s


def fmt_at(sec: float) -> str:
    sec = int(sec or 0)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def clean_questions(items: list[dict]) -> list[dict]:
    """İstemciden gelen soru listesini doğrular ve zamana göre sıralar. Hata → QuizError (422)."""
    if len(items) > MAX_QUESTIONS:
        raise QuizError(f"Bir derse en fazla {MAX_QUESTIONS} soru eklenebilir")
    out = []
    for n, q in enumerate(items, 1):
        try:
            at = float(q.get("at"))
        except (TypeError, ValueError):
            raise QuizError(f"{n}. soru: zaman geçersiz")
        if not (0 <= at <= MAX_AT) or at != at:
            raise QuizError(f"{n}. soru: zaman 0 ile 6 saat arasında olmalı")
        text = _clean_text(q.get("text"), TEXT_MAX, f"{n}. soru metni")
        raw_opts = q.get("options") or []
        if not isinstance(raw_opts, list):
            raise QuizError(f"{n}. soru: şıklar liste olmalı")
        opts = [_clean_text(o, OPTION_MAX, f"{n}. soru şıkkı") for o in raw_opts]
        if not (MIN_OPTIONS <= len(opts) <= MAX_OPTIONS):
            raise QuizError(f"{n}. soru: {MIN_OPTIONS}–{MAX_OPTIONS} şık olmalı")
        if len({o.casefold() for o in opts}) != len(opts):
            raise QuizError(f"{n}. soru: aynı şık iki kez yazılmış")
        correct = q.get("correct")
        if not isinstance(correct, int) or isinstance(correct, bool) or not (0 <= correct < len(opts)):
            raise QuizError(f"{n}. soru: doğru şıkkı işaretleyin")
        qid = q.get("id")
        out.append({"id": qid if isinstance(qid, int) and not isinstance(qid, bool) and qid > 0 else None,
                    "at": round(at, 1), "text": text, "options": opts, "correct": correct,
                    "explanation": _clean_text(q.get("explanation"), EXPLANATION_MAX, f"{n}. soru açıklaması",
                                               required=False)})
    out.sort(key=lambda x: x["at"])
    return out


def parse_options(raw) -> list[str]:
    try:
        v = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def parse_wrong(raw) -> list[int]:
    try:
        v = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return sorted({int(x) for x in v if isinstance(x, int) and not isinstance(x, bool)}) if isinstance(v, list) else []


def staff_view(q: dict) -> dict:
    """Ders yönetimi (owner/admin) — doğru şık ve açıklama dahil."""
    return {"id": q["id"], "at": q["at"], "text": q["text"], "options": parse_options(q["options_json"]),
            "correct": q["correct"], "explanation": q.get("explanation") or ""}


def answer_state(q: dict, ans: dict | None) -> dict:
    """Kişinin bir sorudaki durumu. Doğru şık + açıklama YALNIZCA çözüldüyse döner."""
    solved = bool(ans and ans.get("solved_at"))
    out = {"solved": solved, "attempts": (ans or {}).get("attempts") or 0,
           "wrong": parse_wrong((ans or {}).get("wrong_json")),
           "first_correct": bool(ans and ans.get("first_correct"))}
    if solved:
        out["correct"] = q["correct"]
        out["explanation"] = q.get("explanation") or ""
    return out


def viewer_view(q: dict, ans: dict | None) -> dict:
    """İzleyiciye giden soru — doğru şık YOK (çözülmediyse)."""
    return {"id": q["id"], "at": q["at"], "text": q["text"], "options": parse_options(q["options_json"]),
            "my": answer_state(q, ans)}


def apply_answer(q: dict, ans: dict | None, choice: int, now: int) -> tuple[dict, bool, bool]:
    """Bir cevabı kayda uygular. Döner: (yeni kayıt, bu şık doğru mu, kayıt değişti mi).
    Çözülmüş soruda ve daha önce denenmiş yanlış şıkta kayıt değişmez (deneme iki kez sayılmaz)."""
    ans = dict(ans or {"attempts": 0, "wrong_json": "[]", "first_correct": 0, "solved_at": None})
    correct = choice == q["correct"]
    wrong = parse_wrong(ans.get("wrong_json"))
    if ans.get("solved_at") or (not correct and choice in wrong):
        return ans, correct, False
    ans["attempts"] = (ans.get("attempts") or 0) + 1
    if correct:
        ans["solved_at"] = now
        ans["first_correct"] = 1 if ans["attempts"] == 1 else 0
    else:
        wrong.append(choice)
        ans["wrong_json"] = json.dumps(sorted(set(wrong)))
    return ans, correct, True


def content_changed(old: dict, new: dict) -> bool:
    """Şıklar ya da doğru şık değiştiyse eski cevaplar geçersiz (sıfırlanır). Yalnızca metin/zaman/açıklama
    değişikliği cevapları korur."""
    return parse_options(old["options_json"]) != new["options"] or old["correct"] != new["correct"]
