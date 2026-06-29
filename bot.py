#!/usr/bin/env python3
"""
NFT-Style Telegram Crypto Price Bot
Card  : Holographic NFT Trading Card
Chart : Binance 1D Klines  →  CoinGecko fallback
Data  : CoinGecko API
QR    : t.me/HalucigeniaLtd
"""

import os, io, re, time, logging, random, math, colorsys
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from PIL import Image, ImageDraw, ImageFont
import qrcode as qrlib

# ══════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════
BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
COMMUNITY_TAG = os.getenv("COMMUNITY_TAG", "Haluexebot")
TELEGRAM_URL  = os.getenv("TELEGRAM_URL", "https://t.me/HalucigeniaLtd")
CG_BASE       = "https://api.coingecko.com/api/v3"
BINANCE_BASE  = "https://api.binance.com/api/v3"
CACHE_TTL     = 60

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════
#  CACHE
# ══════════════════════════════════════════
_cache: dict = {}

def cache_get(key):
    e = _cache.get(key)
    return e["v"] if e and time.time() - e["t"] < CACHE_TTL else None

def cache_set(key, v):
    _cache[key] = {"v": v, "t": time.time()}

# ══════════════════════════════════════════
#  PRE-GENERATE QR CODE (at startup)
# ══════════════════════════════════════════
def gen_qr(url: str) -> Image.Image:
    qr = qrlib.QRCode(
        version=1,
        error_correction=qrlib.constants.ERROR_CORRECT_M,
        box_size=7, border=1
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="#0D0D2B", back_color="white").convert("RGB")

QR_CODE = gen_qr(TELEGRAM_URL)

# ══════════════════════════════════════════
#  COINGECKO API
# ══════════════════════════════════════════
async def cg_search(q: str) -> list:
    if (c := cache_get(f"s:{q}")) is not None:
        return c
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{CG_BASE}/search",
                             params={"query": q},
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    coins = (await r.json()).get("coins", [])[:9]
                    cache_set(f"s:{q}", coins)
                    return coins
    except Exception as e:
        log.error(f"cg_search: {e}")
    return []

async def cg_price(coin_id: str) -> dict | None:
    if (c := cache_get(f"p:{coin_id}")) is not None:
        return c
    try:
        async with aiohttp.ClientSession() as s:
            # Market data USD
            async with s.get(f"{CG_BASE}/coins/markets",
                             params={"vs_currency": "usd", "ids": coin_id,
                                     "price_change_percentage": "24h"},
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200 or not (d := await r.json()):
                    return None
                coin = d[0]

            # IDR price
            async with s.get(f"{CG_BASE}/simple/price",
                             params={"ids": coin_id, "vs_currencies": "idr"},
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                coin["idr"] = (await r.json()).get(coin_id, {}).get("idr", 0) \
                              if r.status == 200 else 0

            # Large logo URL
            async with s.get(f"{CG_BASE}/coins/{coin_id}",
                             params={"localization": "false", "tickers": "false",
                                     "market_data": "false", "community_data": "false",
                                     "developer_data": "false"},
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    d2 = await r.json()
                    coin["logo"] = d2.get("image", {}).get("large", coin.get("image", ""))
                else:
                    coin["logo"] = coin.get("image", "")

            cache_set(f"p:{coin_id}", coin)
            return coin
    except Exception as e:
        log.error(f"cg_price: {e}")
    return None

# ══════════════════════════════════════════
#  BINANCE CHART + COINGECKO FALLBACK
# ══════════════════════════════════════════
async def fetch_chart(symbol: str, coin_id: str) -> list | None:
    """Binance 1D klines first, CoinGecko 30-day sparkline as fallback."""
    sym = symbol.upper()
    for quote in ["USDT", "BTC", "ETH", "BNB"]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{BINANCE_BASE}/klines",
                    params={"symbol": f"{sym}{quote}", "interval": "1d", "limit": 30},
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        if isinstance(data, list) and len(data) >= 3:
                            closes = [float(k[4]) for k in data]
                            log.info(f"Chart: Binance {sym}{quote} ({len(closes)} candles)")
                            return closes
        except:
            pass

    # CoinGecko fallback
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{CG_BASE}/coins/{coin_id}/market_chart",
                params={"vs_currency": "usd", "days": "30"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status == 200:
                    raw    = (await r.json()).get("prices", [])
                    prices = [p[1] for p in raw]
                    if len(prices) > 30:
                        step   = max(1, len(prices) // 30)
                        prices = prices[::step][:30]
                    if prices:
                        log.info(f"Chart: CoinGecko fallback {coin_id} ({len(prices)} pts)")
                        return prices
    except:
        pass

    return None

async def fetch_logo(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=7)) as r:
                if r.status == 200:
                    return Image.open(io.BytesIO(await r.read())).convert("RGBA")
    except:
        pass
    return None

# ══════════════════════════════════════════
#  FORMATTERS
# ══════════════════════════════════════════
def fmt_usd(n) -> str:
    if not n: return "$0"
    n = float(n)
    if n >= 10:     return f"${n:,.2f}"   # $107,543.21
    if n >= 0.001:  return f"${n:.4f}"   # $0.9985  /  $4.2312
    return f"${n:.8f}"                    # $0.00000123

def fmt_big(n) -> str:
    if not n: return "$0"
    n = float(n)
    if n >= 1e12: return f"${n/1e12:.2f}T"
    if n >= 1e9:  return f"${n/1e9:.2f}B"
    if n >= 1e6:  return f"${n/1e6:.2f}M"
    return f"${n/1e3:.2f}K"

def fmt_idr(n) -> str:
    """Indonesian style: period as thousand separator → 17.812 IDR"""
    if not n: return "0 IDR"
    n = float(n)
    if n >= 1e9:   return f"{n/1e9:.3f}B IDR"
    if n >= 1e6:   return f"{n/1e6:.3f}M IDR"
    if n >= 1_000: return f"{int(n):,}".replace(",", ".") + " IDR"
    return f"{n:.2f} IDR"

# ══════════════════════════════════════════
#  FONT LOADER
# ══════════════════════════════════════════
def lf(bold: bool, size: int) -> ImageFont.ImageFont:
    for p in (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
              if bold else
              ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]):
        try: return ImageFont.truetype(p, size)
        except: pass
    return ImageFont.load_default()

# ══════════════════════════════════════════
#  NFT CARD RENDERER
# ══════════════════════════════════════════
async def make_nft_card(coin: dict, prices: list | None) -> io.BytesIO:
    W, H    = 600, 880
    BORDER  = 26
    OUTER_R = 46
    INNER_R = 26

    img  = Image.new("RGB", (W, H), "#080808")
    draw = ImageDraw.Draw(img)

    # ── Smooth rainbow border ──────────────────────────────────────────────
    # Single clean hue sweep: magenta → purple → blue → cyan → green → yellow → red
    N = BORDER + 8
    for i in range(N):
        t   = i / N
        hue = (0.83 + t) % 1.0  # starts at magenta, one full lap
        r, g, b = colorsys.hsv_to_rgb(hue, 0.92, 0.96)
        draw.rounded_rectangle(
            [i, i, W - i - 1, H - i - 1],
            radius=max(OUTER_R - i // 2, 6),
            fill=(int(r*255), int(g*255), int(b*255))
        )

    # ── Panel bounds ───────────────────────────────────────────────────────
    IX1, IY1 = BORDER, BORDER
    IX2, IY2 = W - BORDER, H - BORDER
    IH = IY2 - IY1

    UP_H  = int(IH * 0.57)
    UP_Y2 = IY1 + UP_H
    GAP   = 7
    LP_Y1 = UP_Y2 + GAP
    LP_Y2 = IY2

    # Upper panel: steel-blue
    draw.rounded_rectangle([IX1, IY1, IX2, UP_Y2],
                           radius=INNER_R, fill=(180, 203, 230))
    # Lower panel: lavender-white
    draw.rounded_rectangle([IX1, LP_Y1, IX2, LP_Y2],
                           radius=INNER_R, fill=(237, 233, 253))

    # ── Coin logo ──────────────────────────────────────────────────────────
    SZ = 90
    LX, LY = IX1 + 18, IY1 + 18
    logo   = await fetch_logo(coin.get("logo", ""))
    if logo:
        lo   = logo.resize((SZ, SZ), Image.LANCZOS)
        mask = Image.new("L", (SZ, SZ), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, SZ, SZ], fill=255)
        img.paste(Image.new("RGB", (SZ, SZ), "white"), (LX, LY), mask)
        img.paste(lo.convert("RGB"),                    (LX, LY), mask)
    else:
        draw.ellipse([LX, LY, LX+SZ, LY+SZ], fill="white", outline="#C0C8D8", width=2)
        draw.text((LX+SZ//2, LY+SZ//2),
                  coin.get("symbol","?")[:4].upper(),
                  font=lf(True, 17), fill="#1A1A3A", anchor="mm")

    # ── QR code ────────────────────────────────────────────────────────────
    QR_SZ = 90
    QR_X, QR_Y = IX2 - QR_SZ - 16, IY1 + 16
    qr    = QR_CODE.resize((QR_SZ, QR_SZ), Image.LANCZOS)
    P = 5
    draw.rectangle([QR_X-P, QR_Y-P, QR_X+QR_SZ+P, QR_Y+QR_SZ+P], fill="white")
    img.paste(qr, (QR_X, QR_Y))

    # ── Chart (Binance 1D) ──────────────────────────────────────────────────
    CP  = 22
    CY1 = max(LY + SZ, QR_Y + QR_SZ) + 20
    CX1, CX2 = IX1 + CP, IX2 - CP
    CY2 = UP_Y2 - 16
    CW, CH = CX2 - CX1, CY2 - CY1

    if prices and len(prices) >= 2 and CH > 30:
        mn, mx = min(prices), max(prices)
        rng = mx - mn
        if rng == 0: rng = max(abs(mn) * 0.01, 1e-12)
        pad = rng * 0.14
        mn -= pad; mx += pad; rng = mx - mn

        n   = len(prices)
        pts = [(int(CX1 + (i/(n-1))*CW),
                int(max(CY1, min(CY2, CY2 - ((p-mn)/rng)*CH))))
               for i, p in enumerate(prices)]

        up   = prices[-1] >= prices[0]
        LINE = (200, 45, 45) if not up else (40, 185, 85)

        if len(pts) >= 2:
            # Subtle glow: use slightly dimmed wide line + crisp thin line
            DIM = (LINE[0]//3, LINE[1]//3, LINE[2]//3)
            draw.line(pts, fill=DIM,  width=8)
            draw.line(pts, fill=LINE, width=3)
    else:
        cx = (CX1+CX2)//2
        cy = (CY1+CY2)//2 if CY2 > CY1 else IY1 + 280
        draw.text((cx, cy), "Chart not available",
                  font=lf(False, 15), fill=(95, 115, 135), anchor="mm")

    # ── Lower panel text ───────────────────────────────────────────────────
    F = {
        "qty":   lf(True,  44),
        "label": lf(False, 15),
        "price": lf(True,  38),
        "idr":   lf(True,  34),
        "stat":  lf(True,  26),
        "sl":    lf(False, 14),
    }
    DARK = "#18183A"
    LBLC = "#4444BC"
    TX   = IX1 + 24
    TY   = LP_Y1 + 20

    sym = coin.get("symbol", "?").upper()

    draw.text((TX, TY), f"1 {sym}", font=F["qty"], fill=DARK)
    TY += 54

    draw.text((TX, TY), "PRICE", font=F["label"], fill=LBLC)
    TY += 20
    draw.text((TX, TY), fmt_usd(coin.get("current_price", 0)), font=F["price"], fill=DARK)
    TY += 48

    draw.text((TX, TY), "VALUE (IDR)", font=F["label"], fill=LBLC)
    TY += 20
    draw.text((TX, TY), fmt_idr(coin.get("idr", 0)), font=F["idr"], fill=DARK)
    TY += 46

    # Divider line
    DY = TY + 10
    draw.rectangle([IX1+14, DY, IX2-14, DY+1], fill=(172, 162, 208))
    TY = DY + 14

    # Stats 2-column: MARKET CAPS | VOL
    MID = IX1 + (IX2 - IX1) // 2
    RX  = MID + 16
    draw.text((TX, TY), "MARKET CAPS", font=F["sl"], fill=LBLC)
    draw.text((RX, TY), "VOL",          font=F["sl"], fill=LBLC)
    TY += 19
    draw.text((TX, TY), fmt_big(coin.get("market_cap",   0)), font=F["stat"], fill=DARK)
    draw.text((RX, TY), fmt_big(coin.get("total_volume", 0)), font=F["stat"], fill=DARK)

    # Vertical separator in stats area
    draw.rectangle([MID, DY+5, MID+1, LP_Y2-14], fill=(172, 162, 208))

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.seek(0)
    return buf

# ══════════════════════════════════════════
#  QUERY PARSER
# ══════════════════════════════════════════
def parse_query(text: str) -> str | None:
    t = text.strip().lower()
    t = re.sub(r"^\$", "", t)
    t = re.sub(r"^[\d.,]+\s*", "", t)
    t = t.strip()
    if not t or len(t.split()) > 4:
        return None
    return t

# ══════════════════════════════════════════
#  SEND PRICE CARD
# ══════════════════════════════════════════
async def send_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                     coin_id: str, loading_msg):
    try:
        await loading_msg.edit_text("⏳ Generating NFT card...")
    except:
        pass

    coin = await cg_price(coin_id)
    if not coin:
        await loading_msg.edit_text("❌ Gagal ambil data. Coba lagi nanti.")
        return

    sym    = coin.get("symbol", "?").upper()
    prices = await fetch_chart(sym, coin_id)
    card   = await make_nft_card(coin, prices)

    caption = (
        f"🃏 *{coin['name']}* ({sym})  •  #{coin.get('market_cap_rank','?')}\n"
        f"💵 {fmt_usd(coin['current_price'])}  |  🇮🇩 {fmt_idr(coin.get('idr',0))}"
    )
    await ctx.bot.send_photo(update.effective_chat.id,
                              photo=card, caption=caption, parse_mode="Markdown")
    try:
        await loading_msg.delete()
    except:
        pass

# ══════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════
HELP_TEXT = (
    "🃏 *NFT Crypto Price Bot*\n\n"
    "Kirim nama coin atau ticker:\n\n"
    "• `btc` / `bitcoin`\n"
    "• `1 usdt`\n"
    "• `$SOL`\n"
    "• `KNIGHT` ← muncul semua kalau ada banyak token\n\n"
    "📊 Chart: Binance 1D  •  💰 Data: CoinGecko"
)

async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(HELP_TEXT, parse_mode="Markdown")

async def cmd_help(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(HELP_TEXT, parse_mode="Markdown")

# ══════════════════════════════════════════
#  MESSAGE HANDLER
# ══════════════════════════════════════════
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = parse_query(update.message.text or "")
    if not q:
        return

    loading = await update.message.reply_text("🔍 Mencari...")
    results = await cg_search(q)

    if not results:
        await loading.edit_text(
            f"❌ Token *{q.upper()}* tidak ditemukan.",
            parse_mode="Markdown"
        )
        return

    if len(results) == 1:
        await send_price(update, ctx, results[0]["id"], loading)
        return

    kb = []
    for coin in results:
        rank  = f"  #{coin['market_cap_rank']}" if coin.get("market_cap_rank") else ""
        label = f"{coin['symbol'].upper()} — {coin['name']}{rank}"
        kb.append([InlineKeyboardButton(label, callback_data=f"p:{coin['id'][:58]}")])

    await loading.edit_text(
        f"🔎 *{len(results)}* token untuk *{q.upper()}*:\nPilih 👇",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

# ══════════════════════════════════════════
#  CALLBACK HANDLER
# ══════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data.startswith("p:"):
        await send_price(update, ctx, q.data[2:], q.message)

# ══════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════
def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN belum di-set!")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(CallbackQueryHandler(on_callback))
    log.info(f"🃏 NFT Price Bot | @{COMMUNITY_TAG}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
