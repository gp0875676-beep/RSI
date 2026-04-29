"""
╔══════════════════════════════════════════════════════════════════╗
║     RSI EXHAUSTION + VOLUME SPIKE SIGNAL BOT  v2.0              ║
║     15M / 1H / 4H Confluence  →  Telegram Alerts               ║
║     OHLCV: yfinance (no geo-block) + Bybit market data         ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import asyncio
import requests
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
import telegram

# ══════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "8798763306")

SYMBOL      = "BTC-USD"    # yfinance format
SYMBOL_DISPLAY = "BTCUSDT"
RSI_PERIOD  = 14

SHORT_EXHAUSTION_RSI = 90
LONG_EXHAUSTION_RSI  = 10

SHORT_15M_BREAK = 60
LONG_15M_BREAK  = 40

VOLUME_SPIKE_MULTIPLIER = 1.8
VOLUME_LOOKBACK         = 20

SLOPE_CANDLES = 3
MIN_RSI_MOVE  = 3.0

RESET_UPPER = 65
RESET_LOWER = 35

LOOKBACK_CANDLES = 12

# ══════════════════════════════════════════════════════════════════
#  OHLCV via yfinance  (works from any server, no geo-block)
# ══════════════════════════════════════════════════════════════════

YF_INTERVAL = {
    "15m": ("15m",  "7d"),   # interval, period
    "1h":  ("1h",  "30d"),
    "4h":  ("1h",  "60d"),   # yfinance has no 4h; we resample from 1h
}

def fetch_ohlcv(timeframe, limit=120):
    """Fetch candles via yfinance — no geo-restrictions."""
    if timeframe == "4h":
        # Download 1h candles and resample to 4h
        df = yf.download(SYMBOL, interval="1h", period="60d",
                         auto_adjust=True, progress=False)
        if df.empty:
            raise Exception("yfinance returned empty data for 4h (1h source)")
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.columns = ["open", "high", "low", "close", "volume"]
        df = df.resample("4h").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }).dropna()
    else:
        interval, period = YF_INTERVAL[timeframe]
        df = yf.download(SYMBOL, interval=interval, period=period,
                         auto_adjust=True, progress=False)
        if df.empty:
            raise Exception(f"yfinance returned empty data for {timeframe}")
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.columns = ["open", "high", "low", "close", "volume"]

    # Ensure UTC index
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    return df.tail(limit)

def add_rsi(df):
    df = df.copy()
    df["rsi"] = ta.rsi(df["close"], length=RSI_PERIOD)
    return df.dropna()

# ══════════════════════════════════════════════════════════════════
#  MARKET DATA — Bybit public ticker (fallback gracefully if blocked)
# ══════════════════════════════════════════════════════════════════

BYBIT_BASE    = "https://api.bybit.com"
BYBIT_SYMBOL  = "BTCUSDT"

def _bybit_ticker():
    try:
        r = requests.get(f"{BYBIT_BASE}/v5/market/tickers",
                         params={"category": "linear", "symbol": BYBIT_SYMBOL},
                         timeout=8)
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") == 0:
            return data["result"]["list"][0]
    except Exception as e:
        print(f"  [Bybit ticker] {e}")
    return None

def get_mark_price():
    t = _bybit_ticker()
    return float(t["markPrice"]) if t and "markPrice" in t else None

def get_funding_rate():
    t = _bybit_ticker()
    if t and "fundingRate" in t:
        return round(float(t["fundingRate"]) * 100, 5)
    return None

def get_open_interest():
    try:
        r = requests.get(f"{BYBIT_BASE}/v5/market/open-interest",
                         params={"category": "linear", "symbol": BYBIT_SYMBOL,
                                 "intervalTime": "1h", "limit": 1},
                         timeout=8)
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") == 0:
            lst = data["result"]["list"]
            if lst:
                return float(lst[0]["openInterest"])
    except Exception as e:
        print(f"  [Bybit OI] {e}")
    return None

def get_liquidations_approx():
    try:
        r = requests.get(f"{BYBIT_BASE}/v5/market/open-interest",
                         params={"category": "linear", "symbol": BYBIT_SYMBOL,
                                 "intervalTime": "15min", "limit": 3},
                         timeout=8)
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") == 0:
            lst = data["result"]["list"]
            if len(lst) >= 2:
                oi_now  = float(lst[0]["openInterest"])
                oi_prev = float(lst[1]["openInterest"])
                if oi_prev > 0:
                    chg = ((oi_now - oi_prev) / oi_prev) * 100
                    if chg < -2:
                        return f"⚡ OI dropped {chg:.1f}% — possible liquidation cascade"
                    elif chg > 2:
                        return f"📈 OI surged +{chg:.1f}% — new positions opening"
    except Exception as e:
        print(f"  [Bybit liq] {e}")
    return None

# ══════════════════════════════════════════════════════════════════
#  VOLUME SPIKE DETECTION
# ══════════════════════════════════════════════════════════════════

def detect_volume_spike(df, multiplier=VOLUME_SPIKE_MULTIPLIER, lookback=VOLUME_LOOKBACK):
    if len(df) < lookback + 2:
        return False, 0, 0, 0
    recent   = df["volume"].iloc[-(lookback+1):-1]
    avg_vol  = recent.mean()
    curr_vol = df["volume"].iloc[-1]
    ratio    = curr_vol / avg_vol if avg_vol > 0 else 0
    is_spike = ratio >= multiplier
    return is_spike, round(float(curr_vol), 2), round(float(avg_vol), 2), round(float(ratio), 2)

def volume_spike_summary(df_15m, df_1h):
    spike_15m, _, _, ratio_15m = detect_volume_spike(df_15m)
    spike_1h,  _, _, ratio_1h  = detect_volume_spike(df_1h)
    either_spike = spike_15m or spike_1h
    return either_spike, {
        "15m_spike": spike_15m, "15m_ratio": ratio_15m,
        "1h_spike":  spike_1h,  "1h_ratio":  ratio_1h,
        "any_spike": either_spike,
    }

# ══════════════════════════════════════════════════════════════════
#  RSI SLOPE HELPERS
# ══════════════════════════════════════════════════════════════════

def slope(df, n=SLOPE_CANDLES):
    vals = df["rsi"].iloc[-n:].tolist()
    diff = vals[-1] - vals[0]
    if diff > MIN_RSI_MOVE:    return "rising"
    elif diff < -MIN_RSI_MOVE: return "declining"
    return "flat"

def rsi_now(df):    return round(float(df["rsi"].iloc[-1]), 2)
def rsi_prev(df):   return round(float(df["rsi"].iloc[-2]), 2)
def rsi_peak(df, n=LOOKBACK_CANDLES):   return round(float(df["rsi"].iloc[-n:].max()), 2)
def rsi_bottom(df, n=LOOKBACK_CANDLES): return round(float(df["rsi"].iloc[-n:].min()), 2)

# ══════════════════════════════════════════════════════════════════
#  SIGNAL CONDITIONS
# ══════════════════════════════════════════════════════════════════

def check_short(tf):
    df4, df1, df15 = tf["4h"], tf["1h"], tf["15m"]
    r4, pk4, slp4  = rsi_now(df4), rsi_peak(df4), slope(df4)
    came_dn        = (pk4 - r4) >= MIN_RSI_MOVE
    r1, slp1       = rsi_now(df1), slope(df1)
    r15, pr15, slp15 = rsi_now(df15), rsi_prev(df15), slope(df15)
    break15 = (pr15 >= SHORT_15M_BREAK and r15 < SHORT_15M_BREAK) or \
              (r15 < SHORT_15M_BREAK and slp15 == "declining")
    vol_ok, vol_info = volume_spike_summary(df15, df1)
    conds = {
        "exhaustion": pk4 >= SHORT_EXHAUSTION_RSI,
        "4h_decline": slp4 == "declining" and came_dn,
        "1h_decline": slp1 == "declining",
        "15m_break":  break15,
        "vol_spike":  vol_ok,
    }
    details = {**conds,
        "peak_4h": pk4, "rsi_4h": r4, "slope_4h": slp4,
        "rsi_1h": r1,   "slope_1h": slp1,
        "rsi_15m": r15, "slope_15m": slp15,
        "vol": vol_info,
    }
    return all(conds.values()), details

def check_long(tf):
    df4, df1, df15 = tf["4h"], tf["1h"], tf["15m"]
    r4, bt4, slp4  = rsi_now(df4), rsi_bottom(df4), slope(df4)
    came_up        = (r4 - bt4) >= MIN_RSI_MOVE
    r1, slp1       = rsi_now(df1), slope(df1)
    r15, pr15, slp15 = rsi_now(df15), rsi_prev(df15), slope(df15)
    break15 = (pr15 <= LONG_15M_BREAK and r15 > LONG_15M_BREAK) or \
              (r15 > LONG_15M_BREAK and slp15 == "rising")
    vol_ok, vol_info = volume_spike_summary(df15, df1)
    conds = {
        "exhaustion": bt4 <= LONG_EXHAUSTION_RSI,
        "4h_rise":    slp4 == "rising" and came_up,
        "1h_rise":    slp1 == "rising",
        "15m_break":  break15,
        "vol_spike":  vol_ok,
    }
    details = {**conds,
        "bottom_4h": bt4, "rsi_4h": r4, "slope_4h": slp4,
        "rsi_1h": r1,     "slope_1h": slp1,
        "rsi_15m": r15,   "slope_15m": slp15,
        "vol": vol_info,
    }
    return all(conds.values()), details

# ══════════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════════════════════

state = {"setup": None, "signal_fired": False, "extreme_val": None}

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

def _slope_icon(s): return {"rising": "↑", "declining": "↓", "flat": "→"}.get(s, "?")
def _cond_icon(b):  return "✅" if b else "❌"

def build_message(sig_type, details, price, funding, oi, liq_note):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    vol = details["vol"]

    if sig_type == "SHORT":
        hdr        = "🔴 *SHORT SIGNAL — RSI EXHAUSTION*"
        extreme    = f"4H RSI peak: `{details['peak_4h']}` ≥ {SHORT_EXHAUSTION_RSI} {_cond_icon(details['exhaustion'])}"
        entry_hint = "⚠️ *Consider short entry on retest of breakdown level*"
    else:
        hdr        = "🟢 *LONG SIGNAL — RSI EXHAUSTION*"
        extreme    = f"4H RSI bottom: `{details['bottom_4h']}` ≤ {LONG_EXHAUSTION_RSI} {_cond_icon(details['exhaustion'])}"
        entry_hint = "⚠️ *Consider long entry on retest of breakout level*"

    rsi_grid = (
        f"```\n"
        f"TF   RSI    Slope  Cond\n"
        f"─────────────────────────\n"
        f"4H   {details['rsi_4h']:<6} {_slope_icon(details['slope_4h'])}      {_cond_icon(details.get('4h_decline') or details.get('4h_rise'))}\n"
        f"1H   {details['rsi_1h']:<6} {_slope_icon(details['slope_1h'])}      {_cond_icon(details.get('1h_decline') or details.get('1h_rise'))}\n"
        f"15M  {details['rsi_15m']:<6} {_slope_icon(details['slope_15m'])}      {_cond_icon(details['15m_break'])}\n"
        f"```"
    )

    vol_15m   = f"`{vol['15m_ratio']}x` {'🔥' if vol['15m_spike'] else '—'}"
    vol_1h    = f"`{vol['1h_ratio']}x`  {'🔥' if vol['1h_spike'] else '—'}"
    vol_block = f"📊 *Volume Spike* {_cond_icon(vol['any_spike'])}\n  15M: {vol_15m}   1H: {vol_1h}"

    market_lines = []
    if price:
        market_lines.append(f"💵 Price: `${price:,.2f}`")
    if funding is not None:
        sent = "🐂 long bias" if funding > 0 else "🐻 short bias"
        market_lines.append(f"💰 Funding: `{funding:+.4f}%` ({sent})")
    if oi:
        market_lines.append(f"📦 Open Interest: `${oi/1e9:.2f}B`")
    if liq_note:
        market_lines.append(liq_note)

    market_block = "\n".join(market_lines) if market_lines else "_Market data unavailable_"

    return "\n".join(filter(None, [
        hdr,
        f"📌 *{SYMBOL_DISPLAY} — 15M + 1H + 4H Confluence*\n",
        "*Exhaustion confirmed:*",
        extreme + "\n",
        "*RSI breakdown across timeframes:*",
        rsi_grid,
        vol_block + "\n",
        "*Live market data:*",
        market_block + "\n",
        entry_hint,
        f"\n🕐 `{now}`",
        "_Not financial advice — DYOR_",
    ]))

# ══════════════════════════════════════════════════════════════════
#  TELEGRAM SENDER
# ══════════════════════════════════════════════════════════════════

async def _tg_send(msg):
    bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")

def send_telegram(msg):
    asyncio.run(_tg_send(msg))
    print(f"  [TG ✅] Message sent ({len(msg)} chars)")

# ══════════════════════════════════════════════════════════════════
#  MAIN SCAN JOB
# ══════════════════════════════════════════════════════════════════

def run_scan():
    global state
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"\n[{ts}] ─── Scanning {SYMBOL_DISPLAY} ───")

    try:
        tf = {}
        for t in ["15m", "1h", "4h"]:
            raw   = fetch_ohlcv(t, limit=120)
            tf[t] = add_rsi(raw)

        r4  = rsi_now(tf["4h"])
        pk4 = rsi_peak(tf["4h"])
        bt4 = rsi_bottom(tf["4h"])
        print(f"  4H RSI={r4} | peak={pk4} | bottom={bt4} | setup={state['setup']} | fired={state['signal_fired']}")

        if state["signal_fired"]:
            check_reset(tf)
            return

        if state["setup"] is None:
            if pk4 >= SHORT_EXHAUSTION_RSI:
                state["setup"] = "SHORT"; state["extreme_val"] = pk4
                print(f"  ⚡ SHORT exhaustion — 4H peak RSI = {pk4}")
            elif bt4 <= LONG_EXHAUSTION_RSI:
                state["setup"] = "LONG"; state["extreme_val"] = bt4
                print(f"  ⚡ LONG exhaustion — 4H bottom RSI = {bt4}")
            else:
                print(f"  No extreme setup (need ≥{SHORT_EXHAUSTION_RSI} or ≤{LONG_EXHAUSTION_RSI})")
                return

        if state["setup"] == "SHORT":
            fired, details = check_short(tf)
            c = details
            print(f"  SHORT: exhaust={_cond_icon(c['exhaustion'])} 4H↓={_cond_icon(c['4h_decline'])} "
                  f"1H↓={_cond_icon(c['1h_decline'])} 15M={_cond_icon(c['15m_break'])} VOL={_cond_icon(c['vol']['any_spike'])}")
        elif state["setup"] == "LONG":
            fired, details = check_long(tf)
            c = details
            print(f"  LONG:  exhaust={_cond_icon(c['exhaustion'])} 4H↑={_cond_icon(c['4h_rise'])} "
                  f"1H↑={_cond_icon(c['1h_rise'])} 15M={_cond_icon(c['15m_break'])} VOL={_cond_icon(c['vol']['any_spike'])}")
        else:
            return

        if fired:
            print("  🎯 ALL CONDITIONS MET — sending signal...")
            msg = build_message(
                state["setup"], details,
                get_mark_price(), get_funding_rate(),
                get_open_interest(), get_liquidations_approx()
            )
            send_telegram(msg)
            state["signal_fired"] = True
        else:
            pending = [k for k, v in c.items() if isinstance(v, bool) and not v and k != "vol"]
            if not details["vol"]["any_spike"]:
                pending.append("vol_spike")
            print(f"  Waiting for: {', '.join(pending) or 'all met'}")

    except Exception as e:
        import traceback
        print(f"  ❌ ERROR: {e}")
        traceback.print_exc()

# ══════════════════════════════════════════════════════════════════
#  SCHEDULER
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║   RSI EXHAUSTION + VOLUME SPIKE BOT  v2.0  starting...     ║
╠══════════════════════════════════════════════════════════════╣""")
    print(f"║  Symbol     : {SYMBOL_DISPLAY:<44} ║")
    print(f"║  SHORT zone : 4H RSI ≥ {SHORT_EXHAUSTION_RSI} then exhaust + 3-TF breakdown  ║")
    print(f"║  LONG zone  : 4H RSI ≤ {LONG_EXHAUSTION_RSI} then exhaust + 3-TF breakout   ║")
    print(f"║  Vol spike  : {VOLUME_SPIKE_MULTIPLIER}x average volume on 15M or 1H          ║")
    print(f"║  Scan every : 15 minutes                                    ║")
    print(f"║  Data source: yfinance (no geo-block) + Bybit market data  ║")
    print( "╚══════════════════════════════════════════════════════════════╝\n")

    run_scan()

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_scan, trigger="cron", minute="1,16,31,46", second=0)
    print("\n📅 Scheduler active — next run at :01, :16, :31, or :46 past the hour")
    print("   Press Ctrl+C to stop\n")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped cleanly")
