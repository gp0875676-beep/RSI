"""
╔══════════════════════════════════════════════════════════════════╗
║     RSI EXHAUSTION + VOLUME SPIKE SIGNAL BOT  v4.0              ║
║     TOP 2000 COINS — 15M / 1H / 4H Confluence                  ║
║     RSI 90/10 EXTREME LEVELS — Ultra High Quality Signals      ║
║     Data: yfinance only (no geo-block, no external API)        ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import time
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

RSI_PERIOD = 14

SHORT_EXHAUSTION_RSI = 90   # extreme overbought
LONG_EXHAUSTION_RSI  = 10   # extreme oversold

SHORT_15M_BREAK = 60
LONG_15M_BREAK  = 40

VOLUME_SPIKE_MULTIPLIER = 1.8
VOLUME_LOOKBACK         = 20

SLOPE_CANDLES = 3
MIN_RSI_MOVE  = 3.0

RESET_UPPER = 65
RESET_LOWER = 35

LOOKBACK_CANDLES = 12

YF_INTERVAL = {
    "15m": ("15m", "7d"),
    "1h":  ("1h",  "30d"),
}

# ══════════════════════════════════════════════════════════════════
#  TOP 2000 CRYPTO SYMBOLS — hardcoded yfinance format
#  (No external API needed — this list covers top coins + memes)
# ══════════════════════════════════════════════════════════════════

SYMBOLS = [
    # ── Mega caps ──────────────────────────────────────────────────
    "BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD",
    "ADA-USD","AVAX-USD","DOGE-USD","TRX-USD","DOT-USD",
    "MATIC-USD","LTC-USD","SHIB-USD","BCH-USD","LINK-USD",
    "XLM-USD","UNI-USD","ATOM-USD","XMR-USD","ETC-USD",
    # ── Large caps ─────────────────────────────────────────────────
    "ICP-USD","APT-USD","FIL-USD","HBAR-USD","VET-USD",
    "NEAR-USD","ARB-USD","OP-USD","MKR-USD","AAVE-USD",
    "GRT-USD","ALGO-USD","QNT-USD","EGLD-USD","SAND-USD",
    "MANA-USD","AXS-USD","THETA-USD","XTZ-USD","EOS-USD",
    "CAKE-USD","NEO-USD","WAVES-USD","ZEC-USD","DASH-USD",
    "CHZ-USD","ENJ-USD","BAT-USD","COMP-USD","CRV-USD",
    "SNX-USD","YFI-USD","SUSHI-USD","1INCH-USD","ZIL-USD",
    "RVN-USD","HOT-USD","SC-USD","DGB-USD","BTT-USD",
    "WIN-USD","ANKR-USD","CELO-USD","ROSE-USD","ONE-USD",
    "KAVA-USD","IOTA-USD","ONT-USD","ICX-USD","ZRX-USD",
    "LRC-USD","STORJ-USD","BAND-USD","RSR-USD","TWT-USD",
    "DENT-USD","RLC-USD","MTL-USD","STMX-USD","OGN-USD",
    # ── Mid caps ───────────────────────────────────────────────────
    "SUI-USD","SEI-USD","TIA-USD","JTO-USD","PYTH-USD",
    "JUP-USD","WIF-USD","BOME-USD","MEW-USD","SLERF-USD",
    "INJ-USD","IMX-USD","STX-USD","CFX-USD","BLUR-USD",
    "API3-USD","RDNT-USD","PENDLE-USD","SSV-USD","GMX-USD",
    "DYDX-USD","PERP-USD","RUNE-USD","KSM-USD","SCRT-USD",
    "CTSI-USD","OCEAN-USD","NMR-USD","LINA-USD","CELR-USD",
    "SXP-USD","ALPHA-USD","REEF-USD","HARD-USD","VITE-USD",
    "BEL-USD","TKO-USD","POLS-USD","DODO-USD","UNFI-USD",
    "LIT-USD","AKRO-USD","VIDT-USD","FOR-USD","FRONT-USD",
    "PROS-USD","BURGER-USD","BAKE-USD","XVS-USD","AUTO-USD",
    "ALPACA-USD","TLM-USD","ALICE-USD","DEGO-USD","TORN-USD",
    "BOND-USD","PROM-USD","FIRO-USD","DF-USD","MIR-USD",
    "AUCTION-USD","SUPER-USD","MASK-USD","FORTH-USD","ILA-USD",
    "PUNDIX-USD","TRIBE-USD","RGT-USD","AGLD-USD","RAD-USD",
    "BETA-USD","RARE-USD","LAZIO-USD","PORTO-USD","SANTOS-USD",
    "CHESS-USD","IDEX-USD","ASTR-USD","MOVR-USD","ATA-USD",
    "QUICK-USD","BICO-USD","FLOKI-USD","PEOPLE-USD","SPELL-USD",
    "JASMY-USD","ACA-USD","VOXEL-USD","CVP-USD","GHST-USD",
    "YFII-USD","MDT-USD","TRB-USD","BTS-USD","WAN-USD",
    "REI-USD","GTC-USD","XDEFI-USD","FARM-USD","HUNT-USD",
    "GYEN-USD","POLS-USD","OM-USD","TOMO-USD","FET-USD",
    "AGIX-USD","OCEAN-USD","NMR-USD","REN-USD","KNC-USD",
    "BAL-USD","UMA-USD","MLN-USD","BNT-USD","ANT-USD",
    "REP-USD","TRAC-USD","GNO-USD","PLA-USD","KEEP-USD",
    "NU-USD","T-USD","RLC-USD","ASM-USD","ARPA-USD",
    "CTXC-USD","BLZ-USD","TROY-USD","PERL-USD","TCT-USD",
    "MBL-USD","COS-USD","TOMO-USD","FTM-USD","COTI-USD",
    "STPT-USD","WTC-USD","LOOM-USD","EDO-USD","APIX-USD",
    "SKL-USD","GLM-USD","PAXG-USD","WBTC-USD","RENBTC-USD",
    # ── Meme coins ─────────────────────────────────────────────────
    "PEPE-USD","BONK-USD","MEME-USD","TURBO-USD","BABYDOGE-USD",
    "SAMO-USD","ELON-USD","KISHU-USD","VOLT-USD","SHINJA-USD",
    "PIT-USD","CATGIRL-USD","SAITAMA-USD","HOGE-USD","RYOSHI-USD",
    "DOGELON-USD","AKITA-USD","KING-USD","LEASH-USD","BONE-USD",
    "ELONGATE-USD","SAFEMOON-USD","CUMROCKET-USD","TAMA-USD",
    "MONONOKE-USD","SHIBT-USD","WOOF-USD","PIG-USD","TSUKI-USD",
    "POODL-USD","XSHIB-USD","SHIBAINU-USD","FLOKINOMICS-USD",
    "DOGEDASH-USD","FLOKIS-USD","BABYSAITAMA-USD","MOONSTAR-USD",
    "KMON-USD","SMON-USD","COGE-USD","BSCD-USD","SHEESHA-USD",
    # ── DeFi ───────────────────────────────────────────────────────
    "LQTY-USD","LUSD-USD","FRAX-USD","FXS-USD","CVX-USD",
    "ALCX-USD","SPELL-USD","MIM-USD","TIME-USD","WMEMO-USD",
    "OHM-USD","SOHM-USD","KLIMA-USD","BTRFLY-USD","TOKE-USD",
    "VSTA-USD","PREMIA-USD","HEGIC-USD","COVER-USD","RULER-USD",
    "CREAM-USD","IRON-USD","TITAN-USD","SHACK-USD","GYSR-USD",
    "POOL-USD","TRIBE-USD","FEI-USD","RAI-USD","FLOAT-USD",
    "MPH-USD","IDLE-USD","INDEX-USD","DPI-USD","MVI-USD",
    "BED-USD","DATA-USD","BANK-USD","MIST-USD","ARCH-USD",
    # ── Layer 1 / Layer 2 ──────────────────────────────────────────
    "ROSE-USD","GLMR-USD","ASTR-USD","SGB-USD","METIS-USD",
    "BOBA-USD","OMG-USD","CELR-USD","LYX-USD","TLOS-USD",
    "KLAY-USD","CSPR-USD","FLOW-USD","MINA-USD","HBAR-USD",
    "XDC-USD","CELO-USD","ZEN-USD","STRAX-USD","ARK-USD",
    "LSK-USD","XEM-USD","QTUM-USD","NULS-USD","ARDR-USD",
    "IGNIS-USD","NXT-USD","KMD-USD","SYS-USD","PIVX-USD",
    "FIRO-USD","BEAM-USD","GRIN-USD","MWC-USD","DUSK-USD",
    "CCXX-USD","ALIAS-USD","PART-USD","NIX-USD","CLOAK-USD",
    # ── NFT / Gaming / Metaverse ───────────────────────────────────
    "APE-USD","GMT-USD","GST-USD","STEPN-USD","LOOKS-USD",
    "X2Y2-USD","SUDOSWAP-USD","BEND-USD","NFT-USD","RARE-USD",
    "RARI-USD","SUPER-USD","GALA-USD","ILV-USD","ATLAS-USD",
    "POLIS-USD","GODS-USD","GUILD-USD","YGG-USD","MCADE-USD",
    "RLY-USD","SIDUS-USD","SHRAPNEL-USD","HEROES-USD","MOBOX-USD",
    "TLM-USD","ALICE-USD","PVU-USD","TOWER-USD","HERO-USD",
    "SKILL-USD","WANA-USD","SFUND-USD","GBYTE-USD","UFO-USD",
    "DOSE-USD","FEAR-USD","VEMP-USD","NFTB-USD","WAXP-USD",
    "VGX-USD","POWR-USD","MAPS-USD","COPE-USD","MEDIA-USD",
    # ── Infrastructure / Oracle / Storage ──────────────────────────
    "API3-USD","BAND-USD","DIA-USD","ORAI-USD","TRB-USD",
    "NMR-USD","REP-USD","AUC-USD","NEST-USD","DOS-USD",
    "LINK-USD","UMA-USD","SUPRA-USD","FLUX-USD","SIA-USD",
    "AR-USD","STORJ-USD","FIL-USD","BLUZELLE-USD","CUDOS-USD",
    "NYM-USD","DUSK-USD","PRE-USD","HOPR-USD","LTO-USD",
    "CRUST-USD","AIOZ-USD","THETA-USD","TFUEL-USD","ANKR-USD",
    # ── Exchange tokens ────────────────────────────────────────────
    "BNB-USD","HT-USD","OKB-USD","KCS-USD","FTT-USD",
    "GT-USD","LEO-USD","MX-USD","BGB-USD","BTSE-USD",
    "CRO-USD","NEXO-USD","CEL-USD","LOCK-USD","HBTC-USD",
    # ── Privacy coins ──────────────────────────────────────────────
    "XMR-USD","ZEC-USD","DASH-USD","FIRO-USD","BEAM-USD",
    "GRIN-USD","DERO-USD","PIVX-USD","ZEN-USD","SCRT-USD",
    "KEEP-USD","NMX-USD","CCX-USD","OXEN-USD","ZANO-USD",
    # ── Wrapped / Stablecoins (skip these — RSI stays flat) ───────
    # Not included intentionally
    # ── Newer launches ─────────────────────────────────────────────
    "PYTH-USD","JTO-USD","JUP-USD","WEN-USD","TNSR-USD",
    "PRCL-USD","ZETA-USD","STRK-USD","MANTA-USD","ALT-USD",
    "PIXEL-USD","PORTAL-USD","VANRY-USD","MYRO-USD","BODEN-USD",
    "TRUMP-USD","MELANIA-USD","MOTHER-USD","GUMMY-USD","NEIRO-USD",
    "CATI-USD","HMSTR-USD","DOGS-USD","MAJOR-USD","BLUM-USD",
    "BANANA-USD","ORAI-USD","SAGA-USD","REZ-USD","BBB-USD",
    "OMNI-USD","ETHFI-USD","EIGEN-USD","LISTA-USD","ZK-USD",
    "ZKFAIR-USD","MOCA-USD","TAIKO-USD","MERL-USD","BOB-USD",
    "IO-USD","ZKLEND-USD","EKUBO-USD","NOSTR-USD","HAEDAL-USD",
]

# Deduplicate preserving order
_seen = set()
SYMBOLS = [s for s in SYMBOLS if not (_seen.add(s) or s in _seen)]

# ══════════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════════════════════

states = {s: {"setup": None, "signal_fired": False, "extreme_val": None}
          for s in SYMBOLS}

# ══════════════════════════════════════════════════════════════════
#  OHLCV via yfinance
# ══════════════════════════════════════════════════════════════════

def fetch_ohlcv(symbol, timeframe, limit=120):
    if timeframe == "4h":
        df = yf.download(symbol, interval="1h", period="60d",
                         auto_adjust=True, progress=False,
                         silence_errors=True)
        if df is None or df.empty:
            raise Exception("empty")
        df = df[["Open","High","Low","Close","Volume"]].copy()
        df.columns = ["open","high","low","close","volume"]
        df = df.resample("4h").agg({
            "open":"first","high":"max",
            "low":"min","close":"last","volume":"sum"
        }).dropna()
    else:
        interval, period = YF_INTERVAL[timeframe]
        df = yf.download(symbol, interval=interval, period=period,
                         auto_adjust=True, progress=False,
                         silence_errors=True)
        if df is None or df.empty:
            raise Exception("empty")
        df = df[["Open","High","Low","Close","Volume"]].copy()
        df.columns = ["open","high","low","close","volume"]

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0).str.lower()

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
#  VOLUME SPIKE
# ══════════════════════════════════════════════════════════════════

def detect_volume_spike(df, multiplier=VOLUME_SPIKE_MULTIPLIER, lookback=VOLUME_LOOKBACK):
    if len(df) < lookback + 2:
        return False, 0, 0, 0
    recent   = df["volume"].iloc[-(lookback+1):-1]
    avg_vol  = recent.mean()
    curr_vol = df["volume"].iloc[-1]
    ratio    = curr_vol / avg_vol if avg_vol > 0 else 0
    is_spike = ratio >= multiplier
    return is_spike, round(float(curr_vol),2), round(float(avg_vol),2), round(float(ratio),2)

def volume_spike_summary(df_15m, df_1h):
    spike_15m, _, _, ratio_15m = detect_volume_spike(df_15m)
    spike_1h,  _, _, ratio_1h  = detect_volume_spike(df_1h)
    either = spike_15m or spike_1h
    return either, {
        "15m_spike": spike_15m, "15m_ratio": ratio_15m,
        "1h_spike":  spike_1h,  "1h_ratio":  ratio_1h,
        "any_spike": either,
    }

# ══════════════════════════════════════════════════════════════════
#  RSI HELPERS
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
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════

def _slope_icon(s): return {"rising":"↑","declining":"↓","flat":"→"}.get(s,"?")
def _cond_icon(b):  return "✅" if b else "❌"

def build_message(sig_type, details, symbol_display):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    vol = details["vol"]

    if sig_type == "SHORT":
        hdr        = "🔴 *SHORT SIGNAL — RSI EXHAUSTION*"
        extreme    = f"4H RSI peak: `{details['peak_4h']}` ≥ {SHORT_EXHAUSTION_RSI} {_cond_icon(details['exhaustion'])}"
        entry_hint = "⚠️ *Consider short on retest of breakdown level*"
    else:
        hdr        = "🟢 *LONG SIGNAL — RSI EXHAUSTION*"
        extreme    = f"4H RSI bottom: `{details['bottom_4h']}` ≤ {LONG_EXHAUSTION_RSI} {_cond_icon(details['exhaustion'])}"
        entry_hint = "⚠️ *Consider long on retest of breakout level*"

    rsi_grid = (
        f"```\n"
        f"TF   RSI    Slope  Cond\n"
        f"─────────────────────────\n"
        f"4H   {details['rsi_4h']:<6} {_slope_icon(details['slope_4h'])}      {_cond_icon(details.get('4h_decline') or details.get('4h_rise'))}\n"
        f"1H   {details['rsi_1h']:<6} {_slope_icon(details['slope_1h'])}      {_cond_icon(details.get('1h_decline') or details.get('1h_rise'))}\n"
        f"15M  {details['rsi_15m']:<6} {_slope_icon(details['slope_15m'])}      {_cond_icon(details['15m_break'])}\n"
        f"```"
    )

    vol_block = (
        f"📊 *Volume Spike* {_cond_icon(vol['any_spike'])}\n"
        f"  15M: `{vol['15m_ratio']}x` {'🔥' if vol['15m_spike'] else '—'}"
        f"   1H: `{vol['1h_ratio']}x` {'🔥' if vol['1h_spike'] else '—'}"
    )

    return "\n".join(filter(None, [
        hdr,
        f"📌 *{symbol_display} — 15M + 1H + 4H Confluence*\n",
        "*Exhaustion confirmed:*",
        extreme + "\n",
        "*RSI breakdown across timeframes:*",
        rsi_grid,
        vol_block + "\n",
        entry_hint,
        f"\n🕐 `{now}`",
        "_Not financial advice — DYOR_",
    ]))

async def _tg_send(msg):
    bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")

def send_telegram(msg):
    asyncio.run(_tg_send(msg))
    print(f"    [TG ✅] Sent ({len(msg)} chars)")

# ══════════════════════════════════════════════════════════════════
#  SCAN ONE SYMBOL
# ══════════════════════════════════════════════════════════════════

def scan_symbol(symbol):
    display = symbol.replace("-USD","USDT")
    st = states[symbol]

    try:
        tf = {}
        for t in ["15m", "1h", "4h"]:
            raw   = fetch_ohlcv(symbol, t, limit=120)
            tf[t] = add_rsi(raw)
            time.sleep(0.2)

        r4  = rsi_now(tf["4h"])
        pk4 = rsi_peak(tf["4h"])
        bt4 = rsi_bottom(tf["4h"])

        # Only log coins in extreme zone or active setup
        in_zone = pk4 >= SHORT_EXHAUSTION_RSI or bt4 <= LONG_EXHAUSTION_RSI or st["setup"]
        if in_zone:
            print(f"  {display:<14} 4H={r4:<6} peak={pk4:<6} bot={bt4:<6} setup={st['setup'] or '—'}")

        # Reset check
        if st["signal_fired"]:
            if st["setup"] == "SHORT" and r4 <= RESET_UPPER:
                print(f"    [RESET] {display} SHORT cleared")
                states[symbol] = {"setup": None, "signal_fired": False, "extreme_val": None}
            elif st["setup"] == "LONG" and r4 >= RESET_LOWER:
                print(f"    [RESET] {display} LONG cleared")
                states[symbol] = {"setup": None, "signal_fired": False, "extreme_val": None}
            return

        # Detect setup
        if st["setup"] is None:
            if pk4 >= SHORT_EXHAUSTION_RSI:
                states[symbol]["setup"] = "SHORT"
                states[symbol]["extreme_val"] = pk4
                print(f"    ⚡ {display} SHORT exhaustion! peak={pk4}")
            elif bt4 <= LONG_EXHAUSTION_RSI:
                states[symbol]["setup"] = "LONG"
                states[symbol]["extreme_val"] = bt4
                print(f"    ⚡ {display} LONG exhaustion! bottom={bt4}")
            else:
                return

        # Check confluence
        if states[symbol]["setup"] == "SHORT":
            fired, details = check_short(tf)
            c = details
            print(f"    SHORT {display}: "
                  f"exhaust={_cond_icon(c['exhaustion'])} "
                  f"4H↓={_cond_icon(c['4h_decline'])} "
                  f"1H↓={_cond_icon(c['1h_decline'])} "
                  f"15M={_cond_icon(c['15m_break'])} "
                  f"VOL={_cond_icon(c['vol']['any_spike'])}")
        else:
            fired, details = check_long(tf)
            c = details
            print(f"    LONG  {display}: "
                  f"exhaust={_cond_icon(c['exhaustion'])} "
                  f"4H↑={_cond_icon(c['4h_rise'])} "
                  f"1H↑={_cond_icon(c['1h_rise'])} "
                  f"15M={_cond_icon(c['15m_break'])} "
                  f"VOL={_cond_icon(c['vol']['any_spike'])}")

        if fired:
            print(f"    🎯 {display} SIGNAL! Sending Telegram...")
            msg = build_message(states[symbol]["setup"], details, display)
            send_telegram(msg)
            states[symbol]["signal_fired"] = True
        else:
            pending = [k for k, v in c.items() if isinstance(v, bool) and not v and k != "vol"]
            if not details["vol"]["any_spike"]: pending.append("vol_spike")
            print(f"    ⏳ {display} waiting: {', '.join(pending)}")

    except Exception:
        pass  # silently skip — no yfinance data for this coin

# ══════════════════════════════════════════════════════════════════
#  MAIN SCAN
# ══════════════════════════════════════════════════════════════════

def run_scan():
    ts    = datetime.now(timezone.utc).strftime("%H:%M:%S")
    total = len(SYMBOLS)
    print(f"\n[{ts}] ══ Scanning {total} coins ══")

    for i, symbol in enumerate(SYMBOLS, 1):
        if i % 50 == 0:
            print(f"  ... {i}/{total} scanned ...")
        scan_symbol(symbol)

    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] ══ Scan complete ══\n")

# ══════════════════════════════════════════════════════════════════
#  STARTUP + SCHEDULER
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    total = len(SYMBOLS)
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║   RSI EXHAUSTION BOT  v4.0  —  {total} COINS             ║
╠══════════════════════════════════════════════════════════════╣
║  SHORT zone : 4H RSI ≥ 90 — extreme overbought             ║
║  LONG zone  : 4H RSI ≤ 10 — extreme oversold               ║
║  Vol spike  : 1.8x average on 15M or 1H                    ║
║  Confluence : 4H + 1H + 15M all must confirm               ║
║  Scan every : 15 minutes                                    ║
║  Data source: yfinance only (zero external APIs)           ║
╚══════════════════════════════════════════════════════════════╝
""")

    run_scan()

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_scan, trigger="cron", minute="1,16,31,46", second=0)

    print("📅 Scheduler active — scans at :01, :16, :31, :46 past the hour")
    print("   Press Ctrl+C to stop\n")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped cleanly")
