# linux_safety.py
"""
Safety System สำหรับ Production Trading
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json

class TradingSafetyMonitor:
    def __init__(self, max_daily_loss_pct=5, max_drawdown_pct=10):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct
        
        self.daily_pnl = []
        self.trade_log = []
        self.equity_curve = []
        
        self.trading_enabled = True
        self.alerts = []
        
    def update(self, current_equity, trade_pnl=None):
        """Update monitoring state"""
        self.equity_curve.append({
            'timestamp': datetime.now(),
            'equity': current_equity
        })
        
        if trade_pnl is not None:
            self.trade_log.append({
                'timestamp': datetime.now(),
                'pnl': trade_pnl
            })
        
        # Check safety conditions
        self._check_daily_loss()
        self._check_drawdown()
        self._check_performance_degradation()
        
    def _check_daily_loss(self):
        """หยุดเทรดถ้าขาดทุนเกิน Daily Limit"""
        today = datetime.now().date()
        
        # Calculate today's PnL
        today_trades = [t for t in self.trade_log 
                       if t['timestamp'].date() == today]
        
        if not today_trades:
            return
        
        daily_pnl = sum(t['pnl'] for t in today_trades)
        initial_balance = self.equity_curve[0]['equity'] if self.equity_curve else 1000
        
        daily_loss_pct = (daily_pnl / initial_balance) * 100
        
        if daily_loss_pct < -self.max_daily_loss_pct:
            self.trading_enabled = False
            self.alerts.append({
                'type': 'DAILY_LOSS_LIMIT',
                'timestamp': datetime.now(),
                'message': f'Daily loss {daily_loss_pct:.2f}% exceeded limit {self.max_daily_loss_pct}%',
                'severity': 'CRITICAL'
            })
            print(f"🚨 TRADING HALTED: Daily loss limit exceeded!")
    
    def _check_drawdown(self):
        """หยุดเทรดถ้า Drawdown เกินกำหนด"""
        if len(self.equity_curve) < 2:
            return
        
        equity_series = pd.Series([e['equity'] for e in self.equity_curve])
        running_max = equity_series.expanding().max()
        drawdown = (equity_series - running_max) / running_max * 100
        current_dd = drawdown.iloc[-1]
        
        if current_dd < -self.max_drawdown_pct:
            self.trading_enabled = False
            self.alerts.append({
                'type': 'MAX_DRAWDOWN',
                'timestamp': datetime.now(),
                'message': f'Drawdown {current_dd:.2f}% exceeded limit {self.max_drawdown_pct}%',
                'severity': 'CRITICAL'
            })
            print(f"🚨 TRADING HALTED: Max drawdown exceeded!")
    
    def _check_performance_degradation(self):
        """เตือนถ้าประสิทธิภาพลดลงอย่างมาก"""
        if len(self.trade_log) < 50:
            return
        
        # Compare recent 20 trades vs previous 30 trades
        recent_20 = [t['pnl'] for t in self.trade_log[-20:]]
        previous_30 = [t['pnl'] for t in self.trade_log[-50:-20]]
        
        recent_avg = np.mean(recent_20)
        previous_avg = np.mean(previous_30)
        
        # If recent performance is 50% worse
        if previous_avg > 0 and recent_avg < previous_avg * 0.5:
            self.alerts.append({
                'type': 'PERFORMANCE_DEGRADATION',
                'timestamp': datetime.now(),
                'message': f'Recent avg PnL {recent_avg:.2f} vs previous {previous_avg:.2f}',
                'severity': 'WARNING'
            })
            print(f"⚠️ WARNING: Performance degradation detected. Consider retraining model.")
    
    def can_trade(self):
        """ตรวจสอบว่าอนุญาตให้เทรดได้หรือไม่"""
        return self.trading_enabled
    
    def get_status(self):
        """สถานะปัจจุบัน"""
        if not self.equity_curve:
            return {}
        
        initial = self.equity_curve[0]['equity']
        current = self.equity_curve[-1]['equity']
        
        equity_series = pd.Series([e['equity'] for e in self.equity_curve])
        running_max = equity_series.expanding().max()
        current_dd = ((equity_series.iloc[-1] - running_max.iloc[-1]) / running_max.iloc[-1] * 100)
        
        # Daily stats
        today = datetime.now().date()
        today_trades = [t for t in self.trade_log if t['timestamp'].date() == today]
        daily_pnl = sum(t['pnl'] for t in today_trades)
        
        return {
            'trading_enabled': self.trading_enabled,
            'current_equity': current,
            'total_pnl': current - initial,
            'total_pnl_pct': ((current - initial) / initial) * 100,
            'current_drawdown': current_dd,
            'daily_pnl': daily_pnl,
            'total_trades': len(self.trade_log),
            'active_alerts': len([a for a in self.alerts if a['severity'] == 'CRITICAL'])
        }
    
    def save_report(self, filename='safety_report.json'):
        """บันทึกรายงาน"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'status': self.get_status(),
            'alerts': self.alerts[-10:],  # Last 10 alerts
            'equity_curve': [
                {'time': e['timestamp'].isoformat(), 'equity': e['equity']}
                for e in self.equity_curve[-100:]  # Last 100 points
            ]
        }
        
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"📊 Safety report saved to {filename}")