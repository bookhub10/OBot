//+------------------------------------------------------------------+
//|                        linux_OBot.mq5                         |
//|               RL Agent Client (CORRECTED VERSION)                |
//+------------------------------------------------------------------+
#property copyright "OBot RL System"
#property version   "1.0"
#property description "Reinforcement Learning Client"

#include <Trade\Trade.mqh>
CTrade trade;

// --- Inputs ---
input string APIServerURL = "http://127.0.0.1:5000";
input int    MagicNumber  = 12345;
input double FixLotSize   = 0.01;
input int    MaxSpreadPoints = 40;

// --- 💰 Dynamic Position Sizing (Match Training: Kelly Criterion) ---
input bool   UseDynamicLot   = true;    // Enable Dynamic Lot Size
input double RiskPercent     = 1.0;     // Risk % per trade (base)
input double MinLotSize      = 0.01;    // Minimum lot size
input double MaxLotSize      = 0.10;    // Maximum lot size

// --- Safety & Filters ---
input bool   UseTimeFilter     = true;
input int    TradeStartHour    = 3;
input int    TradeEndHour      = 23;
input bool   CloseOnTimeEnd    = true;   // Close positions when trading hours end
input int    MaxConsecutiveLoss = 3;
input int    PenaltyHours       = 1;

// --- Hard Stop ---
input double Emergency_SL_ATR   = 2.0;

// --- 🎯 Trailing Stop & Break-Even Settings (Phase 1 Pro RM) ---
input bool   UseTrailingStop       = true;   // Enable Trailing Stop
input double BreakevenTriggerPct   = 0.5;    // Start BE when profit >= 0.5%
input double TrailingActivationPct = 1.0;    // Start Trail when profit >= 1.0%
input double TrailingDistancePct   = 0.3;    // Trail distance 0.3%

// --- 🔄 Cooldown Settings (Match Training) ---
input int    CooldownBars = 3;               // Bars to wait after closing position

// --- Global Vars ---
string BotStatus = "STOPPED";
string LastAction = "NONE";
double LastATR = 0.0;
int    ConsecutiveLosses = 0;
datetime LastLossTime = 0;
datetime last_bar_time = 0;

// 📰 News Filter State
double NewsRiskMultiplier = 1.0;  // 0.0-1.0 based on news proximity

// 🔄 Cooldown State
int CooldownBarsRemaining = 0;

// 🎯 Trailing Stop State
double HighestPnlPct = 0.0;
bool   BreakevenActive = false;
bool   TrailingActive = false;
double TrailingStopPct = 0.0;
double PositionEntryBalance = 0.0;

// 📰 Smart News Protection State
bool   ShouldTightenSL = false;
double NewsSLMultiplier = 2.0;  // Default to Emergency_SL_ATR

//+------------------------------------------------------------------+
//| JSON Helper Functions                                            |
//+------------------------------------------------------------------+
string ExtractJsonString(string json, string key) {
   string search = "\"" + key + "\":\"";
   int start = StringFind(json, search);
   if(start<0) return "";
   start += StringLen(search);
   int end = StringFind(json, "\"", start);
   if(end<0) return "";
   return StringSubstr(json, start, end-start);
}

double ExtractJsonDouble(string json, string key) {
   string search = "\"" + key + "\":";
   int start = StringFind(json, search);
   if(start<0) return 0.0;
   start += StringLen(search);
   int end = StringFind(json, ",", start);
   int end2 = StringFind(json, "}", start);
   if(end<0 || (end2>=0 && end2<end)) end = end2;
   string value_str = StringSubstr(json, start, end-start);
   StringTrimLeft(value_str);
   StringTrimRight(value_str);
   return StringToDouble(value_str);
}

//+------------------------------------------------------------------+
//| 🔥 FIX #1: Get Position with Correct Encoding                   |
//+------------------------------------------------------------------+
string GetPositionJson() {
   if(PositionsTotal()==0) return "{\"type\":0, \"price\":0.0}";
   
   for(int i=PositionsTotal()-1; i>=0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(PositionGetInteger(POSITION_MAGIC)==MagicNumber && 
         PositionGetString(POSITION_SYMBOL)==_Symbol) {
         
         long type = PositionGetInteger(POSITION_TYPE);
         double price = PositionGetDouble(POSITION_PRICE_OPEN);
         
         // 🔥 CRITICAL FIX: Use -1 for Sell (not 2)
         // Environment expects: 1=Buy, -1=Sell, 0=Empty
         int env_type;
         if(type == POSITION_TYPE_BUY) 
            env_type = 1;
         else if(type == POSITION_TYPE_SELL)
            env_type = -1;  // ✅ CORRECTED from 2
         else
            env_type = 0;
         
         return StringFormat("{\"type\":%d, \"price\":%.5f}", env_type, price);
      }
   }
   return "{\"type\":0, \"price\":0.0}";
}

//+------------------------------------------------------------------+
//| Get Rates as JSON Array (with spread & real_volume)              |
//+------------------------------------------------------------------+
string GetRatesJSON(string symbol, int bars) {
   return GetRatesForTimeframe(symbol, PERIOD_M5, bars);
}

//+------------------------------------------------------------------+
//| 🌐 Get Rates for Any Timeframe (MTF Support)                      |
//+------------------------------------------------------------------+
string GetRatesForTimeframe(string symbol, ENUM_TIMEFRAMES tf, int bars) {
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(symbol, tf, 0, bars, rates) <= 0) return "[]";
   
   string json = "[";
   for(int i=bars-1; i>=0; i--) {
      // Include spread and real_volume for feature alignment
      json += StringFormat("{\"time\":%d,\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,\"tick_volume\":%d,\"spread\":%d,\"real_volume\":%I64d}",
              rates[i].time, rates[i].open, rates[i].high, rates[i].low, 
              rates[i].close, rates[i].tick_volume, rates[i].spread, rates[i].real_volume);
      if(i > 0) json += ",";
   }
   return json + "]";
}

//+------------------------------------------------------------------+
//| 🔥 FIX #2: Get Action from API with Error Handling              |
//+------------------------------------------------------------------+
void GetActionFromAPI() {
   // 1. Get Real Balance & Spread
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   int spread_point = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   double spread_val = spread_point * _Point;
   
   // Safety: High Spread Check
   if(spread_point > MaxSpreadPoints) {
      Print("⚠️ High Spread: ", spread_point, " > ", MaxSpreadPoints, " - Forcing HOLD");
      LastAction = "HOLD"; 
      return;
   }

   // 2. Prepare M5 Data (Base)
   string m5_json = GetRatesJSON(_Symbol, 1000); 
   string pos_json = GetPositionJson();
   
   string usd_json = "[]"; 
   if(SymbolSelect("UsDollar", true)) 
      usd_json = GetRatesJSON("UsDollar", 1000);

   // 🌐 3. Prepare MTF Data (H1, H4, D1)
   string h1_json = GetRatesForTimeframe(_Symbol, PERIOD_H1, 300);  // 300 bars H1
   string h4_json = GetRatesForTimeframe(_Symbol, PERIOD_H4, 200);  // 200 bars H4
   string d1_json = GetRatesForTimeframe(_Symbol, PERIOD_D1, 100);  // 100 bars D1
   
   Print("📊 MTF Data: M5=", StringLen(m5_json), " H1=", StringLen(h1_json), 
         " H4=", StringLen(h4_json), " D1=", StringLen(d1_json), " bytes");

   // 4. Build Payload with MTF Data
   string payload = StringFormat(
      "{\"m5_data\":%s, \"h1_data\":%s, \"h4_data\":%s, \"d1_data\":%s, \"usd_m5\":%s, \"position\":%s, \"balance\":%.2f, \"spread\":%.5f}", 
      m5_json, h1_json, h4_json, d1_json, usd_json, pos_json, balance, spread_val
   );

   // 4. Send Request
   char data[];
   int len = StringToCharArray(payload, data, 0, WHOLE_ARRAY);
   if(len>0) ArrayResize(data, len-1);
   
   char res[]; 
   string headers;
   string req_headers = "Content-Type: application/json";
   
   ResetLastError();
   int code = WebRequest("POST", APIServerURL + "/predict", req_headers, 3000, data, res, headers);
   
   // 🔥 FIX #3: Robust Error Handling
   if(code == 200) {
      string json_res = CharArrayToString(res);
      
      // Validate response
      if(StringLen(json_res) < 5) {
         Print("❌ Invalid API Response: Empty or too short");
         LastAction = "HOLD";
         return;
      }
      
      string new_action = ExtractJsonString(json_res, "action");
      
      // Validate action
      if(new_action != "HOLD" && new_action != "BUY" && 
         new_action != "SELL" && new_action != "CLOSE") {
         Print("❌ Invalid Action from API: ", new_action);
         LastAction = "HOLD";
         return;
      }
      
      LastAction = new_action;
      LastATR = ExtractJsonDouble(json_res, "atr");
      
      // 📰 Parse News Risk Multiplier
      double newsRisk = ExtractJsonDouble(json_res, "news_risk_multiplier");
      if(newsRisk > 0 && newsRisk <= 1.0) {
         NewsRiskMultiplier = newsRisk;
      } else {
         NewsRiskMultiplier = 1.0;  // Default
      }
      
      // Optional: Log debug info
      double confidence = ExtractJsonDouble(json_res, "confidence");
      
      // 📰 Parse Smart News Protection flags
      double tighten_sl = ExtractJsonDouble(json_res, "tighten_sl");
      if(tighten_sl > 0) {
         ShouldTightenSL = true;
         double sl_mult = ExtractJsonDouble(json_res, "sl_atr_mult");
         if(sl_mult > 0) NewsSLMultiplier = sl_mult;
         Print("📰 News Protection: Tighten SL to ", DoubleToString(NewsSLMultiplier, 1), "x ATR");
      } else {
         ShouldTightenSL = false;
         NewsSLMultiplier = Emergency_SL_ATR;  // Reset to default
      }
      
      Print("✅ API Response: Action=", LastAction, " ATR=", LastATR, 
            " Confidence=", confidence, " NewsRisk=", DoubleToString(NewsRiskMultiplier, 2));
            
   } else {
      int err = GetLastError();
      Print("❌ API Request Failed: Code=", code, " Error=", err);
      Print("💡 Check: 1) API is running 2) URL is correct 3) Firewall allows connection");
      LastAction = "HOLD";
   }
}

//+------------------------------------------------------------------+
//| Update Bot Status from API                                       |
//+------------------------------------------------------------------+
void UpdateStatus() {
   char data[], res[];
   string headers;
   
   string payload = StringFormat(
      "{\"balance\":%.2f,\"equity\":%.2f,\"margin_free\":%.2f,\"open_trades\":%d}",
      AccountInfoDouble(ACCOUNT_BALANCE),
      AccountInfoDouble(ACCOUNT_EQUITY),
      AccountInfoDouble(ACCOUNT_MARGIN_FREE),
      PositionsTotal()
   );
   
   int len = StringToCharArray(payload, data, 0, WHOLE_ARRAY);
   if(len>0) ArrayResize(data, len-1);
   
   WebRequest("POST", APIServerURL + "/update_status", 
              "Content-Type: application/json", 1000, data, res, headers);
   
   // Get current status
   WebRequest("GET", APIServerURL + "/status", "", 1000, data, res, headers);
   string json_res = CharArrayToString(res);
   BotStatus = ExtractJsonString(json_res, "status");
}

//+------------------------------------------------------------------+
//| 🎯 Manage Trailing Stop & Break-Even (Phase 1 Pro RM)            |
//+------------------------------------------------------------------+
void ResetTrailingState() {
   HighestPnlPct = 0.0;
   BreakevenActive = false;
   TrailingActive = false;
   TrailingStopPct = 0.0;
   PositionEntryBalance = AccountInfoDouble(ACCOUNT_BALANCE);
}

bool ManageTrailingStop() {
   if(!UseTrailingStop) return false;
   if(PositionsTotal() == 0) return false;
   
   // Calculate current PnL %
   double currentBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double unrealizedPnl = equity - currentBalance;
   double pnlPct = (unrealizedPnl / PositionEntryBalance) * 100.0;
   
   // Track highest PnL
   if(pnlPct > HighestPnlPct) {
      HighestPnlPct = pnlPct;
   }
   
   // 1. Activate Break-Even
   if(!BreakevenActive && HighestPnlPct >= BreakevenTriggerPct) {
      BreakevenActive = true;
      Print("🎯 Break-Even Activated at +", DoubleToString(HighestPnlPct, 2), "%");
   }
   
   // 2. Activate Trailing
   if(!TrailingActive && HighestPnlPct >= TrailingActivationPct) {
      TrailingActive = true;
      TrailingStopPct = HighestPnlPct - TrailingDistancePct;
      Print("🎯 Trailing Stop Activated at +", DoubleToString(HighestPnlPct, 2), "%, Stop at ", DoubleToString(TrailingStopPct, 2), "%");
   }
   
   // 3. Update Trailing Stop
   if(TrailingActive) {
      double newStop = HighestPnlPct - TrailingDistancePct;
      if(newStop > TrailingStopPct) {
         TrailingStopPct = newStop;
         Print("🎯 Trailing Stop Updated to ", DoubleToString(TrailingStopPct, 2), "%");
      }
   }
   
   // 4. Check if Trailing Stop Hit
   if(TrailingActive && pnlPct <= TrailingStopPct) {
      Print("🎯 TRAILING STOP HIT! PnL: ", DoubleToString(pnlPct, 2), "% <= Stop: ", DoubleToString(TrailingStopPct, 2), "%");
      return true;  // Signal to close position
   }
   
   // 5. Check Break-Even (close if profit gone after BE active)
   if(BreakevenActive && pnlPct <= 0) {
      Print("🎯 BREAKEVEN STOP HIT! Profit gone, closing at breakeven.");
      return true;  // Signal to close position
   }
   
   return false;
}

//+------------------------------------------------------------------+
//| Close All Positions                                              |
//+------------------------------------------------------------------+
void ClosePosition() {
   for(int i=PositionsTotal()-1; i>=0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(PositionGetInteger(POSITION_MAGIC)==MagicNumber && 
         PositionGetString(POSITION_SYMBOL)==_Symbol) {
         
         if(trade.PositionClose(ticket))
            Print("✂️ Position Closed: #", ticket);
         else
            Print("❌ Failed to close #", ticket, " Error: ", GetLastError());
      }
   }
}

//+------------------------------------------------------------------+
//| � Tighten Stop Loss for News Protection                        |
//+------------------------------------------------------------------+
void TightenStopLoss(double atr_multiplier) {
   for(int i=PositionsTotal()-1; i>=0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(PositionGetInteger(POSITION_MAGIC)==MagicNumber && 
         PositionGetString(POSITION_SYMBOL)==_Symbol) {
         
         double entry_price = PositionGetDouble(POSITION_PRICE_OPEN);
         double current_sl = PositionGetDouble(POSITION_SL);
         long pos_type = PositionGetInteger(POSITION_TYPE);
         
         // Calculate new tight SL
         double new_sl;
         double dist = LastATR * atr_multiplier;
         
         if(pos_type == POSITION_TYPE_BUY) {
            double current_price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            new_sl = current_price - dist;
            
            // Only move SL up (tighter), never down
            if(current_sl == 0 || new_sl > current_sl) {
               if(trade.PositionModify(ticket, new_sl, 0)) {
                  Print("📰 News SL Tightened: BUY SL moved to ", DoubleToString(new_sl, 2), 
                        " (", DoubleToString(atr_multiplier, 1), "x ATR)");
               }
            }
         }
         else if(pos_type == POSITION_TYPE_SELL) {
            double current_price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
            new_sl = current_price + dist;
            
            // Only move SL down (tighter), never up
            if(current_sl == 0 || new_sl < current_sl) {
               if(trade.PositionModify(ticket, new_sl, 0)) {
                  Print("📰 News SL Tightened: SELL SL moved to ", DoubleToString(new_sl, 2),
                        " (", DoubleToString(atr_multiplier, 1), "x ATR)");
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| �💰 Calculate Dynamic Lot Size (Match Training Kelly Logic)       |
//+------------------------------------------------------------------+
double CalculateDynamicLot() {
   if(!UseDynamicLot) return FixLotSize;
   
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskDollars = balance * (RiskPercent / 100.0);
   
   // ATR-based sizing (similar to training)
   double atr = 0;
   if(LastATR > 0) {
      atr = LastATR;
   } else {
      // Fallback: calculate ATR
      double atr_buffer[];
      ArraySetAsSeries(atr_buffer, true);
      int atr_handle = iATR(_Symbol, PERIOD_M5, 14);
      if(atr_handle != INVALID_HANDLE) {
         CopyBuffer(atr_handle, 0, 0, 1, atr_buffer);
         atr = atr_buffer[0];
         IndicatorRelease(atr_handle);
      }
   }
   
   // Calculate lot based on risk and ATR
   double lot_size;
   if(atr > 0) {
      // Risk $ / (ATR * Point Value per Lot)
      double point_value = 100.0;  // XAUUSD: $100 per $1 move per lot
      lot_size = riskDollars / (atr * point_value * Emergency_SL_ATR);
   } else {
      lot_size = FixLotSize;
   }
   
   // Volatility Adjustment (from training)
   double atr_pct = atr / SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(atr_pct > 0.015) {        // High Vol
      lot_size *= 0.7;
   } else if(atr_pct < 0.008) { // Low Vol
      lot_size *= 1.3;
   }
   
   // Session Adjustment (from training)
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.hour >= 0 && dt.hour < 8) {  // Asian Session
      lot_size *= 0.8;
   }
   
   // 📰 News Risk Adjustment (from API)
   if(NewsRiskMultiplier < 1.0) {
      lot_size *= NewsRiskMultiplier;
      Print("📰 News Risk Applied: ", DoubleToString(NewsRiskMultiplier * 100, 0), "% of normal size");
   }
   
   // Cap lot size
   lot_size = MathMax(MinLotSize, MathMin(lot_size, MaxLotSize));
   
   // Round to broker step
   double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   lot_size = MathFloor(lot_size / lot_step) * lot_step;
   
   Print("💰 Dynamic Lot: ", DoubleToString(lot_size, 2), 
         " (Risk: ", RiskPercent, "%, ATR: ", DoubleToString(atr, 2), 
         ", NewsRisk: ", DoubleToString(NewsRiskMultiplier * 100, 0), "%)");
   
   return lot_size;
}

//+------------------------------------------------------------------+
//| Open New Trade with Safety Checks                                |
//+------------------------------------------------------------------+
void OpenTrade(ENUM_ORDER_TYPE type) {
   // Circuit Breaker Check
   if(ConsecutiveLosses >= MaxConsecutiveLoss) {
      if(TimeCurrent() - LastLossTime < PenaltyHours * 3600) {
         Print("⛔ Circuit Breaker Active: ", ConsecutiveLosses, " losses. ",
               "Wait ", (PenaltyHours*3600 - (TimeCurrent()-LastLossTime))/60, " minutes");
         return;
      } else {
         ConsecutiveLosses = 0;
         Print("✅ Circuit Breaker Reset");
      }
   }

   double price = (type==ORDER_TYPE_BUY) ? 
                   SymbolInfoDouble(_Symbol, SYMBOL_ASK) : 
                   SymbolInfoDouble(_Symbol, SYMBOL_BID);
   
   // 💰 Calculate Dynamic Lot Size
   double lot_size = CalculateDynamicLot();
   
   // Safety: Emergency Stop Loss
   double sl = 0;
   if(LastATR > 0) {
      double dist = LastATR * Emergency_SL_ATR;
      sl = (type==ORDER_TYPE_BUY) ? price - dist : price + dist;
      Print("🛡️ Emergency SL set at: ", sl, " (", Emergency_SL_ATR, " x ATR)");
   }

   if(trade.PositionOpen(_Symbol, type, lot_size, price, sl, 0, 
                         "RL Agent")) {
      Print("🚀 ", EnumToString(type), " Opened: Lot=", lot_size, 
            " Price=", price, " SL=", sl);
   } else {
      Print("❌ Failed to open ", EnumToString(type), " Error: ", GetLastError());
   }
}

//+------------------------------------------------------------------+
//| Main Trading Logic                                               |
//+------------------------------------------------------------------+
void OnTick() {
   // Check for new bar
   datetime curr_time = iTime(_Symbol, PERIOD_M5, 0);
   if(curr_time == last_bar_time) return;
   last_bar_time = curr_time;
   
   // Update Status
   UpdateStatus();
   
   if(BotStatus != "RUNNING") {
      if(BotStatus == "STOPPED")
         Comment("🔴 OBot Status: STOPPED\nUse Telegram /start to activate");
      return;
   }
   
   // Time Filter
   if(UseTimeFilter) {
      MqlDateTime dt; 
      TimeToStruct(TimeCurrent(), dt);
      bool outsideHours = (dt.hour < TradeStartHour || dt.hour >= TradeEndHour);
      
      if(outsideHours) {
         // 🕐 Close positions when trading hours end
         if(CloseOnTimeEnd && PositionsTotal() > 0) {
            Print("⏰ Trading hours ended - Closing open positions");
            ClosePosition();
            ResetTrailingState();
            CooldownBarsRemaining = CooldownBars;
         }
         
         Comment("⏰ Outside Trading Hours: ", dt.hour, ":00\n",
                 "Active: ", TradeStartHour, ":00 - ", TradeEndHour, ":00\n",
                 "Positions: ", (CloseOnTimeEnd ? "Closed" : "Hold"));
         return;
      }
   }
   
   // 🎯 Check Trailing Stop / Break-Even BEFORE getting AI decision
   if(PositionsTotal() > 0 && ManageTrailingStop()) {
      Print("🎯 Trailing/Breakeven triggered - Closing position");
      ClosePosition();
      ResetTrailingState();
      CooldownBarsRemaining = CooldownBars;  // Start cooldown
      return;
   }
   
   // 🔄 Cooldown Period (Match Training: 3 bars after close)
   if(CooldownBarsRemaining > 0) {
      CooldownBarsRemaining--;
      Print("🔄 Cooldown: ", CooldownBarsRemaining, " bars remaining");
      Comment("🔄 Cooldown Period\n",
              "Bars remaining: ", CooldownBarsRemaining, "\n",
              "No new trades until cooldown ends");
      return;
   }

   // Get AI Decision
   GetActionFromAPI();
   
   // 📰 Smart News Protection: Tighten SL if flagged
   if(ShouldTightenSL && PositionsTotal() > 0) {
      TightenStopLoss(NewsSLMultiplier);
   }
   
   // 🔥 FIX #4: Validate Action Before Execution
   if(LastAction == "" || LastAction == "ERROR") {
      Print("⚠️ Invalid API response. Skipping this tick.");
      Comment("⚠️ API Communication Error\nRetrying next bar...");
      return;
   }

   // Execute Actions
   bool has_position = PositionSelect(_Symbol);
   long pos_type = has_position ? PositionGetInteger(POSITION_TYPE) : -1;
   
   Comment("🤖 OBot RL Agent\n",
           "Status: ", BotStatus, "\n",
           "Action: ", LastAction, "\n",
           "ATR: ", DoubleToString(LastATR, 2), "\n",
           "Position: ", has_position ? EnumToString((ENUM_POSITION_TYPE)pos_type) : "NONE", "\n",
           "Cooldown: ", CooldownBarsRemaining, " bars\n",
           "Balance: $", DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2), "\n",
           "Equity: $", DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2));
   
   // CLOSE Signal
   if(LastAction == "CLOSE") {
      if(has_position) {
         ClosePosition();
         CooldownBarsRemaining = CooldownBars;  // Start cooldown after close
      }
   }
   // BUY Signal
   else if(LastAction == "BUY") {
      // If holding SELL, close it first (flip)
      if(has_position && pos_type == POSITION_TYPE_SELL) {
         Print("🔄 Flipping from SELL to BUY");
         ClosePosition();
         Sleep(100); // Small delay for order processing
      }
      // Open BUY if no position
      if(!PositionSelect(_Symbol)) {
         OpenTrade(ORDER_TYPE_BUY);
         ResetTrailingState();  // 🎯 Start fresh trailing for new position
      }
   }
   // SELL Signal
   else if(LastAction == "SELL") {
      // If holding BUY, close it first (flip)
      if(has_position && pos_type == POSITION_TYPE_BUY) {
         Print("🔄 Flipping from BUY to SELL");
         ClosePosition();
         Sleep(100);
      }
      // Open SELL if no position
      if(!PositionSelect(_Symbol)) {
         OpenTrade(ORDER_TYPE_SELL);
         ResetTrailingState();  // 🎯 Start fresh trailing for new position
      }
   }
}

//+------------------------------------------------------------------+
//| Circuit Breaker: Track Consecutive Losses                        |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction& trans, 
                        const MqlTradeRequest& req, 
                        const MqlTradeResult& res) {
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD) {
      if(HistoryDealSelect(trans.deal)) {
         long magic = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
         string symbol = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
         
         if(magic == MagicNumber && symbol == _Symbol) {
            long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
            
            if(entry == DEAL_ENTRY_OUT) { // Position closed
               double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
               
               if(profit < 0) {
                  ConsecutiveLosses++;
                  LastLossTime = TimeCurrent();
                  Print("📉 Loss #", ConsecutiveLosses, ": $", 
                        DoubleToString(profit, 2));
                  
                  if(ConsecutiveLosses >= MaxConsecutiveLoss) {
                     Print("🚨 CIRCUIT BREAKER TRIGGERED! ",
                           "Trading paused for ", PenaltyHours, " hour(s)");
                  }
               } else {
                  ConsecutiveLosses = 0;
                  Print("💰 Profit: $", DoubleToString(profit, 2), 
                        " - Loss counter reset");
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit() {
   Print("========================================");
   Print("🚀 OBot RL Agent v1.0");
   Print("========================================");
   Print("⚙️ Settings:");
   Print("   API URL: ", APIServerURL);
   Print("   Magic: ", MagicNumber);
   Print("   Lot Size: ", FixLotSize);
   Print("   Max Spread: ", MaxSpreadPoints, " points");
   Print("   Trading Hours: ", TradeStartHour, ":00 - ", TradeEndHour, ":00");
   Print("========================================");
   
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(10);
   trade.SetTypeFilling(ORDER_FILLING_IOC);
   
   // Test API Connection
   UpdateStatus();
   if(BotStatus == "STOPPED") {
      Print("💡 Use Telegram /start command to begin trading");
   }
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   Print("========================================");
   Print("🛑 OBot RL Agent Stopped");
   Print("Reason: ", reason);
   Print("========================================");
}