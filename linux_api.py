import pandas as pd
import numpy as np
import onnxruntime as ort
import pickle
import threading
import time
import json
import talib
import subprocess
import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import pytz
import traceback
from pathlib import Path

# --- News Filter Imports ---
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

# Import Features - ใช้ Regime Enhanced Features (76 features) ตรงกับ Training
try:
    from features import compute_regime_enhanced_features
except ImportError:
    print("❌ Critical: features.py not found! System cannot calculate indicators.")

# Import Safety Monitor
try:
    from linux_safety import TradingSafetyMonitor
except ImportError:
    print("⚠️ Warning: linux_safety.py not found. Safety features disabled.")
    TradingSafetyMonitor = None
    
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    load_dotenv(dotenv_path=env_path, override=True)
except ImportError:
    print("⚠️  Warning: python-dotenv not installed. Trying to read .env manually.")

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip() 
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '').strip()

GLOBAL_PATH = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(GLOBAL_PATH, "models/model.onnx")
SCALER_PATH = os.path.join(GLOBAL_PATH, "models/scaler_rl.pkl")

# URLs Update
GITHUB_BASE = "https://raw.githubusercontent.com/bookhub10/OBot/main"
GITHUB_MODEL_URL = f"{GITHUB_BASE}/models/model.onnx"
GITHUB_SCALER_URL = f"{GITHUB_BASE}/models/scaler_rl.pkl"
GITHUB_MODEL_DATA_URL = f"{GITHUB_BASE}/models/model.onnx.data"
GITHUB_ZIP_URL = f"{GITHUB_BASE}/models/ppo_xauusd.zip"
GITHUB_API_URL = f"{GITHUB_BASE}/linux_api.py"
GITHUB_TELEGRAM_URL = f"{GITHUB_BASE}/linux_telegram.py"
GITHUB_FEATURES_URL = f"{GITHUB_BASE}/features.py"
GITHUB_SAFETY_URL = f"{GITHUB_BASE}/linux_safety.py"
EA_URL = f"{GITHUB_BASE}/linux_OBot.mq5"

# Trading Params (Match Training Env)
MIN_ATR = 1.0
EMA_PERIOD = 200
SPREAD_COST = 0.35      # Training spread value
TRAINING_BALANCE = 1000.0

# News Params - Enhanced Version
TARGET_CURRENCY = 'USD'
MIN_IMPACT = 'High'  # Filter: High impact only
LOCKDOWN_MINUTES_BEFORE = 30   # Lock 30 min before
LOCKDOWN_MINUTES_AFTER = 15    # Lock 15 min after
WARNING_MINUTES = 120          # Warn 2 hours before (reduce position)
NEWS_UPDATE_INTERVAL = 300     # Check every 5 min

# ForexFactory Free JSON API
FOREX_FACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Gold-specific high impact events
GOLD_HIGH_IMPACT_EVENTS = [
    'Non-Farm Employment Change',
    'Nonfarm Payrolls',
    'Federal Funds Rate',
    'FOMC Statement',
    'Consumer Price Index',
    'CPI m/m',
    'Core CPI m/m',
    'GDP',
    'Unemployment Rate',
    'Retail Sales m/m',
    'Fed Chair Powell Speaks',
    'ISM Manufacturing PMI',
]

app = Flask(__name__)

# ==========================================
# 🌍 GLOBAL STATE
# ==========================================
ort_session = None
scaler = None
input_name = None
output_name = None
cooldown_counter = 0

# 🛡️ Safety Monitor (initialized on bot start)
safety_monitor = None

bot_status = {
    "status": "STOPPED",
    "last_action": "NONE",
    "last_confidence": 0.0,
    "news_lock": False,
    "news_message": "Initializing...",
    "news_risk_multiplier": 1.0,  # NEW: 0.0-1.0 based on event proximity
    "news_next_event": None,       # NEW: Next upcoming event
    "balance": 0.0,
    "equity": 0.0,
    "margin_free": 0.0,
    "open_trades": 0,
    "model_loaded": False,
    # Safety Monitor fields
    "safety_enabled": False,
    "safety_halted": False,
    "daily_pnl": 0.0,
    "current_drawdown": 0.0
}

# ==========================================
# 📢 NOTIFICATION SYSTEM
# ==========================================
def send_telegram_msg(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        threading.Thread(target=requests.post, args=(url,), kwargs={'json': payload}).start()
    except Exception as e:
        print(f"❌ Telegram Error: {e}")

# ==========================================
# 🛠️ SYSTEM FUNCTIONS
# ==========================================
def download_file(url, path):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        print(f"⬇️ Downloading {path}...")
        r = requests.get(url)
        r.raise_for_status()
        with open(path, 'wb') as f:
            f.write(r.content)
        print(f"✅ Downloaded: {path}")
        return True
    except Exception as e:
        print(f"❌ Download Failed: {e}")
        return False

# ==========================================
# 📰 NEWS FILTER MODULE (Enhanced - ForexFactory JSON)
# ==========================================

def fetch_forexfactory_events():
    """
    Fetch economic calendar from ForexFactory JSON API (FREE!)
    
    Returns:
        list: List of high-impact USD events for today/tomorrow
    """
    try:
        response = requests.get(FOREX_FACTORY_URL, timeout=15)
        if response.status_code != 200:
            return [], "API Error"
        
        events = response.json()
        now_utc = datetime.now(pytz.utc)
        high_impact_events = []
        
        for event in events:
            # Filter: USD only
            if event.get('country', '').upper() != TARGET_CURRENCY:
                continue
            
            # Filter: High impact only
            if event.get('impact', '').lower() != 'high':
                continue
            
            # Parse event time
            try:
                event_time_str = event.get('date', '')
                event_dt = datetime.strptime(event_time_str, '%Y-%m-%dT%H:%M:%S%z')
                
                # Only events within next 24 hours
                time_diff = (event_dt - now_utc).total_seconds() / 60  # minutes
                if -60 <= time_diff <= 1440:  # -1 hour to +24 hours
                    high_impact_events.append({
                        'title': event.get('title', 'Unknown'),
                        'time': event_dt,
                        'minutes_until': time_diff,
                        'impact': event.get('impact', 'High'),
                        'forecast': event.get('forecast', ''),
                        'previous': event.get('previous', '')
                    })
            except Exception as e:
                continue
        
        # Sort by time
        high_impact_events.sort(key=lambda x: x['minutes_until'])
        
        return high_impact_events, "OK"
        
    except Exception as e:
        print(f"ForexFactory Error: {e}")
        return [], f"Error: {str(e)}"


def check_news_risk():
    """
    Check news risk and return:
    - lock: bool (should block trading)
    - risk_multiplier: float (0.0-1.0 for position sizing)
    - message: str (status message)
    - next_event: dict (next upcoming event)
    """
    events, status = fetch_forexfactory_events()
    
    if not events:
        return False, 1.0, "No high impact news today.", None
    
    # Check closest event
    closest = events[0]
    minutes_until = closest['minutes_until']
    event_title = closest['title']
    
    # Check if event is Gold-specific
    is_gold_specific = any(gold_event.lower() in event_title.lower() 
                           for gold_event in GOLD_HIGH_IMPACT_EVENTS)
    
    # Time-based risk calculation
    if minutes_until < 0 and minutes_until >= -LOCKDOWN_MINUTES_AFTER:
        # During/just after event - FULL LOCK
        return True, 0.0, f"🔴 LOCKED: {event_title} (Just released)", closest
    
    elif 0 <= minutes_until <= LOCKDOWN_MINUTES_BEFORE:
        # Just before event - FULL LOCK
        return True, 0.0, f"🔴 LOCKED: {event_title} in {int(minutes_until)} min", closest
    
    elif LOCKDOWN_MINUTES_BEFORE < minutes_until <= WARNING_MINUTES:
        # Warning zone - REDUCE RISK
        # Linear interpolation: 30min=0.3, 120min=1.0
        risk_mult = 0.3 + (minutes_until - LOCKDOWN_MINUTES_BEFORE) / (WARNING_MINUTES - LOCKDOWN_MINUTES_BEFORE) * 0.7
        risk_mult = min(max(risk_mult, 0.3), 1.0)
        
        if is_gold_specific:
            risk_mult *= 0.7  # Extra reduction for Gold-specific events
        
        return False, risk_mult, f"⚠️ WARNING: {event_title} in {int(minutes_until)} min (Risk: {risk_mult:.0%})", closest
    
    else:
        # Safe zone
        return False, 1.0, f"✅ Next: {event_title} in {int(minutes_until)} min", closest


def news_scheduler():
    """
    Enhanced news scheduler with risk multiplier
    """
    last_lock_state = False
    last_multiplier = 1.0
    print("🕰️ News Scheduler: Started (ForexFactory JSON)")
    
    while True:
        try:
            locked, risk_mult, msg, next_event = check_news_risk()
            
            bot_status['news_lock'] = locked
            bot_status['news_message'] = msg
            bot_status['news_risk_multiplier'] = risk_mult
            bot_status['news_next_event'] = {
                'title': next_event['title'],
                'minutes': int(next_event['minutes_until'])
            } if next_event else None
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            if locked:
                status_icon = "🔴"
            elif risk_mult < 1.0:
                status_icon = "⚠️"
            else:
                status_icon = "🟢"
            
            print(f"[{timestamp}] News: {status_icon} {msg}")
            
            # Telegram alerts
            if locked and not last_lock_state:
                send_telegram_msg(f"⛔ **NEWS LOCK:** Trading Blocked!\n{msg}")
            elif not locked and last_lock_state:
                send_telegram_msg(f"✅ **NEWS CLEAR:** Trading Resumed.\n{msg}")
            elif risk_mult < 0.7 and last_multiplier >= 0.7:
                send_telegram_msg(f"⚠️ **NEWS WARNING:** Position size reduced!\n{msg}")
            
            last_lock_state = locked
            last_multiplier = risk_mult
            
        except Exception as e:
            print(f"News Scheduler Error: {e}")
            bot_status['news_message'] = f"Error: {str(e)}"
        
        time.sleep(NEWS_UPDATE_INTERVAL)

# ==========================================
# 🧠 AI ENGINE
# ==========================================
def load_brain():
    global ort_session, scaler, input_name, output_name
    try:
        ort_session = ort.InferenceSession(MODEL_PATH)
        input_name = ort_session.get_inputs()[0].name
        output_name = ort_session.get_outputs()[0].name
        with open(SCALER_PATH, 'rb') as f: scaler = pickle.load(f)
        bot_status['model_loaded'] = True
        print("✅ Brain Loaded Successfully")
        return True
    except Exception as e:
        print(f"❌ Brain Error: {e}")
        bot_status['model_loaded'] = False
        return False

# ==========================================
# 🚀 API ENDPOINTS
# ==========================================
@app.route('/update_status', methods=['POST'])
def update_status():
    global safety_monitor
    try:
        data = request.get_json(force=True, silent=True)
        if data:
            bot_status['balance'] = float(data.get('balance', 0))
            bot_status['equity'] = float(data.get('equity', 0))
            bot_status['margin_free'] = float(data.get('margin_free', 0))
            bot_status['open_trades'] = int(data.get('open_trades', 0))
            
            # 🛡️ Update Safety Monitor
            if safety_monitor and bot_status['equity'] > 0:
                trade_pnl = float(data.get('last_trade_pnl', 0))  # Optional: from EA
                safety_monitor.update(
                    current_equity=bot_status['equity'],
                    trade_pnl=trade_pnl if trade_pnl != 0 else None
                )
                
                # Update bot_status with safety info
                safety_status = safety_monitor.get_status()
                bot_status['daily_pnl'] = safety_status.get('daily_pnl', 0)
                bot_status['current_drawdown'] = safety_status.get('current_drawdown', 0)
                
        return jsonify({'status': 'SUCCESS'})
    except: return jsonify({'status': 'ERROR'}), 500

@app.route('/status', methods=['GET'])
def get_status():
    return jsonify(bot_status)

@app.route('/safety_status', methods=['GET'])
def get_safety_status():
    """Get detailed safety monitor status"""
    if not safety_monitor:
        return jsonify({
            'enabled': False,
            'message': 'Safety monitor not initialized'
        })
    
    status = safety_monitor.get_status()
    status['alerts'] = safety_monitor.alerts[-5:]  # Last 5 alerts
    return jsonify(status)
# 🔥 CORRECTED PREDICT ENDPOINT
@app.route('/predict', methods=['POST'])
def predict():
    global cooldown_counter, safety_monitor
    
    # 1. Check Pre-conditions
    if bot_status["status"] != "RUNNING": 
        return jsonify({"action": "HOLD", "reason": "STOPPED"})
    if bot_status["news_lock"]: 
        return jsonify({"action": "HOLD", "reason": "NEWS_FILTER", "message": bot_status["news_message"]})
    if not bot_status['model_loaded']:
        return jsonify({"action": "HOLD", "reason": "MODEL_NOT_LOADED"})
    
    # 🛡️ Safety Monitor Check
    if safety_monitor and not safety_monitor.can_trade():
        bot_status["safety_halted"] = True
        send_telegram_msg("🚨 **SAFETY HALT:** Trading stopped by safety monitor!")
        return jsonify({"action": "HOLD", "reason": "SAFETY_HALT", "message": "Risk limit exceeded"})

    try:
        req = request.get_json(force=True, silent=True)
        if not req: return jsonify({"action": "HOLD", "reason": "BAD_JSON"})
        
        # 2. Parse M5 Data (Base)
        df_m5 = pd.DataFrame(req.get('m5_data', []))
        pos_info = req.get('position', {'type':0, 'price':0})
        
        if df_m5.empty: return jsonify({"action": "HOLD", "reason": "NO_DATA"})
        
        df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s')
        df_m5.set_index('time', inplace=True)
        
        # Parse USD Data
        df_usd = pd.DataFrame(req.get('usd_m5', []))
        if not df_usd.empty: 
            df_usd['time'] = pd.to_datetime(df_usd['time'], unit='s')
            df_usd.set_index('time', inplace=True)
        else:
            df_usd = None

        # 🌐 Parse MTF Data (H1, H4, D1) from EA
        df_h1, df_h4, df_d1 = None, None, None
        
        h1_data = req.get('h1_data', [])
        if h1_data and len(h1_data) > 200:
            df_h1 = pd.DataFrame(h1_data)
            df_h1['time'] = pd.to_datetime(df_h1['time'], unit='s')
            df_h1.set_index('time', inplace=True)
            print(f"📊 H1 Data: {len(df_h1)} bars")
        
        h4_data = req.get('h4_data', [])
        if h4_data and len(h4_data) > 50:
            df_h4 = pd.DataFrame(h4_data)
            df_h4['time'] = pd.to_datetime(df_h4['time'], unit='s')
            df_h4.set_index('time', inplace=True)
            print(f"📊 H4 Data: {len(df_h4)} bars")
        
        d1_data = req.get('d1_data', [])
        if d1_data and len(d1_data) > 30:
            df_d1 = pd.DataFrame(d1_data)
            df_d1['time'] = pd.to_datetime(df_d1['time'], unit='s')
            df_d1.set_index('time', inplace=True)
            print(f"📊 D1 Data: {len(df_d1)} bars")

        # 3. Compute Features (76 Regime Enhanced Features with MTF!)
        df_feat = compute_regime_enhanced_features(df_m5, df_h1=df_h1, df_h4=df_h4, df_d1=df_d1, df_usd=df_usd)
        if df_feat.empty: return jsonify({"action": "HOLD", "reason": "FEAT_ERROR"})

        # 4. Get Real Values from MT5
        real_balance = float(req.get('balance', TRAINING_BALANCE)) 
        real_spread_val = float(req.get('spread', SPREAD_COST))

        # 5. Construct State (21 Inputs)
        curr_price = df_m5['close'].iloc[-1]
        
        # 🔥 FIX #1: Position Logic (Handle -1, 0, 1 correctly)
        pos_type = pos_info.get('type', 0)
        entry_price = float(pos_info.get('price', 0))
        
        # Convert EA encoding to environment encoding
        if pos_type == 1:      # EA Buy
            env_pos = 1.0
        elif pos_type == -1:   # EA Sell (CORRECTED FROM 2)
            env_pos = -1.0
        else:                  # Empty
            env_pos = 0.0
        
        # 🔥 FIX #2: Normalize Spread (Match Training Scale)
        spread_normalized = real_spread_val / TRAINING_BALANCE
        
        # PnL Calculation with Normalized Spread
        pnl_val = 0.0
        if env_pos == 1.0: # Buy
            pnl_val = (curr_price - entry_price) - spread_normalized * TRAINING_BALANCE
        elif env_pos == -1.0: # Sell
            pnl_val = (entry_price - curr_price) - spread_normalized * TRAINING_BALANCE
            
        # Normalize PnL
        pnl_pct = pnl_val / TRAINING_BALANCE

        # Cooldown State (1=Ready, 0=Busy)
        if cooldown_counter > 0:
            cooldown_val = 0.0
            cooldown_counter -= 1
        else: 
            cooldown_val = 1.0

        # 6. Inference Prep
        last_row = df_feat.iloc[[-1]]  # 76 Features (Regime Enhanced)
        
        # 🔥 FIX #3: ATR Check (Use Raw ATR from atr_pct)
        latest_atr_pct = last_row['atr_pct'].values[0]
        latest_atr_raw = curr_price * latest_atr_pct  # Convert back to raw ATR
        
        if latest_atr_raw < MIN_ATR: 
            return jsonify({
                "action": "HOLD", 
                "reason": "LOW_ATR", 
                "atr": float(latest_atr_raw),
                "atr_pct": float(latest_atr_pct)
            })

        # Scale Features & Concat State
        input_market = scaler.transform(last_row).astype(np.float32)
        full_input = np.concatenate((
            input_market[0], 
            [env_pos, pnl_pct, cooldown_val]
        )).reshape(1, -1).astype(np.float32)
        
        # Run ONNX
        logits = ort_session.run([output_name], {input_name: full_input})[0]
        action_idx = np.argmax(logits)
        action = ["HOLD", "BUY", "SELL", "CLOSE"][action_idx]
        confidence = float(np.max(logits))

        # Post-Processing
        if action == "CLOSE": cooldown_counter = 12
        
        # 🔥 FIX #4: EMA Filter (REMOVED - Let RL Learn Naturally)
        # If you want to keep it, add dist_ema200 to features.py instead
        # ema = talib.EMA(df_m5['close'].values, timeperiod=EMA_PERIOD)[-1]
        # if (action=="BUY" and curr_price < ema): action = "HOLD"
        # if (action=="SELL" and curr_price > ema): action = "HOLD"

        # Signal Alert
        if action != "HOLD" and action != bot_status["last_action"]:
            emoji = "🟢" if action == "BUY" else ("🔴" if action == "SELL" else "✂️")
            msg = (f"{emoji} **SIGNAL:** {action}\n"
                   f"Price: {curr_price:.2f}\n"
                   f"ATR: {latest_atr_raw:.2f}\n"
                   f"Confidence: {confidence:.3f}")
            send_telegram_msg(msg)

        bot_status["last_action"] = action
        bot_status["last_confidence"] = confidence
        
        return jsonify({
            "action": action, 
            "atr": float(latest_atr_raw),
            "confidence": confidence,
            "reason": "MODEL",
            "news_risk_multiplier": bot_status.get("news_risk_multiplier", 1.0),  # NEW: For EA position sizing
            "debug": {
                "position": env_pos,
                "pnl_pct": pnl_pct,
                "cooldown": cooldown_val,
                "spread_used": real_spread_val,
                "news_message": bot_status.get("news_message", "")
            }
        })

    except Exception as e:
        print(f"❌ Prediction Error: {e}")
        traceback.print_exc()
        return jsonify({"action": "HOLD", "reason": "ERROR", "error": str(e)})

@app.route('/command', methods=['POST'])
def execute_command():
    global safety_monitor
    try:
        cmd = request.json.get('command')
        if cmd == 'START':
            # 🛡️ Initialize Safety Monitor
            if TradingSafetyMonitor:
                safety_monitor = TradingSafetyMonitor(
                    max_daily_loss_pct=5,   # หยุดถ้าขาดทุน 5%/วัน
                    max_drawdown_pct=15     # หยุดถ้า DD เกิน 15%
                )
                bot_status['safety_enabled'] = True
                bot_status['safety_halted'] = False
                print("🛡️ Safety Monitor initialized")
            
            bot_status['status'] = 'RUNNING'
            send_telegram_msg("🟢 **OBot Started** (Safety Monitor Active)")
            return jsonify({'status': 'SUCCESS', 'message': 'Bot RUNNING'})
        
        elif cmd == 'STOP':
            bot_status['status'] = 'STOPPED'
            # Save safety report if exists
            if safety_monitor:
                try:
                    safety_monitor.save_report('safety_report.json')
                except: pass
            send_telegram_msg("🔴 **OBot Stopped**")
            return jsonify({'status': 'SUCCESS', 'message': 'Bot STOPPED'})
        
        elif cmd == 'RESET_SAFETY':
            # 🔄 Reset Safety Monitor (resume trading after halt)
            if TradingSafetyMonitor:
                safety_monitor = TradingSafetyMonitor(
                    max_daily_loss_pct=5,
                    max_drawdown_pct=15
                )
                bot_status['safety_halted'] = False
                send_telegram_msg("🔄 **Safety Monitor Reset** - Trading resumed")
                return jsonify({'status': 'SUCCESS', 'message': 'Safety monitor reset'})
            return jsonify({'status': 'FAIL', 'message': 'Safety monitor not available'})
        
        return jsonify({'status': 'FAIL'}), 400
    except Exception as e: 
        return jsonify({'status': 'ERROR', 'message': str(e)}), 500

@app.route('/restart', methods=['POST'])
def restart_service():
    try:
        commands = [
            ["sudo", "/bin/systemctl", "restart", "obot_mt5.service"],
            ["sudo", "/bin/systemctl", "restart", "obot_telegram.service"],
            ["sudo", "/bin/systemctl", "restart", "obot_api.service"]
        ]
        for cmd in commands:
            subprocess.run(cmd)
        return jsonify({'status': 'SUCCESS', 'message': 'Services restarting...'}), 200
    except Exception as e:
        return jsonify({'status': 'FAIL', 'message': str(e)}), 500

@app.route('/update_ea', methods=['POST'])
def update_expert_advisor(): 
    EA_PATH = "/home/hp/.mt5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/OBotTrading.mq5"
    TRIGGER_FILE = "/home/hp/Downloads/bot/COMPILE_NOW.trigger" 

    try:
        print(f"⬇️ Downloading new EA from {EA_URL}...")
        response = requests.get(EA_URL)
        response.raise_for_status()
        with open(EA_PATH, 'wb') as f:
            f.write(response.content)
        print("✅ EA Downloaded.")

        with open(TRIGGER_FILE, 'w') as f:
            f.write('triggered') 
        print(f"✅ Trigger file created at {TRIGGER_FILE}")

        return jsonify({
            'status': 'SUCCESS', 
            'message': '✅ EA Downloaded. Compile trigger issued.'
        }), 200

    except Exception as e:
        print(f"❌ Error in /update_ea: {e}")
        traceback.print_exc()
        return jsonify({'status': 'FAIL', 'message': str(e)}), 500

@app.route('/fix', methods=['POST'])
def fix_system():
    files = [
        (GITHUB_MODEL_URL, MODEL_PATH),
        (GITHUB_SCALER_URL, SCALER_PATH),
        (GITHUB_MODEL_DATA_URL, MODEL_PATH + ".data"),
        (GITHUB_ZIP_URL, os.path.join(GLOBAL_PATH, "models/ppo_xauusd.zip")),
        (GITHUB_API_URL, __file__),
        (GITHUB_TELEGRAM_URL, os.path.join(GLOBAL_PATH, "linux_telegram.py")),
        (GITHUB_FEATURES_URL, os.path.join(GLOBAL_PATH, "features.py")),
        (GITHUB_SAFETY_URL, os.path.join(GLOBAL_PATH, "linux_safety.py"))
    ]
    
    success = True
    for url, path in files:
        if not download_file(url, path): success = False
    
    if success and load_brain():
        return jsonify({'status': 'SUCCESS', 'message': 'System Updated & Reloaded'})
    return jsonify({'status': 'FAIL', 'message': 'Update Failed'})

if __name__ == '__main__':
    # Load Model Before Start
    load_brain()
    
    # Start News Thread
    threading.Thread(target=news_scheduler, daemon=True).start()
    
    print("🚀 OBot RL API Started on Port 5000")

    app.run(host='0.0.0.0', port=5000)
