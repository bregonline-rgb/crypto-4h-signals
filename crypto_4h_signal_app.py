"""
crypto_4h_signal_app.py

Single-file Python app that fetches 4-hour OHLCV for major coins (Binance public REST via ccxt),
calculates technical indicators, detects a few candlestick patterns, and outputs buy/sell/hold
signals with a confidence score. Saves results to JSON and CSV and can run continuously.

Dependencies:
  pip install ccxt pandas numpy rich

Usage:
  python crypto_4h_signal_app.py          # runs once and prints signals
  python crypto_4h_signal_app.py --loop   # run continuously (polls every 15 minutes)

Notes:
  - Uses public endpoints (no API keys) for OHLCV.
  - This is educational/example code. Backtest before trading; use risk management.

"""
import argparse
import ccxt
import pandas as pd
import numpy as np
import time
import json
import os
from datetime import datetime, timezone
from rich.console import Console
from rich.table import Table

console = Console()

DEFAULT_SYMBOLS = ["ETH/USDT", "BTC/USDT", "BNB/USDT", "SOL/USDT", "ADA/USDT"]
TIMEFRAME = "4h"
LIMIT = 300  # number of candles to fetch
POLL_MINUTES = 15
OUTPUT_DIR = "signals_output"

# ---------------------- Indicator helpers ----------------------

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ma_up = up.ewm(alpha=1/period, adjust=False).mean()
    ma_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))

def macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def bollinger(series: pd.Series, length=20, stds=2):
    ma = series.rolling(window=length).mean()
    std = series.rolling(window=length).std()
    upper = ma + stds * std
    lower = ma - stds * std
    return upper, ma, lower

# ---------------------- Candlestick patterns (simple) ----------------------

def is_bullish_engulfing(df: pd.DataFrame, idx: int) -> bool:
    if idx < 1:
        return False
    prev = df.iloc[idx - 1]
    cur = df.iloc[idx]
    return (prev['close'] < prev['open']) and (cur['close'] > cur['open']) and (cur['close'] > prev['open']) and (cur['open'] < prev['close'])

def is_bearish_engulfing(df: pd.DataFrame, idx: int) -> bool:
    if idx < 1:
        return False
    prev = df.iloc[idx - 1]
    cur = df.iloc[idx]
    return (prev['close'] > prev['open']) and (cur['close'] < cur['open']) and (cur['open'] > prev['close']) and (cur['close'] < prev['open'])

def is_hammer(df: pd.DataFrame, idx: int, body_threshold=0.35) -> bool:
    row = df.iloc[idx]
    body = abs(row['close'] - row['open'])
    lower_shadow = min(row['open'], row['close']) - row['low']
    upper_shadow = row['high'] - max(row['open'], row['close'])
    return (lower_shadow > 2 * body) and (upper_shadow < body * 0.5)

# ---------------------- Signal logic ----------------------
def compute_signals(df: pd.DataFrame) -> dict:
    close = df['close']
    df = df.copy()
    df['ema20'] = ema(close, 20)
    df['ema50'] = ema(close, 50)
    df['rsi'] = rsi(close, 14)
    macd_line, signal_line, hist = macd(close)
    df['macd'] = macd_line
    df['macd_signal'] = signal_line
    df['macd_hist'] = hist
    df['bb_upper'], df['bb_mid'], df['bb_lower'] = bollinger(close, 20, 2)

    latest_idx = len(df) - 1
    last = df.iloc[latest_idx]

    score = 0
    reasons = []

    # Trend: price vs EMA50
    if last['close'] > last['ema50']:
        score += 1
        reasons.append("price above EMA50 (uptrend)")
    else:
        score -= 1
        reasons.append("price below EMA50 (downtrend)")

    # Momentum: RSI
    if last['rsi'] < 30:
        score += 2
        reasons.append(f"RSI {last['rsi']:.1f} (oversold)")
    elif last['rsi'] > 70:
        score -= 2
        reasons.append(f"RSI {last['rsi']:.1f} (overbought)")
    else:
        reasons.append(f"RSI {last['rsi']:.1f}")

    # MACD crossover
    prev_macd = df.iloc[latest_idx - 1]['macd']
    prev_signal = df.iloc[latest_idx - 1]['macd_signal']
    cur_macd = last['macd']
    cur_signal = last['macd_signal']

    if (prev_macd < prev_signal) and (cur_macd > cur_signal):
        score += 2
        reasons.append('MACD bullish crossover')
    elif (prev_macd > prev_signal) and (cur_macd < cur_signal):
        score -= 2
        reasons.append('MACD bearish crossover')
    else:
        reasons.append('MACD neutral')

    # Bollinger: price below lower band = potential buy, above upper band = potential sell
    if last['close'] < last['bb_lower']:
        score += 1
        reasons.append('price below Bollinger lower band')
    elif last['close'] > last['bb_upper']:
        score -= 1
        reasons.append('price above Bollinger upper band')

    # Candlestick patterns (last candle)
    if is_bullish_engulfing(df, latest_idx):
        score += 2
        reasons.append('Bullish Engulfing')
    if is_bearish_engulfing(df, latest_idx):
        score -= 2
        reasons.append('Bearish Engulfing')
    if is_hammer(df, latest_idx):
        score += 1
        reasons.append('Hammer')

    # Final mapping
    if score >= 3:
        signal = 'STRONG BUY'
    elif score == 2:
        signal = 'BUY'
    elif score == 1 or score == 0:
        signal = 'HOLD'
    elif score == -1 or score == -2:
        signal = 'SELL'
    else:
        signal = 'STRONG SELL'

    return {
        'signal': signal,
        'score': int(score),
        'reasons': reasons,
        'last_close': float(last['close']),
        'rsi': float(last['rsi']),
        'ema20': float(last['ema20']),
        'ema50': float(last['ema50']),
        'macd': float(last['macd']),
        'macd_signal': float(last['macd_signal']),
        'bb_upper': float(last['bb_upper']),
        'bb_lower': float(last['bb_lower']),
        'timestamp': int(last.name.timestamp()) if isinstance(last.name, pd.Timestamp) else int(time.time())
    }

# ---------------------- Fetch data ----------------------
def fetch_ohlcv(exchange, symbol: str, timeframe: str = TIMEFRAME, limit: int = LIMIT) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df = df.set_index('ts')
    return df

# ---------------------- Main runner ----------------------
def run_once(symbols: list) -> dict:
    exchange = ccxt.binance({'enableRateLimit': True})
    results = {}
    for s in symbols:
        try:
            df = fetch_ohlcv(exchange, s)
            signal = compute_signals(df)
            results[s] = signal
        except Exception as e:
            console.print(f"[red]Error fetching or processing {s}: {e}")
    return results

def save_results(results: dict):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    json_path = os.path.join(OUTPUT_DIR, f'signals_{now}.json')
    csv_rows = []
    with open(json_path, 'w') as f:
        json.dump(results, f, indent=2)
    for sym, r in results.items():
        csv_rows.append({
            'symbol': sym,
            'signal': r['signal'],
            'score': r['score'],
            'last_close': r['last_close'],
            'rsi': r['rsi'],
            'ema20': r['ema20'],
            'ema50': r['ema50'],
            'macd': r['macd'],
            'macd_signal': r['macd_signal'],
            'bb_upper': r['bb_upper'],
            'bb_lower': r['bb_lower'],
            'timestamp': r['timestamp']
        })
    dfc = pd.DataFrame(csv_rows)
    csv_path = os.path.join(OUTPUT_DIR, f'signals_{now}.csv')
    dfc.to_csv(csv_path, index=False)
    console.print(f"Saved JSON -> {json_path}")
    console.print(f"Saved CSV  -> {csv_path}")

def pretty_print(results: dict):
    table = Table(title=f"4h Signals - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    table.add_column("Symbol", style="cyan")
    table.add_column("Signal", style="magenta")
    table.add_column("Score", style="green")
    table.add_column("Last Close", justify="right")
    table.add_column("RSI", justify="right")
    table.add_column("Reasons")
    for s, r in results.items():
        table.add_row(s, r['signal'], str(r['score']), f"{r['last_close']:.6f}", f"{r['rsi']:.1f}", "; ".join(r['reasons']))
    console.print(table)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', help='List of symbols (e.g. ETH/USDT BTC/USDT)')
    parser.add_argument('--loop', action='store_true', help='Run continuously (poll every POLL_MINUTES)')
    args = parser.parse_args()

    symbols = args.symbols if args.symbols else DEFAULT_SYMBOLS

    if args.loop:
        console.print(f"Running continuously. Poll every {POLL_MINUTES} minutes. Symbols: {symbols}")
        try:
            while True:
                res = run_once(symbols)
                pretty_print(res)
                save_results(res)
                console.print(f"Sleeping {POLL_MINUTES} minutes... (Ctrl+C to stop)")
                time.sleep(POLL_MINUTES * 60)
        except KeyboardInterrupt:
            console.print("Stopped by user.")
    else:
        res = run_once(symbols)
        pretty_print(res)
        save_results(res)
