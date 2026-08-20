"""
SellerSprite MCP client — Claude'suz, doğrudan backend'den bağlanır.

Bağlantı testinde (Claude Desktop üzerinden) doğrulanmış gerçek tool adları
ve davranışlar burada sabittir. Yeni bir tool eklerken önce Claude'da
tool_search ile test edip gerçek parametre/çıktı şemasını doğrula, sonra
buraya ekle — tahmin yürütme.

KRİTİK BULGU: returnFields parametresi güvenilir değil (null döndürüyor).
Bu yüzden hiçbir çağrıda returnFields KULLANMIYORUZ — tam objeyi çekip
Python tarafında filtreliyoruz.
"""
import os
import json
from contextlib import asynccontextmanager
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _get_mcp_url() -> str:
    """
    Env var'ı İSTEK ANINDA okur (import anında değil). Böylece key eksikse
    tüm uygulama çökmek yerine yalnızca MCP çağıran endpoint'ler net bir
    hata mesajıyla başarısız olur — /health, /api/thresholds gibi diğer
    her şey çalışmaya devam eder.

    ESNEKLİK (gerçek üretim hatasından öğrenildi): Kullanıcı env var'a ya
    sadece ham secret-key'i (e5e08f9d...) ya da SellerSprite'ın kendi
    sitesinde gösterdiği TAM URL'yi (https://mcp.sellersprite.com/mcp?
    secret-key=e5e08f9d...) yapıştırabilir. İkincisini ham key sanıp tekrar
    URL şablonuna sokarsak "https://...?secret-key=https://...?secret-key=..."
    şeklinde ÇİFT/BOZUK bir URL oluşur — sunucu buna 200 dönebilir ama kimlik
    doğrulama geçersiz kalır ve gerçek tool listesi yerine sınırlı/farklı bir
    yanıt döner (örn. "Tool X not listed by server" hatası). Bu yüzden burada
    değerin zaten tam URL olup olmadığını kontrol edip iki duruma göre
    davranıyoruz.
    """
    key = os.environ.get("SELLERSPRITE_SECRET_KEY")
    if not key:
        raise RuntimeError(
            "SELLERSPRITE_SECRET_KEY ortam değişkeni tanımlı değil. "
            "Vercel > Settings > Environment Variables'a ekleyip yeniden deploy et "
            "(env var eklemek otomatik redeploy tetiklemez)."
        )
    key = key.strip()
    if key.startswith("http://") or key.startswith("https://"):
        return key  # zaten tam URL yapıştırılmış
    return f"https://mcp.sellersprite.com/mcp?secret-key={key}"


@asynccontextmanager
async def mcp_session():
    """Her istek için kısa ömürlü bir MCP oturumu açar."""
    async with streamablehttp_client(_get_mcp_url()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call_tool(tool_name: str, arguments: dict, wrap_in_request: bool = True) -> dict:
    """
    Tek bir SellerSprite MCP tool'unu çağırır, JSON içeriğini döndürür.
    returnFields ASLA gönderilmez (bkz. modül docstring'i).

    KRİTİK: SellerSprite'ın ÇOĞU tool'u parametreleri "request" adlı bir obje
    içine sarılı bekliyor — {"request": {...}} (product_node, keyword_miner,
    market_*, competitor_lookup için gerçek çağrılarla doğrulandı).

    AMA HEPSİ DEĞİL: keyword_research_trends'in gerçek şeması DÜZ parametre
    bekliyor (request sarmalı YOK) — bu, Claude'un tool_search'ünde görülen
    şemadan ve gerçek bir çağrıdan doğrulandı. Bu tool'u eskiden hep-sarma
    varsayımıyla çağırmak isteği bozup boş sonuç döndürüyordu (panelde
    "Search Volume Trend" grafiği hep boş kalıyordu — kök sebep buydu).
    Bu yüzden çağıran taraf wrap_in_request=False geçebiliyor.
    """
    arguments = {k: v for k, v in arguments.items() if k != "returnFields"}
    async with mcp_session() as session:
        payload = {"request": arguments} if wrap_in_request else arguments
        result = await session.call_tool(tool_name, payload)
        for block in result.content:
            if hasattr(block, "text"):
                try:
                    return json.loads(block.text)
                except json.JSONDecodeError:
                    return {"raw": block.text}
        return {}


async def call_many(calls: list[tuple[str, dict]]) -> list[dict]:
    """
    Birden fazla tool çağrısını sıralı çalıştırır (aynı oturum içinde,
    bağlantı kurma maliyetini tekrarlamamak için).
    calls: [(tool_name, arguments), ...] — arguments düz dict, "request"
    sarmalı burada otomatik eklenir.
    """
    results = []
    async with mcp_session() as session:
        for tool_name, arguments in calls:
            arguments = {k: v for k, v in arguments.items() if k != "returnFields"}
            result = await session.call_tool(tool_name, {"request": arguments})
            parsed = {}
            for block in result.content:
                if hasattr(block, "text"):
                    try:
                        parsed = json.loads(block.text)
                    except json.JSONDecodeError:
                        parsed = {"raw": block.text}
                    break
            results.append(parsed)
    return results
