"""
E-POSTA (Resend) — gönderim + şablonlar. Saf modül: veritabanına dokunmaz.

Ortam değişkenleri (İSTEK ANINDA okunur — import anında değil; eksikse uygulama çökmez, e-posta kapalı sayılır):
- RESEND_API_KEY   : Resend API anahtarı (re_...)
- EMAIL_FROM       : gönderici, ör. "Hercul Panel <bildirim@alanadiniz.com>" (alan adı Resend'de doğrulanmış olmalı)
- APP_BASE_URL     : panelin adresi, ör. https://panel.alanadiniz.com — e-postadaki TÜM bağlantılar buradan kurulur.
                     İsteğin Host başlığından ASLA üretilmez (saldırgan şifre sıfırlama bağlantısını kendi sitesine
                     yönlendirebilirdi).
- EMAIL_VERIFY_MODE: strict (varsayılan — doğrulanmamış hesap paneli kullanamaz) | soft (yalnızca uyarı bandı)

Üçü de (anahtar, gönderici, adres) tanımlı değilse e-posta KAPALI: doğrulama zorunlu değildir, şifre sıfırlama
503 döner, bildirim e-postası gitmez — sistem e-postasız haliyle çalışmaya devam eder.
"""
import html
import os

import httpx

RESEND_URL = "https://api.resend.com"
TIMEOUT = 8.0
BATCH_MAX = 100   # Resend /emails/batch sınırı


class MailError(RuntimeError):
    pass


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def enabled() -> bool:
    return bool(_env("RESEND_API_KEY") and _env("EMAIL_FROM") and base_url())


def base_url() -> str:
    u = _env("APP_BASE_URL").rstrip("/")
    return u if u.startswith("https://") or u.startswith("http://localhost") else ""


def verification_enforced() -> bool:
    """Doğrulanmamış hesap paneli kullanamaz mı? Yalnızca e-posta açıkken ve mod 'soft' değilken."""
    return enabled() and _env("EMAIL_VERIFY_MODE").lower() != "soft"


def link(path: str) -> str:
    return base_url() + path


def _payload(to: str, subject: str, html_body: str, text: str, headers: dict | None = None) -> dict:
    p = {"from": _env("EMAIL_FROM"), "to": [to], "subject": subject, "html": html_body, "text": text}
    if headers:
        p["headers"] = headers
    return p


async def send(to: str, subject: str, html_body: str, text: str, headers: dict | None = None) -> str:
    """Tek e-posta. Başarısızsa MailError (çağıran karar verir: kayıtta yut, sıfırlamada logla)."""
    if not enabled():
        raise MailError("E-posta servisi yapılandırılmamış")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(f"{RESEND_URL}/emails", json=_payload(to, subject, html_body, text, headers),
                             headers={"Authorization": f"Bearer {_env('RESEND_API_KEY')}"})
    except httpx.HTTPError as e:
        raise MailError(f"Resend'e ulaşılamadı: {e}") from e
    if r.status_code >= 300:
        raise MailError(f"Resend {r.status_code}: {r.text[:300]}")
    return (r.json() or {}).get("id", "")


async def send_batch(messages: list[dict]) -> int:
    """messages: [{to, subject, html, text, headers?}] — 100'lük gruplar halinde /emails/batch. Gönderilen sayısı."""
    if not enabled() or not messages:
        return 0
    sent = 0
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        for i in range(0, len(messages), BATCH_MAX):
            chunk = [_payload(m["to"], m["subject"], m["html"], m["text"], m.get("headers")) for m in messages[i:i + BATCH_MAX]]
            try:
                r = await c.post(f"{RESEND_URL}/emails/batch", json=chunk,
                                 headers={"Authorization": f"Bearer {_env('RESEND_API_KEY')}"})
            except httpx.HTTPError as e:
                raise MailError(f"Resend'e ulaşılamadı: {e}") from e
            if r.status_code >= 300:
                raise MailError(f"Resend {r.status_code}: {r.text[:300]}")
            sent += len(chunk)
    return sent


# ---------------------------------------------------------------------------
# ŞABLONLAR — kullanıcı içeriği (başlık, alıntı, ad) her zaman html.escape'ten geçer.
# ---------------------------------------------------------------------------
E = html.escape
BRAND = "Hercul Intelligent"


def layout(title: str, body_html: str, button: tuple[str, str] | None = None, footer_html: str = "") -> str:
    btn = (f'<p style="margin:24px 0"><a href="{E(button[1])}" style="background:#005c55;color:#ffffff;'
           f'text-decoration:none;padding:11px 20px;border-radius:8px;font-weight:600;display:inline-block">'
           f'{E(button[0])}</a></p>') if button else ""
    return f"""<!doctype html><html lang="tr"><body style="margin:0;background:#f8f9ff;font-family:Inter,Segoe UI,Arial,sans-serif;color:#0b1c30">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border:1px solid #e3e9f2;border-radius:14px">
<tr><td style="padding:24px 28px 8px;font-size:13px;font-weight:700;letter-spacing:.04em;color:#005c55">{E(BRAND)}</td></tr>
<tr><td style="padding:0 28px 24px;font-size:15px;line-height:1.55">
<h1 style="font-size:19px;line-height:1.35;margin:8px 0 14px">{E(title)}</h1>{body_html}{btn}</td></tr>
<tr><td style="padding:16px 28px;border-top:1px solid #e3e9f2;font-size:12px;line-height:1.5;color:#565e74">{footer_html}</td></tr>
</table></td></tr></table></body></html>"""


def _plain(*lines: str) -> str:
    return "\n".join(l for l in lines if l is not None)


def verify_email(name: str, url: str) -> tuple[str, str, str]:
    subject = "E-posta adresini doğrula"
    body = (f"<p>Merhaba {E(name or '')},</p><p>{E(BRAND)} paneline kaydını tamamlamak için e-posta adresini doğrula. "
            f"Bağlantı 24 saat geçerlidir.</p>")
    foot = "Bu kaydı sen yapmadıysan bu e-postayı yok sayabilirsin."
    return subject, layout(subject, body, ("E-postamı doğrula", url), E(foot)), _plain(
        f"Merhaba {name},", "Kaydını tamamlamak için e-posta adresini doğrula (24 saat geçerli):", url, "", foot)


def reset_email(name: str, url: str, minutes: int) -> tuple[str, str, str]:
    subject = "Şifre sıfırlama"
    body = (f"<p>Merhaba {E(name or '')},</p><p>Şifreni sıfırlamak için aşağıdaki bağlantıyı kullan. "
            f"Bağlantı {minutes} dakika geçerlidir ve yalnızca bir kez kullanılabilir.</p>")
    foot = "Şifre sıfırlamayı sen istemediysen bu e-postayı yok say — şifren değişmez."
    return subject, layout(subject, body, ("Yeni şifre belirle", url), E(foot)), _plain(
        f"Merhaba {name},", f"Şifreni sıfırlamak için ({minutes} dakika geçerli, tek kullanımlık):", url, "", foot)


def password_changed_email(name: str, url: str) -> tuple[str, str, str]:
    subject = "Şifren değiştirildi"
    body = (f"<p>Merhaba {E(name or '')},</p><p>Hesabının şifresi az önce değiştirildi ve diğer cihazlardaki "
            f"oturumların kapatıldı.</p><p><b>Bunu sen yapmadıysan</b> hemen şifreni sıfırla ve panel yöneticine haber ver.</p>")
    return subject, layout(subject, body, ("Panele git", url)), _plain(
        f"Merhaba {name},", "Hesabının şifresi az önce değiştirildi; diğer oturumların kapatıldı.",
        "Bunu sen yapmadıysan hemen şifreni sıfırla ve panel yöneticine haber ver:", url)


def notify_footer(settings_url: str, unsub_url: str) -> tuple[str, str]:
    h = (f'Bu e-postayı bildirim ayarların nedeniyle aldın. <a href="{E(settings_url)}" style="color:#005c55">Bildirim '
         f'ayarlarını değiştir</a> · <a href="{E(unsub_url)}" style="color:#565e74">Tüm e-postaları kapat</a>')
    t = f"Bildirim ayarları: {settings_url}\nTüm e-postaları kapat: {unsub_url}"
    return h, t


def notification_email(text: str, excerpt: str, url: str, button: str, settings_url: str,
                       unsub_url: str) -> tuple[str, str, str]:
    subject = text if len(text) <= 120 else text[:117] + "…"
    body = f"<p>{E(text)}</p>" + (f'<blockquote style="margin:12px 0;padding:10px 14px;background:#eff4ff;'
                                  f'border-radius:8px;color:#3e4947">{E(excerpt)}</blockquote>' if excerpt else "")
    fh, ft = notify_footer(settings_url, unsub_url)
    return subject, layout(subject, body, (button, url), fh), _plain(text, excerpt or None, "", url, "", ft)


def digest_email(name: str, sections: list[tuple[str, list[tuple[str, str]]]], url: str, settings_url: str,
                 unsub_url: str) -> tuple[str, str, str]:
    """sections: [(bölüm başlığı, [(satır metni, bağlantı), ...])]"""
    subject = "Haftalık özet — Topluluk & Eğitim"
    parts, text = [f"<p>Merhaba {E(name or '')}, geçen haftadan öne çıkanlar:</p>"], [f"Merhaba {name}, geçen haftadan öne çıkanlar:"]
    for title, rows in sections:
        parts.append(f'<h2 style="font-size:15px;margin:20px 0 8px">{E(title)}</h2><ul style="padding-left:18px;margin:0">')
        text.append("")
        text.append(title)
        for label, href in rows:
            parts.append(f'<li style="margin:4px 0"><a href="{E(href)}" style="color:#005c55">{E(label)}</a></li>')
            text.append(f"- {label}: {href}")
        parts.append("</ul>")
    fh, ft = notify_footer(settings_url, unsub_url)
    return subject, layout(subject, "".join(parts), ("Panele git", url), fh), _plain(*text, "", url, "", ft)
