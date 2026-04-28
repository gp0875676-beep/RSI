"""
╔══════════════════════════════════════════════════════════════════╗
║     RSI EXHAUSTION + VOLUME SPIKE SIGNAL BOT  v2.0              ║
║     15M / 1H / 4H Confluence  →  Telegram Alerts               ║
║                                                                  ║
║  ✅ 100% FREE — No paid API keys needed                         ║
║     All data from Binance Public API (no account required)      ║
╚══════════════════════════════════════════════════════════════════╝

SHORT SIGNAL fires when ALL of these are true:
  1. 4H RSI hit 90+ recently     → extreme exhaustion zone reached
  2. 4H RSI now declining        → pressure releasing (3+ pts off peak)
  3. 1H RSI also declining       → intermediate trend confirming
  4. 15M RSI broke below 60      → short-term momentum breaking down
  5. Volume spike on 15M/1H      → smart money entering, not retail noise

LONG SIGNAL fires when ALL of these are true:
  1. 4H RSI hit 10- recently     → extreme exhaustion zone reached
  2. 4H RSI now rising           → pressure releasing (3+ pts off bottom)
  3. 1H RSI also rising          → intermediate trend confirming
  4. 15M RSI broke above 40      → short-term momentum breaking up
  5. Volume spike on 15M/1H      → real buying pressure confirmed

Requirements:
    pip install ccxt pandas pandas-ta requests apscheduler "python-telegram-bot>=20.0"
"""

import time
import asyncio
import requests
import pandas as pd
import pandas_ta as ta
import ccxt
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
import telegram

# ══════════════════════════════════════════════════════════════════
#  YOUR CONFIG  — only 2 things to fill in (Telegram only)
# ══════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = "8613005145:AAHFWQwgi-94BYzmLJ0_j7hvT-YgDIPa730"    # from @BotFather
TELEGRAM_CHAT_ID   = "8798763306"      # your group/channel/personal ID

# ── Trading pair ───────────────────────────────────────────────────
SYMBOL      = "BTCUSDT"    # Binance format (no slash)
RSI_PERIOD  = 14

# ── Exhaustion levels ──────────────────────────────────────────────
SHORT_EXHAUSTION_RSI  = 90   # 4H RSI must have hit THIS or higher
LONG_EXHAUSTION_RSI   = 10   # 4H RSI must have hit THIS or lower

# ── Timeframe confirmation levels ─────────────────────────────────
SHORT_15M_BREAK = 60   # 15M RSI must break BELOW this
LONG_15M_BREAK  = 40   # 15M RSI must break ABOVE this

# ── Volume spike settings ─────────────────────────────────────────
VOLUME_SPIKE_MULTIPLIER = 1.8   # current vol must be 1.8x the average
VOLUME_LOOKBACK         = 20    # candles to calculate average volume

# ── Slope detection ───────────────────────────────────────────────
SLOPE_CANDLES = 3    # RSI direction measured over this many candles
MIN_RSI_MOVE  = 3.0  # minimum RSI point change to call it a slope

# ── State reset zone (after signal fires, wait for RSI to normalize)
RESET_UPPER = 65   # after SHORT: 4H RSI must fall below this to reset
RESET_LOWER = 35   # after LONG:  4H RSI must rise above this to reset

# ── How many candles to look back for the extreme peak/bottom ─────
LOOKBACK_CANDLES = 12   # 12 × 4H = 48 hours window

# ══════════════════════════════════════════════════════════════════
#  BINANCE PUBLIC API ENDPOINTS  (no key, no account needed)
# ══════════════════════════════════════════════════════════════════

BINANCE_FAPI = "https://fapi.binance.com"   # Futures public API

def _fapi(path, params=None):
    """Call Binance Futures public REST endpoint."""
    try:
        r = requests.get(f"{BINANCE_FAPI}{path}", params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [Binance API] {path} → {e}")
        return None

# ── Free data functions ────────────────────────────────────────────

def get_funding_rate(symbol=SYMBOL):
    """
    Latest funding rate — Binance Futures, no key needed.
    GET /fapi/v1/premiumIndex
    """
    data = _fapi("/fapi/v1/premiumIndex", {"symbol": symbol})
    if data and "lastFundingRate" in data:
        return round(float(data["lastFundingRate"]) * 100, 5)
    return None

def get_open_interest(symbol=SYMBOL):
    """
    Current open interest in USD — Binance Futures, no key needed.
    GET /fapi/v1/openInterest
    """
    data = _fapi("/fapi/v1/openInterest", {"symbol": symbol})
    if data and "openInterestValue" in data:
        # openInterestValue is in USDT
        return float(data["openInterestValue"])
    # fallback: openInterest (in coin) × price
    if data and "openInterest" in data:
        price = get_mark_price(symbol)
        if price:
            return float(data["openInterest"]) * price
    return None

def get_mark_price(symbol=SYMBOL):
    """Current mark price — no key needed."""
    data = _fapi("/fapi/v1/premiumIndex", {"symbol": symbol})
    if data and "markPrice" in data:
        return float(data["markPrice"])
    return None

def get_long_short_ratio(symbol=SYMBOL, period="1h"):
    """
    Top trader long/short ratio — Binance Futures, no key needed.
    GET /futures/data/topLongShortPositionRatio
    period: "5m","15m","30m","1h","2h","4h","6h","12h","1d"
    """
    data = _fapi(
        "/futures/data/topLongShortPositionRatio",
        {"symbol": symbol, "period": period, "limit": 1}
    )
    if data and isinstance(data, list) and len(data) > 0:
        return float(data[0].get("longShortRatio", 0))
    return None

def get_taker_buy_sell_volume(symbol=SYMBOL, period="1h"):
    """
    Taker buy vs sell volume — shows aggressive buying/selling pressure.
    GET /futures/data/takerlongshortRatio  — no key needed.
    """
    data = _fapi(
        "/futures/data/takerlongshortRatio",
        {"symbol": symbol, "period": period, "limit": 1}
    )
    if data and isinstance(data, list) and len(data) > 0:
        buy_vol  = float(data[0].get("buyVol", 0))
        sell_vol = float(data[0].get("sellVol", 0))
        return buy_vol, sell_vol
    return None, None

def get_liquidations_approx(symbol=SYMBOL):
    """
    Binance doesn't expose raw liq data publicly via REST.
    We estimate using OI change + price move as a proxy.
    Returns a simple sentiment string.
    """
    # Use OI history to detect sudden OI drop (= mass liquidation)
    data = _fapi(
        "/futures/data/openInterestHist",
        {"symbol": symbol, "period": "15m", "limit": 3}
    )
    if data and isinstance(data, list) and len(data) >= 2:
        oi_now  = float(data[-1].get("sumOpenInterestValue", 0))
        oi_prev = float(data[-2].get("sumOpenInterestValue", 0))
        if oi_prev > 0:
            oi_change_pct = ((oi_now - oi_prev) / oi_prev) * 100
            if oi_change_pct < -2:
                return f"⚡ OI dropped {oi_change_pct:.1f}% — possible liquidation cascade"
            elif oi_change_pct > 2:
                return f"📈 OI surged +{oi_change_pct:.1f}% — new positions opening"
    return None


# ══════════════════════════════════════════════════════════════════
#  OHLCV + RSI  (via ccxt — uses Binance public market data)
# ══════════════════════════════════════════════════════════════════

_exchange = None

def get_exchange():
    global _exchange
    if _exchange is None:
        # ccxt Binance — public endpoints, no API key needed
        _exchange = ccxt.binance({
            "options": {"defaultType": "future"}   # futures market
        })
    return _exchange

def fetch_ohlcv(timeframe, limit=100):
    """Fetch candles from Binance Futures (public, no key)."""
    ex = get_exchange()
    raw = ex.fetch_ohlcv(
        SYMBOL.replace("USDT", "/USDT"),  # ccxt format: BTC/USDT
        timeframe,
        limit=limit
    )
    df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    return df

def add_rsi(df):
    df = df.copy()
    df["rsi"] = ta.rsi(df["close"], length=RSI_PERIOD)
    return df.dropna()


# ══════════════════════════════════════════════════════════════════
#  VOLUME SPIKE DETECTION
# ══════════════════════════════════════════════════════════════════

def detect_volume_spike(df, multiplier=VOLUME_SPIKE_MULTIPLIER, lookback=VOLUME_LOOKBACK):
    """
    Returns (is_spike: bool, current_vol, avg_vol, ratio).
    A spike = current candle volume > multiplier × average of last N candles.
    """
    if len(df) < lookback + 2:
        return False, 0, 0, 0

    # Use all candles except the very last (may be forming)
    recent    = df["volume"].iloc[-(lookback+1):-1]
    avg_vol   = recent.mean()
    curr_vol  = df["volume"].iloc[-1]

    ratio     = curr_vol / avg_vol if avg_vol > 0 else 0
    is_spike  = ratio >= multiplier

    return is_spike, round(curr_vol, 2), round(avg_vol, 2), round(ratio, 2)

def volume_spike_summary(df_15m, df_1h):
    """Check volume spike on both 15M and 1H."""
    spike_15m, cvol_15m, avg_15m, ratio_15m = detect_volume_spike(df_15m)
    spike_1h,  cvol_1h,  avg_1h,  ratio_1h  = detect_volume_spike(df_1h)

    # Pass if at least ONE timeframe shows a spike
    either_spike = spike_15m or spike_1h

    details = {
        "15m_spike":  spike_15m,
        "15m_ratio":  ratio_15m,
        "1h_spike":   spike_1h,
        "1h_ratio":   ratio_1h,
        "any_spike":  either_spike,
    }
    return either_spike, details


# ══════════════════════════════════════════════════════════════════
#  RSI SLOPE HELPERS
# ══════════════════════════════════════════════════════════════════

def slope(df, n=SLOPE_CANDLES):
    vals = df["rsi"].iloc[-n:].tolist()
    diff = vals[-1] - vals[0]
    if diff > MIN_RSI_MOVE:
        return "rising"
    elif diff < -MIN_RSI_MOVE:
        return "declining"
    return "flat"

def rsi_now(df):
    return round(df["rsi"].iloc[-1], 2)

def rsi_prev(df):
    return round(df["rsi"].iloc[-2], 2)

def rsi_peak(df, n=LOOKBACK_CANDLES):
    return round(df["rsi"].iloc[-n:].max(), 2)

def rsi_bottom(df, n=LOOKBACK_CANDLES):
    return round(df["rsi"].iloc[-n:].min(), 2)


# ══════════════════════════════════════════════════════════════════
#  SIGNAL CONDITIONS
# ══════════════════════════════════════════════════════════════════

def check_short(tf):
    df4  = tf["4h"]
    df1  = tf["1h"]
    df15 = tf["15m"]

    r4      = rsi_now(df4)
    pk4     = rsi_peak(df4)
    slp4    = slope(df4)
    came_dn = (pk4 - r4) >= MIN_RSI_MOVE

    r1   = rsi_now(df1)
    slp1 = slope(df1)

    r15      = rsi_now(df15)
    pr15     = rsi_prev(df15)
    slp15    = slope(df15)

    # 15M break: was above level, now below OR already below and declining
    break15  = (pr15 >= SHORT_15M_BREAK and r15 < SHORT_15M_BREAK) or \
               (r15 < SHORT_15M_BREAK and slp15 == "declining")

    vol_ok, vol_info = volume_spike_summary(df15, df1)

    conds = {
        "exhaustion":  pk4 >= SHORT_EXHAUSTION_RSI,
        "4h_decline":  slp4 == "declining" and came_dn,
        "1h_decline":  slp1 == "declining",
        "15m_break":   break15,
        "vol_spike":   vol_ok,
    }
    details = {
        **conds,
        "peak_4h": pk4, "rsi_4h": r4, "slope_4h": slp4,
        "rsi_1h":  r1,  "slope_1h": slp1,
        "rsi_15m": r15, "slope_15m": slp15,
        "vol":     vol_info,
    }
    fired = all(conds.values())
    return fired, details

def check_long(tf):
    df4  = tf["4h"]
    df1  = tf["1h"]
    df15 = tf["15m"]

    r4      = rsi_now(df4)
    bt4     = rsi_bottom(df4)
    slp4    = slope(df4)
    came_up = (r4 - bt4) >= MIN_RSI_MOVE

    r1   = rsi_now(df1)
    slp1 = slope(df1)

    r15      = rsi_now(df15)
    pr15     = rsi_prev(df15)
    slp15    = slope(df15)

    break15  = (pr15 <= LONG_15M_BREAK and r15 > LONG_15M_BREAK) or \
               (r15 > LONG_15M_BREAK and slp15 == "rising")

    vol_ok, vol_info = volume_spike_summary(df15, df1)

    conds = {
        "exhaustion": bt4 <= LONG_EXHAUSTION_RSI,
        "4h_rise":    slp4 == "rising" and came_up,
        "1h_rise":    slp1 == "rising",
        "15m_break":  break15,
        "vol_spike":  vol_ok,
    }
    details = {
        **conds,
        "bottom_4h": bt4, "rsi_4h": r4, "slope_4h": slp4,
        "rsi_1h":   r1,   "slope_1h": slp1,
        "rsi_15m":  r15,  "slope_15m": slp15,
        "vol":      vol_info,
    }
    fired = all(conds.values())
    return fired, details


# ══════════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════════════════════
state = {
    "setup":        None,   # 'SHORT' | 'LONG' | None
    "signal_fired": False,
    "extreme_val":  None,
}

def reset_state():
    global state
    state = {"setup": None, "signal_fired": False, "extreme_val": None}

def check_reset(tf):
    r4 = rsi_now(tf["4h"])
    if state["setup"] == "SHORT" and r4 <= RESET_UPPER:
        print(f"  [RESET] RSI={r4} normalised ↓ {RESET_UPPER} — SHORT state cleared")
        reset_state()
    elif state["setup"] == "LONG" and r4 >= RESET_LOWER:
        print(f"  [RESET] RSI={r4} normalised ↑ {RESET_LOWER} — LONG state cleared")
        reset_state()


# ══════════════════════════════════════════════════════════════════
#  TELEGRAM MESSAGE
# ══════════════════════════════════════════════════════════════════

def _slope_icon(s):
    return {"rising": "↑", "declining": "↓", "flat": "→"}.get(s, "?")

def _cond_icon(b):
    return "✅" if b else "❌"

def build_message(sig_type, details, price,
                  funding, oi, ls_ratio,
                  buy_vol, sell_vol, liq_note):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    vol = details["vol"]

    if sig_type == "SHORT":
        hdr     = "🔴 *SHORT SIGNAL — RSI EXHAUSTION*"
        extreme = f"4H RSI peak: `{details['peak_4h']}` ≥ {SHORT_EXHAUSTION_RSI} {_cond_icon(details['exhaustion'])}"
        entry_hint = "⚠️ *Consider short entry on retest of breakdown level*"
    else:
        hdr     = "🟢 *LONG SIGNAL — RSI EXHAUSTION*"
        extreme = f"4H RSI bottom: `{details['bottom_4h']}` ≤ {LONG_EXHAUSTION_RSI} {_cond_icon(details['exhaustion'])}"
        entry_hint = "⚠️ *Consider long entry on retest of breakout level*"

    # RSI grid
    rsi_grid = (
        f"```\n"
        f"TF   RSI    Slope  Cond\n"
        f"─────────────────────────\n"
        f"4H   {details['rsi_4h']:<6} {_slope_icon(details['slope_4h'])}      {_cond_icon(details.get('4h_decline') or details.get('4h_rise'))}\n"
        f"1H   {details['rsi_1h']:<6} {_slope_icon(details['slope_1h'])}      {_cond_icon(details.get('1h_decline') or details.get('1h_rise'))}\n"
        f"15M  {details['rsi_15m']:<6} {_slope_icon(details['slope_15m'])}      {_cond_icon(details['15m_break'])}\n"
        f"```"
    )

    # Volume block
    vol_15m = f"`{vol['15m_ratio']}x` {'🔥' if vol['15m_spike'] else '—'}"
    vol_1h  = f"`{vol['1h_ratio']}x`  {'🔥' if vol['1h_spike'] else '—'}"
    vol_block = (
        f"📊 *Volume Spike* {_cond_icon(vol['any_spike'])}\n"
        f"  15M: {vol_15m}   1H: {vol_1h}"
    )

    # Market data block
    market_lines = []
    if price:
        market_lines.append(f"💵 Price: `${price:,.2f}`")
    if funding is not None:
        sent = "🐂 long bias" if funding > 0 else "🐻 short bias"
        market_lines.append(f"💰 Funding: `{funding:+.4f}%` ({sent})")
    if oi:
        market_lines.append(f"📦 Open Interest: `${oi/1e9:.2f}B`")
    if ls_ratio:
        market_lines.append(f"⚖️ Long/Short ratio: `{ls_ratio:.2f}`")
    if buy_vol and sell_vol and (buy_vol + sell_vol) > 0:
        buy_pct = buy_vol / (buy_vol + sell_vol) * 100
        market_lines.append(f"🤜 Taker buy: `{buy_pct:.1f}%` | sell: `{100-buy_pct:.1f}%`")
    if liq_note:
        market_lines.append(f"{liq_note}")

    market_block = "\n".join(market_lines) if market_lines else ""

    msg = "\n".join(filter(None, [
        hdr,
        f"📌 *{SYMBOL} — 15M + 1H + 4H Confluence*\n",
        f"*Exhaustion confirmed:*",
        extreme + "\n",
        f"*RSI breakdown across timeframes:*",
        rsi_grid,
        vol_block + "\n",
        f"*Live market data:*",
        market_block + "\n",
        entry_hint,
        f"\n🕐 `{now}`",
        f"_Not financial advice — DYOR_",
    ]))
    return msg


# ══════════════════════════════════════════════════════════════════
#  TELEGRAM SENDER
# ══════════════════════════════════════════════════════════════════

async def _tg_send(msg):
    bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=msg,
        parse_mode="Markdown"
    )

def send_telegram(msg):
    asyncio.run(_tg_send(msg))
    print(f"  [TG ✅] Message sent ({len(msg)} chars)")


# ══════════════════════════════════════════════════════════════════
#  MAIN SCAN JOB
# ══════════════════════════════════════════════════════════════════

def run_scan():
    global state
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"\n[{ts}] ─── Scanning {SYMBOL} ───")

    try:
        # 1. Fetch all timeframes
        tf = {}
        for t in ["15m", "1h", "4h"]:
            raw = fetch_ohlcv(t, limit=120)
            tf[t] = add_rsi(raw)

        r4  = rsi_now(tf["4h"])
        pk4 = rsi_peak(tf["4h"])
        bt4 = rsi_bottom(tf["4h"])

        print(f"  4H RSI={r4} | peak48h={pk4} | bottom48h={bt4} | setup={state['setup']} | fired={state['signal_fired']}")

        # 2. If signal already fired — just watch for reset
        if state["signal_fired"]:
            check_reset(tf)
            return

        # 3. Detect which setup we're in (if not already set)
        if state["setup"] is None:
            if pk4 >= SHORT_EXHAUSTION_RSI:
                state["setup"]       = "SHORT"
                state["extreme_val"] = pk4
                print(f"  ⚡ SHORT exhaustion detected — 4H peak RSI = {pk4}")
            elif bt4 <= LONG_EXHAUSTION_RSI:
                state["setup"]       = "LONG"
                state["extreme_val"] = bt4
                print(f"  ⚡ LONG exhaustion detected — 4H bottom RSI = {bt4}")
            else:
                print(f"  No extreme setup active (need ≥{SHORT_EXHAUSTION_RSI} or ≤{LONG_EXHAUSTION_RSI})")
                return

        # 4. Check confluence + fire
        if state["setup"] == "SHORT":
            fired, details = check_short(tf)
            c = details
            print(f"  SHORT checks: exhaust={_cond_icon(c['exhaustion'])} "
                  f"4H↓={_cond_icon(c['4h_decline'])} "
                  f"1H↓={_cond_icon(c['1h_decline'])} "
                  f"15M={_cond_icon(c['15m_break'])} "
                  f"VOL={_cond_icon(c['vol']['any_spike'])}")

        elif state["setup"] == "LONG":
            fired, details = check_long(tf)
            c = details
            print(f"  LONG  checks: exhaust={_cond_icon(c['exhaustion'])} "
                  f"4H↑={_cond_icon(c['4h_rise'])} "
                  f"1H↑={_cond_icon(c['1h_rise'])} "
                  f"15M={_cond_icon(c['15m_break'])} "
                  f"VOL={_cond_icon(c['vol']['any_spike'])}")
        else:
            return

        if fired:
            print(f"  🎯 ALL CONDITIONS MET — fetching market data & sending signal...")

            # Fetch all free market data
            price      = get_mark_price()
            funding    = get_funding_rate()
            oi         = get_open_interest()
            ls_ratio   = get_long_short_ratio(period="1h")
            buy_vol, sell_vol = get_taker_buy_sell_volume(period="1h")
            liq_note   = get_liquidations_approx()

            msg = build_message(
                state["setup"], details, price,
                funding, oi, ls_ratio,
                buy_vol, sell_vol, liq_note
            )
            send_telegram(msg)
            state["signal_fired"] = True

        else:
            # Tell user which condition is still pending
            pending = [k for k, v in c.items()
                       if isinstance(v, bool) and not v
                       and k not in ("vol",)]
            if details["vol"]["any_spike"] is False:
                pending.append("vol_spike")
            print(f"  Waiting for: {', '.join(pending) or 'all met but not triggered'}")

    except Exception as e:
        import traceback
        print(f"  ❌ ERROR: {e}")
        traceback.print_exc()


# ══════════════════════════════════════════════════════════════════
#  SCHEDULER  — every 15 minutes
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║   RSI EXHAUSTION + VOLUME SPIKE BOT  v2.0  starting...     ║
╠══════════════════════════════════════════════════════════════╣""")
    print(f"║  Symbol     : {SYMBOL:<44} ║")
    print(f"║  SHORT zone : 4H RSI ≥ {SHORT_EXHAUSTION_RSI} then exhaust + 3-TF breakdown  ║")
    print(f"║  LONG zone  : 4H RSI ≤ {LONG_EXHAUSTION_RSI} then exhaust + 3-TF breakout   ║")
    print(f"║  Vol spike  : {VOLUME_SPIKE_MULTIPLIER}x average volume on 15M or 1H          ║")
    print(f"║  Scan every : 15 minutes                                    ║")
    print(f"║  Data source: Binance Public API (FREE, no key needed)      ║")
    print( "╚══════════════════════════════════════════════════════════════╝\n")

    # Immediate run on startup
    run_scan()

    # Schedule every 15 minutes (1 min after candle close)
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_scan,
        trigger="cron",
        minute="1,16,31,46",
        second=0,
    )

    print("\n📅 Scheduler active — next run at :01, :16, :31, or :46 past the hour")
    print("   Press Ctrl+C to stop\n")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped cleanly")
