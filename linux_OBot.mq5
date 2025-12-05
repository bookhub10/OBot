//+------------------------------------------------------------------+
//|                        OBotTrading_RL_v1.mq5                     |
//+------------------------------------------------------------------+
#property copyright "OBot RL System"
#property version   "1.0"
#property description "Reinforcement Learning Agent (ONNX)"

input string APIServerURL = "http://127.0.0.1:5000";
input int    MagicNumber  = 12345;
input double MaxLotSize   = 1.0;
input double FixLotSize   = 0.1; // RL มักจะเทรด Lot คงที่ตอนฝึก แนะนำให้ Fix ไว้ก่อน
input bool   UseMoneyMgmt = false; // ถ้า True จะคำนวณจาก SL (แต่ RL บางทีไม่มี SL)

// --- RL Specific Inputs ---
input int    CooldownBars = 0;   // Cooldown จัดการใน Python แล้ว ตรงนี้เป็น 0 ได้เลย
input int    MaxSpreadPoints = 35;

// --- Global Vars ---
string BotStatus = "STOPPED";
string LastAction = "NONE";
string LastReason = "";
double LastATR = 0.0;

// --- JSON Helpers ---
string ExtractJsonString(string json_data, string key) {
    string search = "\"" + key + "\":\"";
    int start = StringFind(json_data, search);
    if (start < 0) return "";
    start += StringLen(search);
    int end = StringFind(json_data, "\"", start);
    if (end < 0) return "";
    return StringSubstr(json_data, start, end - start);
}

double ExtractJsonDouble(string json_data, string key) {
    string search = "\"" + key + "\":";
    int start = StringFind(json_data, search);
    if (start < 0) return 0.0;
    start += StringLen(search);
    int end = StringFind(json_data, ",", start);
    int end2 = StringFind(json_data, "}", start);
    if (end < 0 || (end2 >= 0 && end2 < end)) end = end2;
    return StringToDouble(StringSubstr(json_data, start, end - start));
}

// --- Network Functions ---
string GetRatesJSON(string symbol, int bars) {
    MqlRates rates[];
    if (CopyRates(symbol, PERIOD_M5, 0, bars, rates) <= 0) return "[]";
    string json = "[";
    for(int i=0; i<ArraySize(rates); i++) {
        if(i>0) json += ",";
        json += StringFormat("{\"time\":%d,\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,\"tick_volume\":%d}",
        rates[i].time, rates[i].open, rates[i].high, rates[i].low, rates[i].close, rates[i].tick_volume);
    }
    return json + "]";
}

void GetActionFromAPI() {
    // 1. Prepare Data
    string m5_json = GetRatesJSON(_Symbol, 100); // ส่งไปแค่ 100 แท่งพอ (ประหยัด Bandwidth)
    string usd_json = "[]"; 
    // (ถ้ามี USD ให้เปิดบรรทัดล่างนี้)
    // usd_json = GetRatesJSON("UsDollar", 100); 

    string payload = StringFormat("{\"m5_data\":%s, \"usd_m5\":%s}", m5_json, usd_json);
    
    // 2. Send Request
    char data[];
    StringToCharArray(payload, data, 0, WHOLE_ARRAY);
    char res[]; string res_headers;
    string headers = "Content-Type: application/json";
    
    int code = WebRequest("POST", APIServerURL + "/predict", headers, 5000, data, res, res_headers);
    
    if (code == 200) {
        string json = CharArrayToString(res);
        LastAction = ExtractJsonString(json, "action");
        LastReason = ExtractJsonString(json, "reason");
        LastATR    = ExtractJsonDouble(json, "atr");
        
        Print("🤖 RL Agent Action: ", LastAction, " (Reason: ", LastReason, ")");
    } else {
        Print("❌ API Error: ", code);
        LastAction = "HOLD";
    }
}

void UpdateStatus() {
    char data[]; char res[]; string h;
    string url = APIServerURL + "/status";
    int code = WebRequest("GET", url, "", 2000, data, res, h);
    if(code==200) {
        BotStatus = ExtractJsonString(CharArrayToString(res), "status");
    }
}

// --- Trade Functions ---
void CloseAllPositions() {
    for(int i=PositionsTotal()-1; i>=0; i--) {
        ulong ticket = PositionGetTicket(i);
        if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
        if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
        
        trade.PositionClose(ticket);
        Print("🚫 RL Close Action Triggered.");
    }
}

void OpenTrade(ENUM_ORDER_TYPE type) {
    if(PositionsTotal() > 0) return; // เข้าทีละไม้ (Sniper Style)
    
    double price = (type==ORDER_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
    double sl = 0, tp = 0;
    
    // RL บางทีไม่ตั้ง SL/TP (เพราะมันจะ Close เอง) แต่เพื่อความปลอดภัยควรตั้ง
    if(LastATR > 0) {
        double dist = LastATR * 2.0; // SL กว้างๆ กันเหนียว
        if(type==ORDER_TYPE_BUY) sl = price - dist;
        else sl = price + dist;
    }

    trade.PositionOpen(_Symbol, type, FixLotSize, price, sl, tp);
}

#include <Trade\Trade.mqh>
CTrade trade;

// --- Main Loop ---
void OnTick() {
    static datetime last_bar = 0;
    datetime time = iTime(_Symbol, PERIOD_M5, 0);
    
    // เช็คทุกแท่งเทียนใหม่
    if(time != last_bar) {
        last_bar = time;
        
        UpdateStatus();
        if(BotStatus != "RUNNING") return;
        
        GetActionFromAPI();
        
        // --- EXECUTION ---
        if (LastAction == "CLOSE") {
            CloseAllPositions();
        }
        else if (LastAction == "BUY") {
            // ถ้ามี Sell อยู่ ปิดก่อน
            if(PositionSelect(_Symbol) && PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_SELL) CloseAllPositions();
            OpenTrade(ORDER_TYPE_BUY);
        }
        else if (LastAction == "SELL") {
            // ถ้ามี Buy อยู่ ปิดก่อน
            if(PositionSelect(_Symbol) && PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY) CloseAllPositions();
            OpenTrade(ORDER_TYPE_SELL);
        }
    }
}