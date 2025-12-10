# features.py
"""
🧠 OBot Feature Engineering - Unified Module
"""

import pandas as pd
import numpy as np
import talib

# =============================================================================
# 📋 Feature Lists
# =============================================================================

# Standard Features (19)
REQUIRED_FEATURES = [
    'log_ret_1', 'log_ret_5', 'dist_ema50', 'dist_h1_ema',
    'body_pct', 'upper_wick_pct', 'lower_wick_pct',
    'vol_force',
    'dist_pivot', 'dist_r1', 'dist_s1',
    'atr_14', 'atr_pct', 'rsi_14',
    'hour_sin', 'hour_cos',
    'usd_ret_5', 'usd_corr',
    'dist_ema200'
]

# Enhanced Features (45+)
ENHANCED_FEATURES = [
    # Original (19)
    'log_ret_1', 'log_ret_5', 'dist_ema50', 'dist_h1_ema',
    'body_pct', 'upper_wick_pct', 'lower_wick_pct', 'vol_force',
    'dist_pivot', 'dist_r1', 'dist_s1',
    'atr_14', 'atr_pct', 'rsi_14',
    'hour_sin', 'hour_cos',
    'usd_ret_5', 'usd_corr', 'dist_ema200',
    
    # Market Regime (6)
    'vol_regime', 'adx', 'trend_strength',
    'session_asian', 'session_london', 'session_ny',
    
    # Order Flow (4)
    'net_pressure', 'vol_spike', 'pv_divergence',
    
    # Momentum (7)
    'rsi_7', 'rsi_21', 'macd_hist', 'macd_cross',
    'stoch_k', 'stoch_d', 'roc_10',
    
    # Support/Resistance (6)
    'dist_swing_high', 'dist_swing_low', 'bb_position',
    'dist_fib_382', 'dist_fib_618'
]

# 🌐 MTF Features (Phase 2: Multi-Timeframe) - 10 new features
MTF_FEATURES = [
    # H1 Timeframe (3)
    'h1_trend',          # H1 EMA20 vs EMA50 direction (-1, 0, 1)
    'h1_rsi',            # H1 RSI 14
    'h1_momentum',       # H1 price momentum (ROC)
    
    # H4 Timeframe (3)
    'h4_trend',          # H4 EMA20 vs EMA50 direction
    'h4_rsi',            # H4 RSI 14
    'h4_above_ema200',   # H4 price above EMA200 (0 or 1)
    
    # D1 Timeframe (2)
    'd1_trend',          # D1 EMA20 vs EMA50 direction
    'd1_above_ema200',   # D1 price above EMA200
    
    # Confluence (2)
    'mtf_confluence',    # Combined MTF score (-1 to 1)
    'mtf_alignment'      # All TFs aligned (0 or 1)
]

# 🌡️ Regime Features (Phase 3: Market Regime Adaptation) - 6 new features
REGIME_FEATURES = [
    'regime_trending',    # 1 if trending, 0 otherwise
    'regime_ranging',     # 1 if ranging, 0 otherwise
    'regime_volatile',    # 1 if volatile, 0 otherwise
    'regime_quiet',       # 1 if quiet, 0 otherwise
    'atr_ratio',          # Current ATR / ATR MA (normalized)
    'regime_multiplier'   # Position size multiplier (0.3 - 1.2)
]

# =============================================================================
# 🔧 Standard Feature Engineering
# =============================================================================

def compute_features(df_m5, df_usd=None):
    """
    ฟังก์ชันคำนวณ Indicator มาตรฐาน (ใช้ร่วมกันทั้ง Training และ Production)
    
    Args:
        df_m5: DataFrame with OHLCV data (M5)
        df_usd: Optional DataFrame for USD correlation
        
    Returns:
        DataFrame with 19 standard features + ema200
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

    # --- 0. Trend Filter ---
    df['ema200'] = talib.EMA(close_p, timeperiod=200)
    df['ema200'] = df['ema200'].bfill().fillna(close_p[0])
    df['dist_ema200'] = (close_p - df['ema200']) / close_p  # Normalized distance
    
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

    # Return all calculated columns before filtering in the main script
    # This allows flexibility to extract 'ema200' for prices but 'dist_ema200' for features
    return df.replace([np.inf, -np.inf], np.nan).dropna()

# =============================================================================
# 🔥 Enhanced Feature Engineering
# =============================================================================

def _compute_market_regime(df):
    """เพิ่ม Market Regime Detection"""
    close = df['close'].values
    
    # 1. Volatility Regime (ATR-based)
    atr_14 = talib.ATR(df['high'].values, df['low'].values, close, 14)
    atr_50 = talib.SMA(atr_14, 50)
    
    df['vol_regime'] = np.where(atr_14 > atr_50 * 1.5, 2,  # High Vol
                        np.where(atr_14 > atr_50 * 0.8, 1,  # Normal Vol
                                 0))                         # Low Vol
    
    # 2. Trend Strength (ADX)
    df['adx'] = talib.ADX(df['high'].values, df['low'].values, close, 14)
    df['trend_strength'] = np.where(df['adx'] > 25, 1, 0)  # 1 = Trending, 0 = Ranging
    
    # 3. Market Session
    hour = df.index.hour
    df['session_asian'] = ((hour >= 0) & (hour < 8)).astype(int)
    df['session_london'] = ((hour >= 8) & (hour < 16)).astype(int)
    df['session_ny'] = ((hour >= 13) & (hour < 21)).astype(int)
    
    return df


def _compute_order_flow_features(df):
    """Order Flow Analysis"""
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    volume = df['tick_volume'].values
    
    # 1. Buying/Selling Pressure
    df['buy_pressure'] = ((close - low) / (high - low + 1e-9)) * volume
    df['sell_pressure'] = ((high - close) / (high - low + 1e-9)) * volume
    df['net_pressure'] = df['buy_pressure'] - df['sell_pressure']
    
    # 2. Volume Profile (Recent vs Historical)
    vol_ma_20 = talib.SMA(volume, 20)
    df['vol_spike'] = np.where(volume > vol_ma_20 * 2, 1, 0)
    
    # 3. Price-Volume Divergence
    price_change = df['close'].pct_change(5)
    volume_change = df['tick_volume'].pct_change(5)
    df['pv_divergence'] = price_change * volume_change  # ถ้า < 0 = divergence
    
    return df


def _compute_momentum_features(df):
    """Advanced Momentum Indicators"""
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    
    # 1. Multiple Timeframe RSI
    df['rsi_7'] = talib.RSI(close, 7)
    df['rsi_14'] = talib.RSI(close, 14)
    df['rsi_21'] = talib.RSI(close, 21)
    
    # 2. MACD
    macd, signal, hist = talib.MACD(close, 12, 26, 9)
    df['macd_hist'] = hist
    df['macd_cross'] = np.where(hist > 0, 1, -1)
    
    # 3. Stochastic
    slowk, slowd = talib.STOCH(high, low, close, fastk_period=14, 
                                slowk_period=3, slowd_period=3)
    df['stoch_k'] = slowk
    df['stoch_d'] = slowd
    
    # 4. Rate of Change
    df['roc_10'] = talib.ROC(close, 10)
    
    return df


def _compute_support_resistance(df):
    """Dynamic Support/Resistance"""
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    
    # 1. Recent Swing High/Low
    df['swing_high'] = df['high'].rolling(20).max()
    df['swing_low'] = df['low'].rolling(20).min()
    df['dist_swing_high'] = (close - df['swing_high']) / close
    df['dist_swing_low'] = (close - df['swing_low']) / close
    
    # 2. Bollinger Bands
    upper, middle, lower = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)
    df['bb_upper'] = upper
    df['bb_lower'] = lower
    df['bb_position'] = (close - lower) / (upper - lower + 1e-9)  # 0-1 normalized
    
    # 3. Fibonacci Levels (Daily)
    df_daily = df.resample('D').agg({'high': 'max', 'low': 'min', 'close': 'last'})
    df_daily['fib_382'] = df_daily['low'] + (df_daily['high'] - df_daily['low']) * 0.382
    df_daily['fib_618'] = df_daily['low'] + (df_daily['high'] - df_daily['low']) * 0.618
    
    df['fib_382'] = df_daily['fib_382'].reindex(df.index, method='ffill')
    df['fib_618'] = df_daily['fib_618'].reindex(df.index, method='ffill')
    df['dist_fib_382'] = (close - df['fib_382']) / close
    df['dist_fib_618'] = (close - df['fib_618']) / close
    
    return df


def compute_enhanced_features(df_m5, df_usd=None):
    """
    รวม Features ทั้งหมด (45+ indicators)
    
    Args:
        df_m5: DataFrame with OHLCV data (M5)
        df_usd: Optional DataFrame for USD correlation
        
    Returns:
        DataFrame with enhanced features
    """
    df = df_m5.sort_index().copy()
    
    # Original Features (from compute_features)
    df = compute_features(df, df_usd)
    
    # New Features
    df = _compute_market_regime(df)
    df = _compute_order_flow_features(df)
    df = _compute_momentum_features(df)
    df = _compute_support_resistance(df)
    
    return df.replace([np.inf, -np.inf], np.nan).dropna()


# =============================================================================
# 🌐 Multi-Timeframe Features (Phase 2)
# =============================================================================

def _compute_single_tf_features(df, prefix):
    """
    คำนวณ Features สำหรับ Timeframe เดียว
    
    Args:
        df: DataFrame with OHLCV
        prefix: 'h1', 'h4', 'd1'
    """
    close = df['close'].values
    
    # Trend: EMA20 vs EMA50
    ema20 = talib.EMA(close, 20)
    ema50 = talib.EMA(close, 50)
    ema200 = talib.EMA(close, 200)
    
    # Trend direction: -1 (down), 0 (neutral), 1 (up)
    trend = np.where(ema20 > ema50 * 1.001, 1,
                     np.where(ema20 < ema50 * 0.999, -1, 0))
    
    # RSI
    rsi = talib.RSI(close, 14)
    
    # Momentum (ROC 10)
    momentum = talib.ROC(close, 10)
    
    # Above EMA200
    above_ema200 = (close > ema200).astype(int)
    
    return {
        f'{prefix}_trend': trend,
        f'{prefix}_rsi': rsi / 100.0,  # Normalize 0-1
        f'{prefix}_momentum': momentum / 100.0 if momentum is not None else 0,
        f'{prefix}_above_ema200': above_ema200
    }


def _compute_mtf_features(df_m5, df_h1=None, df_h4=None, df_d1=None):
    """
    คำนวณ Multi-Timeframe Features
    
    Args:
        df_m5: M5 DataFrame (base)
        df_h1: H1 DataFrame (optional)
        df_h4: H4 DataFrame (optional)
        df_d1: D1 DataFrame (optional)
        
    Returns:
        DataFrame with MTF features aligned to M5 index
    """
    df = df_m5.copy()
    
    # Initialize with zeros
    for feat in MTF_FEATURES:
        df[feat] = 0.0
    
    # H1 Features
    if df_h1 is not None and len(df_h1) > 200:
        h1_feats = _compute_single_tf_features(df_h1, 'h1')
        for key, values in h1_feats.items():
            if key in df.columns:
                # Create series with H1 index, then reindex to M5
                h1_series = pd.Series(values, index=df_h1.index)
                df[key] = h1_series.reindex(df.index, method='ffill').fillna(0)
    
    # H4 Features
    if df_h4 is not None and len(df_h4) > 200:
        h4_feats = _compute_single_tf_features(df_h4, 'h4')
        for key, values in h4_feats.items():
            if key in df.columns:
                h4_series = pd.Series(values, index=df_h4.index)
                df[key] = h4_series.reindex(df.index, method='ffill').fillna(0)
    
    # D1 Features
    if df_d1 is not None and len(df_d1) > 200:
        d1_feats = _compute_single_tf_features(df_d1, 'd1')
        for key, values in d1_feats.items():
            if key in df.columns:
                d1_series = pd.Series(values, index=df_d1.index)
                df[key] = d1_series.reindex(df.index, method='ffill').fillna(0)
    
    # Confluence Score: Weighted average of trends
    # H1 weight=1, H4 weight=2, D1 weight=3
    df['mtf_confluence'] = (
        df['h1_trend'] * 1 + 
        df['h4_trend'] * 2 + 
        df['d1_trend'] * 3
    ) / 6.0  # Normalized -1 to 1
    
    # Alignment: All timeframes agree
    df['mtf_alignment'] = (
        (df['h1_trend'] == df['h4_trend']) & 
        (df['h4_trend'] == df['d1_trend']) &
        (df['h1_trend'] != 0)
    ).astype(int)
    
    return df


def compute_mtf_enhanced_features(df_m5, df_h1=None, df_h4=None, df_d1=None, df_usd=None):
    """
    🌐 Full Feature Set with MTF (Phase 2)
    
    รวม Enhanced Features (45+) + MTF Features (10) = 55+ features
    
    Args:
        df_m5: M5 DataFrame (required)
        df_h1: H1 DataFrame (optional)
        df_h4: H4 DataFrame (optional)
        df_d1: D1 DataFrame (optional)
        df_usd: USD Index for correlation (optional)
        
    Returns:
        DataFrame with all features
    """
    # First compute all enhanced features
    df = compute_enhanced_features(df_m5, df_usd)
    
    # Then add MTF features
    df = _compute_mtf_features(df, df_h1, df_h4, df_d1)
    
    print(f"🌐 MTF Features computed: {len(df)} rows, {len(df.columns)} columns")
    print(f"   H1: {'✅' if df_h1 is not None else '❌'}")
    print(f"   H4: {'✅' if df_h4 is not None else '❌'}")
    print(f"   D1: {'✅' if df_d1 is not None else '❌'}")
    
    return df.replace([np.inf, -np.inf], np.nan).dropna()


# =============================================================================
# 🌡️ Regime Features (Phase 3)
# =============================================================================

def _compute_regime_features(df, lookback=50):
    """
    คำนวณ Regime Features
    
    Args:
        df: DataFrame with 'adx' and 'atr_14' columns (from enhanced features)
        lookback: bars for ATR moving average
        
    Returns:
        DataFrame with regime features added
    """
    # ATR Moving Average
    df['atr_ma'] = df['atr_14'].rolling(window=lookback, min_periods=10).mean()
    df['atr_ratio'] = df['atr_14'] / df['atr_ma']
    df['atr_ratio'] = df['atr_ratio'].fillna(1.0)
    
    # Regime Detection Thresholds
    ADX_TRENDING = 25
    ADX_RANGING = 20
    ATR_VOLATILE = 2.0
    ATR_QUIET = 0.5
    
    # One-hot encode regimes
    df['regime_volatile'] = (df['atr_ratio'] > ATR_VOLATILE).astype(float)
    df['regime_quiet'] = (df['atr_ratio'] < ATR_QUIET).astype(float)
    df['regime_trending'] = ((df['adx'] > ADX_TRENDING) & 
                              (df['regime_volatile'] == 0) & 
                              (df['regime_quiet'] == 0)).astype(float)
    df['regime_ranging'] = ((df['adx'] < ADX_RANGING) & 
                             (df['regime_volatile'] == 0) & 
                             (df['regime_quiet'] == 0)).astype(float)
    
    # Position Multiplier based on regime
    df['regime_multiplier'] = 1.0  # Default
    df.loc[df['regime_trending'] == 1, 'regime_multiplier'] = 1.2
    df.loc[df['regime_ranging'] == 1, 'regime_multiplier'] = 0.7
    df.loc[df['regime_volatile'] == 1, 'regime_multiplier'] = 0.5
    df.loc[df['regime_quiet'] == 1, 'regime_multiplier'] = 0.3
    
    # Clean up temp column
    df = df.drop('atr_ma', axis=1, errors='ignore')
    
    return df


def compute_regime_enhanced_features(df_m5, df_h1=None, df_h4=None, df_d1=None, df_usd=None):
    """
    🌡️ Full Feature Set with Regime (Phase 3)
    
    รวม Enhanced Features (45+) + MTF Features (10) + Regime Features (6) = 61+ features
    
    Args:
        df_m5: M5 DataFrame (required)
        df_h1: H1 DataFrame (optional)
        df_h4: H4 DataFrame (optional)
        df_d1: D1 DataFrame (optional)
        df_usd: USD Index for correlation (optional)
        
    Returns:
        DataFrame with all features including regime
    """
    # First compute MTF enhanced features
    df = compute_mtf_enhanced_features(df_m5, df_h1, df_h4, df_d1, df_usd)
    
    # Then add Regime features
    df = _compute_regime_features(df)
    
    print(f"🌡️ Regime Features computed: {len(df)} rows, {len(df.columns)} columns")
    
    # Count regime distribution
    if 'regime_trending' in df.columns:
        regime_counts = {
            'Trending': (df['regime_trending'] == 1).sum(),
            'Ranging': (df['regime_ranging'] == 1).sum(),
            'Volatile': (df['regime_volatile'] == 1).sum(),
            'Quiet': (df['regime_quiet'] == 1).sum()
        }
        total = sum(regime_counts.values())
        for regime, count in regime_counts.items():
            pct = (count / total * 100) if total > 0 else 0
            print(f"   {regime}: {count:,} ({pct:.1f}%)")
    
    return df.replace([np.inf, -np.inf], np.nan).dropna()