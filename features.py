# features.py
import pandas as pd
import numpy as np
import talib

REQUIRED_FEATURES = [
    'log_ret_1', 'log_ret_5', 'dist_ema50', 'dist_h1_ema',
    'body_pct', 'upper_wick_pct', 'lower_wick_pct',
    'vol_force',
    'dist_pivot', 'dist_r1', 'dist_s1',
    'atr_14', 'atr_pct', 'rsi_14',
    'hour_sin', 'hour_cos',
    'usd_ret_5', 'usd_corr'
]

def compute_features(df_m5, df_usd=None):
    """
    ฟังก์ชันคำนวณ Indicator มาตรฐาน (ใช้ร่วมกันทั้ง Training และ Production)
    """
    df = df_m5.sort_index().copy()
    
    # แปลงข้อมูลเป็น float เพื่อความชัวร์
    for col in ['open','high','low','close','tick_volume']:
        if col in df.columns:
            df[col] = df[col].astype(float)
    
    close_p = df['close'].values
    open_p  = df['open'].values
    high_p  = df['high'].values
    low_p   = df['low'].values
    volume  = df['tick_volume'].values if 'tick_volume' in df.columns else np.zeros_like(close_p)

    # --- 1. Momentum & Trend ---
    df['log_ret_1'] = np.log(df['close'] / df['close'].shift(1))
    df['log_ret_5'] = np.log(df['close'] / df['close'].shift(5))
    
    ema50 = talib.EMA(close_p, timeperiod=50)
    df['dist_ema50'] = (close_p - ema50) / close_p
    
    # H1 Context (Resample)
    df_h1 = df.resample('1h').agg({'close': 'last'}).dropna()
    df_h1['ema50_h1'] = talib.EMA(df_h1['close'].values, timeperiod=50)
    df['ema50_h1'] = df_h1['ema50_h1'].reindex(df.index, method='ffill')
    df['dist_h1_ema'] = (df['close'] - df['ema50_h1']) / df['close']

    # --- 2. Candle Psychology ---
    candle_range = (high_p - low_p) + 1e-9
    df['body_pct'] = np.abs(close_p - open_p) / candle_range
    df['upper_wick_pct'] = (high_p - np.maximum(close_p, open_p)) / candle_range
    df['lower_wick_pct'] = (np.minimum(close_p, open_p) - low_p) / candle_range

    # --- 3. Volume Force ---
    vol_sma = talib.SMA(volume, timeperiod=20) + 1e-9
    df['vol_force'] = (volume * np.sign(close_p - open_p)) / vol_sma

    # --- 4. Pivots ---
    df_day = df.resample('D').agg({'high':'max','low':'min','close':'last'}).shift(1).dropna()
    df_day['Pivot'] = (df_day['high'] + df_day['low'] + df_day['close']) / 3
    df_day['R1'] = (2 * df_day['Pivot']) - df_day['low']
    df_day['S1'] = (2 * df_day['Pivot']) - df_day['high']
    
    df['Pivot'] = df_day['Pivot'].reindex(df.index, method='ffill')
    df['R1'] = df_day['R1'].reindex(df.index, method='ffill')
    df['S1'] = df_day['S1'].reindex(df.index, method='ffill')
    
    df['dist_pivot'] = (close_p - df['Pivot']) / close_p
    df['dist_r1'] = (close_p - df['R1']) / close_p
    df['dist_s1'] = (close_p - df['S1']) / close_p

    # --- 5. Volatility & Time ---
    df['atr_14'] = talib.ATR(high_p, low_p, close_p, timeperiod=14)
    df['atr_pct'] = df['atr_14'] / close_p
    df['rsi_14'] = talib.RSI(close_p, timeperiod=14)
    df['hour_sin'] = np.sin(2 * np.pi * df.index.hour / 24.0)
    df['hour_cos'] = np.cos(2 * np.pi * df.index.hour / 24.0)

    # --- 6. Intermarket Analysis (USD) ---
    if df_usd is not None and not df_usd.empty:
        # Align USD to M5
        usd_close = df_usd['close'].reindex(df.index, method='ffill').bfill()
        usd_vals = usd_close.astype(float).values
        
        df['usd_ret_5'] = np.log(usd_vals / (pd.Series(usd_vals).shift(5).values + 1e-9))
        df['usd_corr'] = df['close'].rolling(12).corr(usd_close)
    else:
        df['usd_ret_5'] = 0.0
        df['usd_corr'] = 0.0

    # Clean & Select
    for col in REQUIRED_FEATURES:
        if col not in df.columns: df[col] = 0.0
            
    df = df[REQUIRED_FEATURES].replace([np.inf, -np.inf], np.nan).dropna()
    return df