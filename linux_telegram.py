import telegram
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import requests
import time
import os
from pathlib import Path
from datetime import datetime

# =============================================================================
# ⚙️ CONFIGURATION
# =============================================================================

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    load_dotenv(dotenv_path=env_path, override=True)
except ImportError:
    print("⚠️ Warning: python-dotenv not installed")

TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
CHAT_ID_STR = os.getenv('TELEGRAM_CHAT_ID', '').strip()

if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN is not set!")
if not CHAT_ID_STR:
    raise ValueError("❌ TELEGRAM_CHAT_ID is not set!")

CHAT_ID = int(CHAT_ID_STR)
API_URL = 'http://127.0.0.1:5000'

# =============================================================================
# 🎨 HELPER FUNCTIONS
# =============================================================================

def format_pnl(value):
    """Format PnL with color emoji"""
    if value > 0:
        return f"🟢 +${value:,.2f}"
    elif value < 0:
        return f"🔴 ${value:,.2f}"
    return f"⚪ ${value:,.2f}"

def format_percent(value):
    """Format percentage with color"""
    if value > 0:
        return f"🟢 +{value:.2f}%"
    elif value < 0:
        return f"🔴 {value:.2f}%"
    return f"⚪ {value:.2f}%"

# =============================================================================
# 🌐 API CALLS (Centralized)
# =============================================================================

def api_get(endpoint):
    """GET request to API"""
    try:
        response = requests.get(f'{API_URL}{endpoint}', timeout=5)
        if response.status_code == 200:
            return response.json(), None
        return None, f"API Error: {response.status_code}"
    except requests.exceptions.ConnectionError:
        return None, "API not running"
    except Exception as e:
        return None, str(e)

def api_post(endpoint, data=None):
    """POST request to API"""
    try:
        response = requests.post(f'{API_URL}{endpoint}', json=data, timeout=10)
        if response.status_code == 200:
            return response.json(), None
        return None, f"API Error: {response.status_code}"
    except requests.exceptions.ConnectionError:
        return None, "API not running"
    except Exception as e:
        return None, str(e)

# =============================================================================
# 📝 MESSAGE BUILDERS (Single Source of Truth)
# =============================================================================

def build_status_message(full=True):
    """Build status message - used by both /status and button"""
    data, error = api_get('/status')
    if error:
        return f"❌ **Error:** {error}"
    
    d = data
    bot_state = d.get('status', 'UNKNOWN')
    state_emoji = "🟢" if bot_state == "RUNNING" else "🔴"
    last_action = d.get('last_action', 'NONE')
    action_emoji = "📈" if last_action == "BUY" else ("📉" if last_action == "SELL" else "⏸️")
    
    balance = d.get('balance', 0)
    equity = d.get('equity', 0)
    floating = equity - balance
    daily_pnl = d.get('daily_pnl', 0)
    
    if full:
        # Full version for /status command
        margin_free = d.get('margin_free', 0)
        open_trades = d.get('open_trades', 0)
        safety_halted = d.get('safety_halted', False)
        current_dd = d.get('current_drawdown', 0)
        safety_status = "🚨 HALTED!" if safety_halted else "✅ OK"
        
        news_msg = d.get('news_message', 'Unknown')
        news_risk = d.get('news_risk_multiplier', 1.0)
        news_emoji = "🔴" if news_risk < 0.5 else ("⚠️" if news_risk < 1.0 else "🟢")
        
        model_loaded = d.get('model_loaded', False)
        model_status = "✅ Ready" if model_loaded else "❌ Not Loaded"
        
        return (
            f"📊 **OBOT TRADING SYSTEM** 📊\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            f"🤖 **BOT STATUS**\n"
            f"├ State: {state_emoji} `{bot_state}`\n"
            f"├ Last Action: {action_emoji} `{last_action}`\n"
            f"└ Model: `{model_status}`\n\n"
            
            f"💰 **ACCOUNT**\n"
            f"├ Balance: `${balance:,.2f}`\n"
            f"├ Equity: `${equity:,.2f}` ({format_pnl(floating)})\n"
            f"├ Free Margin: `${margin_free:,.2f}`\n"
            f"└ Open Trades: `{open_trades}`\n\n"
            
            f"📈 **TODAY'S P/L**\n"
            f"├ Daily PnL: {format_pnl(daily_pnl)}\n"
            f"├ Drawdown: `{current_dd:.2f}%`\n"
            f"└ Safety: `{safety_status}`\n\n"
            
            f"📰 **NEWS FILTER**\n"
            f"├ Status: {news_emoji} `{news_msg[:40]}...`\n"
            f"└ Risk Level: `{news_risk*100:.0f}%`\n\n"
            
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 Updated: `{time.strftime('%Y-%m-%d %H:%M:%S')}`"
        )
    else:
        # Quick version for button callback
        news_msg = d.get('news_message', 'Unknown')[:30]
        return (
            f"📊 **QUICK STATUS**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"State: {state_emoji} `{bot_state}`\n"
            f"Action: `{last_action}`\n"
            f"Balance: `${balance:,.2f}`\n"
            f"Equity: `${equity:,.2f}`\n"
            f"Today: {format_pnl(daily_pnl)}\n"
            f"News: `{news_msg}...`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )


def build_news_message(full=True):
    """Build news message - used by both /news and button"""
    data, error = api_get('/status')
    if error:
        return f"❌ **Error:** {error}"
    
    d = data
    news_msg = d.get('news_message', 'Unknown')
    news_risk = d.get('news_risk_multiplier', 1.0)
    news_lock = d.get('news_lock', False)
    news_next = d.get('news_next_event', None)
    
    if news_lock:
        status_icon = "🔴 LOCKED"
        risk_bar = "▓▓▓▓▓▓▓▓▓▓"
    elif news_risk < 0.5:
        status_icon = "🟡 WARNING"
        risk_bar = "▓▓▓▓▓░░░░░"
    elif news_risk < 1.0:
        status_icon = "🟠 CAUTION"
        risk_bar = "▓▓▓░░░░░░░"
    else:
        status_icon = "🟢 CLEAR"
        risk_bar = "░░░░░░░░░░"
    
    if full:
        message = (
            f"📰 **NEWS FILTER STATUS** 📰\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            f"📊 **CURRENT STATUS**\n"
            f"├ Status: {status_icon}\n"
            f"├ Risk Level: {risk_bar} `{news_risk*100:.0f}%`\n"
            f"└ Trading: {'❌ Blocked' if news_lock else '✅ Allowed'}\n\n"
        )
        
        if news_next:
            mins = news_next.get('minutes', 0)
            hours = int(mins / 60)
            remaining_mins = int(mins % 60)
            time_str = f"{hours}h {remaining_mins}m" if hours > 0 else f"{remaining_mins}m"
            if mins <= 0:
                time_str = "Just passed"
            
            message += (
                f"⏰ **NEXT EVENT**\n"
                f"├ Event: `{news_next.get('title', 'Unknown')}`\n"
                f"└ Time: `{time_str}`\n\n"
            )
        
        message += (
            f"📝 **MESSAGE**\n"
            f"`{news_msg}`\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ Position size auto-adjusts based on news."
        )
        return message
    else:
        return (
            f"📰 **NEWS STATUS**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Status: {status_icon}\n"
            f"Risk: `{news_risk*100:.0f}%`\n"
            f"`{news_msg[:50]}...`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )


def build_performance_message(full=True):
    """Build performance message - used by both /performance and button"""
    data, error = api_get('/safety_status')
    if error:
        return f"❌ **Error:** {error}"
    
    d = data
    total_pnl = d.get('total_pnl', 0)
    total_pnl_pct = d.get('total_pnl_pct', 0)
    daily_pnl = d.get('daily_pnl', 0)
    current_dd = d.get('current_drawdown', 0)
    total_trades = d.get('total_trades', 0)
    current_equity = d.get('current_equity', 0)
    
    # Grade
    if total_pnl_pct > 50:
        grade = "🏆 ELITE"
    elif total_pnl_pct > 20:
        grade = "⭐ EXCELLENT"
    elif total_pnl_pct > 10:
        grade = "✅ GOOD"
    elif total_pnl_pct > 0:
        grade = "👍 POSITIVE"
    else:
        grade = "📉 NEGATIVE"
    
    if full:
        return (
            f"📈 **TRADING PERFORMANCE** 📈\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            f"💰 **PROFIT/LOSS**\n"
            f"├ Total P/L: {format_pnl(total_pnl)}\n"
            f"├ Total %: {format_percent(total_pnl_pct)}\n"
            f"├ Today: {format_pnl(daily_pnl)}\n"
            f"└ Grade: {grade}\n\n"
            
            f"📊 **STATISTICS**\n"
            f"├ Current Equity: `${current_equity:,.2f}`\n"
            f"├ Total Trades: `{total_trades}`\n"
            f"└ Max Drawdown: `{current_dd:.2f}%`\n\n"
            
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 Updated: `{time.strftime('%Y-%m-%d %H:%M:%S')}`"
        )
    else:
        return (
            f"📈 **QUICK PERFORMANCE**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Total P/L: {format_pnl(total_pnl)}\n"
            f"Today: {format_pnl(daily_pnl)}\n"
            f"Trades: `{total_trades}`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )


def build_safety_message(full=True):
    """Build safety message - used by both /safety and button"""
    data, error = api_get('/safety_status')
    if error:
        return f"❌ **Error:** {error}"
    
    d = data
    
    if not d.get('enabled', True):
        return "⚪ **Safety Monitor Not Active**\n\nStart the bot first with /start"
    
    trading_ok = d.get('trading_enabled', True)
    current_dd = d.get('current_drawdown', 0)
    status = "✅ OK" if trading_ok else "🚨 HALTED"
    
    if full:
        message = (
            f"🛡️ **SAFETY MONITOR** 🛡️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            f"📊 **STATUS**\n"
            f"├ Can Trade: `{'✅ YES' if trading_ok else '🚨 HALTED'}`\n"
            f"├ Equity: `${d.get('current_equity', 0):,.2f}`\n"
            f"└ Drawdown: `{current_dd:.2f}%`\n\n"
            
            f"💰 **PROFIT/LOSS**\n"
            f"├ Total P/L: {format_pnl(d.get('total_pnl', 0))}\n"
            f"├ Total %: {format_percent(d.get('total_pnl_pct', 0))}\n"
            f"└ Daily P/L: {format_pnl(d.get('daily_pnl', 0))}\n\n"
            
            f"📈 **STATISTICS**\n"
            f"├ Total Trades: `{d.get('total_trades', 0)}`\n"
            f"└ Active Alerts: `{d.get('active_alerts', 0)}`\n\n"
        )
        
        alerts = d.get('alerts', [])
        if alerts:
            message += "⚠️ **RECENT ALERTS**\n"
            for alert in alerts[-3:]:
                message += f"└ {alert.get('type')}: {alert.get('message')}\n"
        else:
            message += "✅ **No alerts**\n"
        
        message += (
            f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Use /reset\\_safety to clear and resume"
        )
        return message
    else:
        return (
            f"🛡️ **SAFETY STATUS**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Status: {status}\n"
            f"Drawdown: `{current_dd:.2f}%`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )


def build_help_message():
    """Build help message - single version"""
    return (
        "🤖 **OBOT COMMANDS** 🤖\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        "📊 **STATUS COMMANDS**\n"
        "/status - Full status report\n"
        "/news - News filter status\n"
        "/performance - Trading stats\n"
        "/safety - Safety monitor details\n\n"
        
        "🎮 **CONTROL COMMANDS**\n"
        "/start - Start trading\n"
        "/stop - Stop trading\n"
        "/reset\\_safety - Reset safety halt\n\n"
        
        "🛠️ **SYSTEM COMMANDS**\n"
        "/menu - Show button menu\n"
        "/fix - Download & reload model\n"
        "/update\\_ea - Update EA from GitHub\n"
        "/restart\\_api - Restart all services\n"
        "/help - Show this help\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 Tip: Use /menu for quick buttons!"
    )

# =============================================================================
# 🎛️ INLINE KEYBOARDS
# =============================================================================

def get_main_keyboard():
    """Main menu with all commands as buttons"""
    keyboard = [
        # Row 1: Status Commands
        [
            InlineKeyboardButton("📊 Status", callback_data='status'),
            InlineKeyboardButton("📈 Performance", callback_data='performance'),
        ],
        # Row 2: More Status
        [
            InlineKeyboardButton("📰 News", callback_data='news'),
            InlineKeyboardButton("🛡️ Safety", callback_data='safety'),
        ],
        # Row 3: Control
        [
            InlineKeyboardButton("🟢 Start Bot", callback_data='start'),
            InlineKeyboardButton("🔴 Stop Bot", callback_data='stop'),
        ],
        # Row 4: System Tools & Help
        [
            InlineKeyboardButton("🛠️ System Tools", callback_data='show_system'),
            InlineKeyboardButton("❓ Help", callback_data='help'),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_system_keyboard():
    """System tools keyboard"""
    keyboard = [
        [
            InlineKeyboardButton("🛡️ Reset Safety", callback_data='reset_safety'),
        ],
        [
            InlineKeyboardButton("📥 Fix/Reload Model", callback_data='fix'),
        ],
        [
            InlineKeyboardButton("📦 Update EA", callback_data='update_ea'),
        ],
        [
            InlineKeyboardButton("🔄 Restart Services", callback_data='restart_api'),
        ],
        [
            InlineKeyboardButton("⬅️ Back to Main", callback_data='back_main'),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_confirm_keyboard(action):
    """Confirmation keyboard for dangerous actions"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, proceed", callback_data=f'confirm_{action}'),
            InlineKeyboardButton("❌ Cancel", callback_data='back_main'),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# =============================================================================
# 🎮 COMMAND HANDLERS
# =============================================================================

def check_auth(update):
    """Check if user is authorized"""
    return update.effective_chat.id == CHAT_ID

async def status_command(update, context):
    """Handle /status command"""
    if not check_auth(update): return
    message = build_status_message(full=True)
    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=get_main_keyboard())

async def news_command(update, context):
    """Handle /news command"""
    if not check_auth(update): return
    message = build_news_message(full=True)
    await update.message.reply_text(message, parse_mode='Markdown')

async def performance_command(update, context):
    """Handle /performance command"""
    if not check_auth(update): return
    message = build_performance_message(full=True)
    await update.message.reply_text(message, parse_mode='Markdown')

async def safety_command(update, context):
    """Handle /safety command"""
    if not check_auth(update): return
    message = build_safety_message(full=True)
    await update.message.reply_text(message, parse_mode='Markdown')

async def start_command(update, context):
    """Handle /start command - Start trading"""
    if not check_auth(update): return
    
    await update.message.reply_text("⏳ Starting OBot...", parse_mode='Markdown')
    
    _, error = api_post('/command', {'command': 'START'})
    if error:
        message = f"❌ **Error:** {error}"
    else:
        message = "🟢 **OBot Started!**\n\nMT5 Bot is now trading.\nUse /status to check current state."
    
    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=get_main_keyboard())

async def stop_command(update, context):
    """Handle /stop command - Stop trading"""
    if not check_auth(update): return
    
    _, error = api_post('/command', {'command': 'STOP'})
    if error:
        message = f"❌ **Error:** {error}"
    else:
        message = "🔴 **OBot Stopped!**\n\nTrading is now paused."
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def reset_safety_command(update, context):
    """Handle /reset_safety command - Reset safety monitor"""
    if not check_auth(update): return
    
    _, error = api_post('/command', {'command': 'RESET_SAFETY'})
    if error:
        message = f"❌ **Error:** {error}"
    else:
        message = "✅ **Safety Monitor Reset!**\n\nTrading can now resume."
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def menu_command(update, context):
    """Handle /menu command - Show button menu"""
    if not check_auth(update): return
    
    message = (
        "🎛️ **OBOT CONTROL PANEL** 🎛️\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Select an option below:"
    )
    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=get_main_keyboard())

async def help_command(update, context):
    """Handle /help command"""
    if not check_auth(update): return
    message = build_help_message()
    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=get_main_keyboard())

async def fix_command(update, context):
    """Handle /fix command - Download & reload system files"""
    if not check_auth(update): return
    
    await update.message.reply_text("⏳ Downloading system files...", parse_mode='Markdown')
    
    _, error = api_post('/fix')
    if error:
        message = f"❌ **Error:** {error}"
    else:
        message = "✅ **System files updated!**\n\nModel and scaler reloaded."
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def update_ea_command(update, context):
    """Handle /update_ea command - Update EA from GitHub"""
    if not check_auth(update): return
    
    await update.message.reply_text("⏳ Downloading EA from GitHub...", parse_mode='Markdown')
    
    _, error = api_post('/update_ea')
    if error:
        message = f"❌ **Error:** {error}"
    else:
        message = "✅ **EA Updated!**\n\nCompile trigger issued.\nRestart EA in MT5 to apply."
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def restart_api_command(update, context):
    """Handle /restart_api command - Restart all services"""
    if not check_auth(update): return
    
    await update.message.reply_text("⏳ Restarting services...", parse_mode='Markdown')
    
    _, error = api_post('/restart')
    if error:
        message = f"❌ **Error:** {error}"
    else:
        message = "✅ **Services Restarting!**\n\nBot may be offline for a moment."
    
    await update.message.reply_text(message, parse_mode='Markdown')

# =============================================================================
# 🎛️ CALLBACK HANDLER (Inline Buttons)
# =============================================================================

async def button_callback(update, context):
    """Handle inline button presses"""
    query = update.callback_query
    await query.answer()
    
    if query.message.chat.id != CHAT_ID:
        return
    
    data = query.data
    message = None
    keyboard = None
    
    # === STATUS BUTTONS ===
    if data == 'status':
        message = build_status_message(full=False)
        keyboard = get_main_keyboard()
    
    elif data == 'performance':
        message = build_performance_message(full=False)
        keyboard = get_main_keyboard()
    
    elif data == 'news':
        message = build_news_message(full=False)
        keyboard = get_main_keyboard()
    
    elif data == 'safety':
        message = build_safety_message(full=False)
        keyboard = get_main_keyboard()
    
    # === CONTROL BUTTONS ===
    elif data == 'start':
        _, error = api_post('/command', {'command': 'START'})
        message = "🟢 **OBot Started!**\nTrading is now active." if not error else f"❌ {error}"
        keyboard = get_main_keyboard()
    
    elif data == 'stop':
        _, error = api_post('/command', {'command': 'STOP'})
        message = "🔴 **OBot Stopped!**\nTrading is now paused." if not error else f"❌ {error}"
        keyboard = get_main_keyboard()
    
    elif data == 'reset_safety':
        _, error = api_post('/command', {'command': 'RESET_SAFETY'})
        message = "✅ **Safety Monitor Reset!**\nTrading can now resume." if not error else f"❌ {error}"
        keyboard = get_system_keyboard()
    
    # === SYSTEM TOOLS MENU ===
    elif data == 'show_system':
        message = (
            "🛠️ **SYSTEM TOOLS** 🛠️\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📥 **Fix/Reload** - Download model & scaler\n"
            "📦 **Update EA** - Get latest EA from GitHub\n"
            "🔄 **Restart** - Restart all services\n\n"
            "⚠️ Use with caution!"
        )
        keyboard = get_system_keyboard()
    
    elif data == 'back_main':
        message = (
            "🎛️ **OBOT CONTROL PANEL** 🎛️\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Select an option:"
        )
        keyboard = get_main_keyboard()
    
    # === SYSTEM ACTIONS ===
    elif data == 'fix':
        message = "⏳ Downloading system files..."
        await query.message.reply_text(message, parse_mode='Markdown')
        _, error = api_post('/fix')
        message = "✅ **System files updated!**\nModel and scaler reloaded." if not error else f"❌ {error}"
        keyboard = get_system_keyboard()
    
    elif data == 'update_ea':
        message = "⏳ Downloading EA from GitHub..."
        await query.message.reply_text(message, parse_mode='Markdown')
        _, error = api_post('/update_ea')
        message = "✅ **EA Updated!**\nCompile trigger issued.\nRestart EA in MT5." if not error else f"❌ {error}"
        keyboard = get_system_keyboard()
    
    elif data == 'restart_api':
        # Show confirmation first
        message = (
            "⚠️ **CONFIRM RESTART** ⚠️\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "This will restart all services:\n"
            "• API Server\n"
            "• Telegram Bot\n"
            "• MT5 Service\n\n"
            "Bot may be offline for 1-2 minutes.\n\n"
            "**Are you sure?**"
        )
        keyboard = get_confirm_keyboard('restart')
    
    elif data == 'confirm_restart':
        message = "⏳ Restarting services..."
        await query.message.reply_text(message, parse_mode='Markdown')
        _, error = api_post('/restart')
        message = "✅ **Services Restarting!**\nBot may be offline for a moment." if not error"
        keyboard = get_main_keyboard()
    
    # === HELP ===
    elif data == 'help':
        message = build_help_message()
        keyboard = get_main_keyboard()
    
    else:
        message = "❌ Unknown action"
        keyboard = get_main_keyboard()
    
    # Send response
    if keyboard:
        await query.message.reply_text(message, parse_mode='Markdown', reply_markup=keyboard)
    else:
        await query.message.reply_text(message, parse_mode='Markdown')

# =============================================================================
# ⏰ DAILY REPORT (Optional)
# =============================================================================

async def send_daily_report(context):
    """Send daily report at scheduled time"""
    data, error = api_get('/safety_status')
    if error:
        return
    
    d = data
    daily_pnl = d.get('daily_pnl', 0)
    total_pnl = d.get('total_pnl', 0)
    total_trades = d.get('total_trades', 0)
    current_equity = d.get('current_equity', 0)
    
    if daily_pnl > 100:
        grade = "🏆 EXCELLENT DAY"
    elif daily_pnl > 0:
        grade = "✅ PROFITABLE DAY"
    elif daily_pnl == 0:
        grade = "⚪ BREAK-EVEN"
    else:
        grade = "📉 LOSS DAY"
    
    message = (
        f"📊 **DAILY REPORT** 📊\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {time.strftime('%Y-%m-%d')}\n\n"
        
        f"💰 **TODAY'S RESULT**\n"
        f"├ P/L: {format_pnl(daily_pnl)}\n"
        f"├ Result: {grade}\n"
        f"└ Trades: `{total_trades}`\n\n"
        
        f"📈 **OVERALL**\n"
        f"├ Total P/L: {format_pnl(total_pnl)}\n"
        f"└ Equity: `${current_equity:,.2f}`\n\n"
        
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌙 Good night! See you tomorrow."
    )
    
    await context.bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')

# =============================================================================
# 🚀 MAIN
# =============================================================================

async def post_init_callback(application):
    """Called after bot is initialized"""
    try:
        await application.bot.send_message(
            chat_id=CHAT_ID, 
            text=(
                "🤖 **OBOT TELEGRAM BOT STARTED** 🤖\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "✅ Bot is online and ready!\n"
                "📊 Use /status to check system\n"
                "🎛️ Use /menu for quick buttons\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        print(f"⚠️ Could not send startup message: {e}")


def main():
    """Start the Telegram Bot"""
    print(f"🔐 Token: {TELEGRAM_TOKEN[:10]}...")
    print(f"📱 Chat ID: {CHAT_ID}")

    max_retries = 10
    retry_count = 0
    base_delay = 5
    
    while retry_count < max_retries:
        try:
            application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init_callback).build()
            
            # Register command handlers
            commands = [
                ("start", start_command),
                ("stop", stop_command),
                ("status", status_command),
                ("news", news_command),
                ("performance", performance_command),
                ("safety", safety_command),
                ("reset_safety", reset_safety_command),
                ("menu", menu_command),
                ("help", help_command),
                ("fix", fix_command),
                ("update_ea", update_ea_command),
                ("restart_api", restart_api_command),
            ]
            
            for cmd, handler in commands:
                application.add_handler(CommandHandler(cmd, handler))
            
            # Callback handler for inline buttons
            application.add_handler(CallbackQueryHandler(button_callback))
            
            print("🚀 Starting Telegram Bot...")
            print(f"📋 Registered {len(commands)} commands")
            application.run_polling(allowed_updates=telegram.Update.ALL_TYPES)
            
            break
            
        except telegram.error.InvalidToken as e:
            print(f"❌ Invalid Token: {e}")
            raise
            
        except Exception as e:
            retry_count += 1
            delay = min(base_delay * (2 ** (retry_count - 1)), 300)
            print(f"❌ Error: {e}")
            print(f"🔄 Retry {retry_count}/{max_retries} in {delay}s...")
            time.sleep(delay)
    
    if retry_count >= max_retries:
        print(f"❌ Max retries exceeded. Exiting.")


if __name__ == '__main__':
    main()

