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

# --- News Filter Imports ---
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

# Import Features
from features import compute_features 

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
# Paths
GLOBAL_PATH = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = "models/model.onnx"
SCALER_PATH = "models/scaler_rl.pkl"

# URLs (สำหรับสั่ง update/fix)
GITHUB_MODEL_URL = "https://raw.githubusercontent.com/bookhub10/models/main/models/model.onnx"
GITHUB_SCALER_URL = "https://raw.githubusercontent.com/bookhub10/models/main/models/scaler_rl.pkl"

GITHUB_API_URL = "https://raw.githubusercontent.com/bookhub10/models/main/linux_api.py"
GITHUB_TELEGRAM_URL = "https://raw.githubusercontent.com/bookhub10/models/main/linux_telegram.py"
GITHUB_FEATURES_URL = "https://raw.githubusercontent.com/bookhub10/models/main/features.py"
# Trading Params
MIN_ATR = 1.0
EMA_PERIOD = 200

# News Params
TARGET_CURRENCY = 'USD'
MIN_BULLS = 3
LOCKDOWN_MINUTES = 30
NEWS_UPDATE_INTERVAL = 1800

app = Flask(__name__)

# ==========================================
# 🌍 GLOBAL STATE
# ==========================================
ort_session = None
scaler = None
input_name = None
output_name = None
cooldown_counter = 0

# Status รวมญาติ (เก็บทั้งสถานะ Bot และข้อมูลพอร์ต)
bot_status = {
    "status": "STOPPED",
    "last_action": "NONE",
    "last_confidence": 0.0,
    "news_lock": False,
    "news_message": "Initializing...",
    "balance": 0.0,      # รับจาก EA
    "equity": 0.0,       # รับจาก EA
    "margin_free": 0.0,  # รับจาก EA
    "open_trades": 0,    # รับจาก EA
    "model_loaded": False
}

# ==========================================
# 🛠️ SYSTEM FUNCTIONS (ยกมาจากตัวเก่า)
# ==========================================
def download_file(url, path):
    try:
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
# 📰 NEWS FILTER MODULE
# ==========================================
def fetch_investing_news():
    url = "https://www.investing.com/economic-calendar/"
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.9'}
    try:
        response = cffi_requests.get(url, headers=headers, impersonate="chrome110", timeout=20)
        if response.status_code != 200: return False, "News HTTP Error"
        
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table', id='economicCalendarData')
        if not table: return False, "Table not found"

        active_lockdown = False
        msg = "No high impact news."
        now_utc = datetime.now(pytz.utc)
        
        for row in table.find_all('tr', class_='js-event-item'):
            currency_td = row.find('td', class_='flagCur')
            if not currency_td or TARGET_CURRENCY not in currency_td.text.strip().upper(): continue
            
            bulls = len(row.find('td', class_='sentiment').find_all('i', class_='grayFullBullishIcon'))
            if bulls < MIN_BULLS: continue

            time_str = row.find('td', class_='time').text.strip()
            event_name = row.find('td', class_='event').text.strip()

            try:
                event_time = datetime.strptime(time_str, "%H:%M")
                event_dt = now_utc.replace(hour=event_time.hour, minute=event_time.minute, second=0)
                if event_dt - timedelta(minutes=LOCKDOWN_MINUTES) <= now_utc <= event_dt + timedelta(minutes=LOCKDOWN_MINUTES):
                    return True, f"LOCKDOWN: {event_name} ({time_str})"
            except: continue
            
        return False, msg
    except Exception as e:
        print(f"News Error: {e}")
        return False, "News Error"

def news_scheduler():
    while True:
        locked, msg = fetch_investing_news()
        bot_status['news_lock'] = locked
        bot_status['news_message'] = msg
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

# 1. รับค่า Status จาก EA (เพื่อให้ Telegram อ่านได้)
@app.route('/update_status', methods=['POST'])
def update_status():
    try:
        data = request.get_json(force=True, silent=True)
        if data:
            bot_status['balance'] = float(data.get('balance', 0))
            bot_status['equity'] = float(data.get('equity', 0))
            bot_status['margin_free'] = float(data.get('margin_free', 0))
            bot_status['open_trades'] = int(data.get('open_trades', 0))
        return jsonify({'status': 'SUCCESS'})
    except: return jsonify({'status': 'ERROR'}), 500

# 2. ส่ง Status กลับไปให้ Telegram (รวมข้อมูลข่าว + พอร์ต)
@app.route('/status', methods=['GET'])
def get_status():
    # จัด Format ให้เหมือนตัวเก่าที่ Telegram ชอบ
    return jsonify({
        'bot_status': bot_status['status'],
        'news_status': bot_status['news_message'],
        'model_loaded': bot_status['model_loaded'],
        'last_signal': bot_status['last_action'],
        'balance': bot_status['balance'],
        'equity': bot_status['equity'],
        'margin_free': bot_status['margin_free'],
        'open_trades': bot_status['open_trades'],
        'last_regime': "RL-ONNX" # ใส่หลอกไว้ให้ Telegram ไม่ Error
    })

# 3. AI Predict (Logic ใหม่ 21 Inputs)
@app.route('/predict', methods=['POST'])
def predict():
    global cooldown_counter
    if bot_status["status"] != "RUNNING": return jsonify({"action": "HOLD", "reason": "STOPPED"})
    if bot_status["news_lock"]: return jsonify({"action": "HOLD", "reason": "NEWS_FILTER", "message": bot_status["news_message"]})

    try:
        req = request.get_json(force=True, silent=True)
        if not req: return jsonify({"action": "HOLD", "reason": "BAD_JSON"})
        
        df_m5 = pd.DataFrame(req.get('m5_data', []))
        pos_info = req.get('position', {'type':0, 'price':0})
        
        if df_m5.empty: return jsonify({"action": "HOLD", "reason": "NO_DATA"})
        
        # Prepare Data
        df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s')
        df_m5.set_index('time', inplace=True)
        df_usd = pd.DataFrame(req.get('usd_m5', []))
        if not df_usd.empty: 
            df_usd['time'] = pd.to_datetime(df_usd['time'], unit='s')
            df_usd.set_index('time', inplace=True)

        # Features
        df_feat = compute_features(df_m5, df_usd)
        if df_feat.empty: return jsonify({"action": "HOLD", "reason": "FEAT_ERROR"})

        # State Construction
        curr_price = df_m5['close'].iloc[-1]
        env_pos = 0.0
        if pos_info.get('type') == 1: env_pos = 1.0
        elif pos_info.get('type') == 2: env_pos = -1.0
        
        pnl_pct = 0.0
        if env_pos != 0: pnl_pct = (curr_price - float(pos_info.get('price', 0))) * env_pos / 1000.0

        if cooldown_counter > 0:
            cooldown_val = 0.0; cooldown_counter -= 1
        else: cooldown_val = 1.0

        # Inference
        last_row = df_feat.iloc[[-1]]
        latest_atr = last_row['atr_14'].values[0]
        
        if latest_atr < MIN_ATR: return jsonify({"action": "HOLD", "reason": "LOW_ATR", "atr": float(latest_atr)})

        input_market = scaler.transform(last_row).astype(np.float32)
        full_input = np.concatenate((input_market[0], [env_pos, pnl_pct, cooldown_val])).reshape(1, -1).astype(np.float32)
        
        logits = ort_session.run([output_name], {input_name: full_input})[0]
        action = ["HOLD", "BUY", "SELL", "CLOSE"][np.argmax(logits)]

        if action == "CLOSE": cooldown_counter = 12
        
        # Trend Filter
        ema = talib.EMA(df_m5['close'].values, timeperiod=EMA_PERIOD)[-1]
        if (action=="BUY" and curr_price<ema) or (action=="SELL" and curr_price>ema): action = "HOLD"

        bot_status["last_action"] = action
        return jsonify({"action": action, "atr": float(latest_atr), "reason": "MODEL"})

    except Exception as e:
        print(f"Pred Error: {e}")
        return jsonify({"action": "HOLD", "reason": "ERROR"})

# 4. Command Control (Start/Stop)
@app.route('/command', methods=['POST'])
def execute_command():
    """Endpoint for Telegram Bot or external system to send START/STOP commands."""
    try:
        command = request.json.get('command')
        
        if command == 'START':
            bot_status['bot_status'] = 'RUNNING'
            return jsonify({'status': 'SUCCESS', 'message': 'Bot set to RUNNING.'})
        
        elif command == 'STOP':
            bot_status['bot_status'] = 'STOPPED'
            return jsonify({'status': 'SUCCESS', 'message': 'Bot set to STOPPED.'})
        
        else:
            return jsonify({'status': 'FAIL', 'message': 'Invalid command.'}), 400

    except Exception as e:
        return jsonify({'status': 'ERROR', 'message': str(e)}), 500
# 5. Restart Service (Full System Restart)
@app.route('/restart', methods=['POST'])
def restart_service():
    """Endpoint to restart the service via systemd."""
    try:
        command = ["sudo", "/bin/systemctl", "restart", "obot_api.service"]
        command2 = ["sudo", "/bin/systemctl", "restart", "obot_telegram.service"]
        command3 = ["sudo", "/bin/systemctl", "restart", "obot_mt5.service"]

        subprocess.run(command3)
        subprocess.run(command2)
        subprocess.run(command)
        
        return jsonify({'status': 'SUCCESS', 'message': 'The service restart command issued.'}), 200
    except Exception as e:
        print(f"❌ Error in /restart: {e}")
        return jsonify({'status': 'FAIL', 'message': str(e)}), 500

# 6. Update EA (Download MQ5)
@app.route('/update_ea', methods=['POST'])
def update_expert_advisor():
    """
    [NEW VERSION] Downloads the EA and creates a trigger file.
    """
    EA_URL = 'https://raw.githubusercontent.com/bookhub10/models/main/linux_OBot.mq5' 
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
            'message': f'✅ EA Downloaded. Compile trigger issued to GUI watcher.'
        }), 200

    except Exception as e:
        print(f"❌ Error in /update_ea: {e}")
        traceback.print_exc()
        return jsonify({'status': 'FAIL', 'message': f'Error during EA update: {str(e)}'}), 500

# 7. Fix/Retrain (โหลด Model/files ใหม่จาก GitHub มาทับ)
@app.route('/fix', methods=['POST'])
def fix_system():
    s1 = download_file(GITHUB_MODEL_URL, MODEL_PATH)
    s2 = download_file(GITHUB_SCALER_URL, SCALER_PATH)
    s3 = download_file(GITHUB_API_URL, os.path.join(GLOBAL_PATH, "linux_api.py"))
    s4 = download_file(GITHUB_TELEGRAM_URL, os.path.join(GLOBAL_PATH, "linux_telegram.py"))
    s5 = download_file(GITHUB_FEATURES_URL, os.path.join(GLOBAL_PATH, "features.py"))
    
    if s1 and s2 and s3 and s4 and s5:
        if load_brain(): # โหลดเข้า RAM ทันที
            return jsonify({'status': 'SUCCESS', 'message': 'Model & Scaler Updated & Reloaded!'})
    return jsonify({'status': 'FAIL', 'message': 'Update Failed'})

if __name__ == '__main__':
    if load_brain():
        threading.Thread(target=news_scheduler, daemon=True).start()
        app.run(host='0.0.0.0', port=5000)