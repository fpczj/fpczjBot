
import logging
import json
import os
import sqlite3
import re
from datetime import date, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, JobQueue
from telegram.ext import filters
import threading
import time
import copy

# config和save_config定义补充，防止未定义变量
CONFIG_PATH = "config.json"
try:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
except Exception:
    config = {"admins": [], "authorized": [], "auth_expire": {}}

def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def load_typo_dict():
    if not os.path.exists(TYPO_DICT_PATH):
        with open(TYPO_DICT_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)
        return {}
    else:
        with open(TYPO_DICT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

def save_typo_dict(typo_dict):
    with open(TYPO_DICT_PATH, "w", encoding="utf-8") as f:
        json.dump(typo_dict, f, ensure_ascii=False, indent=2)

def load_users():
    if not os.path.exists(USERS_PATH):
        with open(USERS_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)
        return {}
    else:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

def save_users(users):
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

async def typo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat = update.effective_chat
    # 群组内管理员仅能授权
    if chat.type in ["group", "supergroup"]:
        if is_admin(user_id) and not text.startswith("授权"):
            return
        if not (is_admin(user_id) or is_authorized(user_id)):
            return
    else:
        # 私聊：管理员和授权用户均可
        if not (is_admin(user_id) or is_authorized(user_id)):
            return
    text = update.message.text.strip()
    global typo_dict
    if text.startswith("添加错别字"):
        m = re.match(r"添加错别字\s+(.+?)=(.+)", text)
        if m:
            k, v = m.group(1).strip(), m.group(2).strip()
            typo_dict[k] = v
            save_typo_dict(typo_dict)
            await update.message.reply_text(f"已添加错别字映射：{k} → {v}")
            return
        await update.message.reply_text("格式错误，应为：添加错别字 错别词=正确词")
        return
    if text.startswith("删除错别字"):
        m = re.match(r"删除错别字\s+(.+)", text)
        if m:
            k = m.group(1).strip()
            if k in typo_dict:
                del typo_dict[k]
                save_typo_dict(typo_dict)
                await update.message.reply_text(f"已删除错别字映射：{k}")
                return
            await update.message.reply_text("未找到该错别字映射")
            return
        await update.message.reply_text("格式错误，应为：删除错别字 错别词")
        return
    if text == "查看错别字":
        if typo_dict:
            msg = "当前错别字映射：\n" + "\n".join([f"{k} → {v}" for k,v in typo_dict.items()])
        else:
            msg = "当前无自定义错别字映射。"
        await update.message.reply_text(msg)
        return
    await update.message.reply_text("用法：\n添加错别字 错别词=正确词\n删除错别字 错别词\n查看错别字")
    return

async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        if is_admin(user_id):
            return
        if not is_authorized(user_id):
            return
    else:
        if not (is_admin(user_id) or is_authorized(user_id)):
            return
    global user_last_bill_id
    if user_id not in user_last_bill_id:
        await update.message.reply_text("无可撤销的记录。")
        return
    bill_id = user_last_bill_id[user_id]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM bills WHERE id=? AND user_id=?", (bill_id, str(user_id)))
    conn.commit()
    conn.close()
    await update.message.reply_text("已撤销上一条记账。")
    del user_last_bill_id[user_id]

# 常用描述推荐
async def suggest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        if is_admin(user_id):
            return
        if not is_authorized(user_id):
            return
    else:
        if not (is_admin(user_id) or is_authorized(user_id)):
            return

    user_id = update.effective_user.id
    if not is_admin_or_authorized(user_id):
        return
    global user_common_desc
    descs = sorted(user_common_desc.get(user_id, {}).items(), key=lambda x: -x[1])[:5]
    if descs:
        await update.message.reply_text("常用描述：" + ", ".join([d for d,c in descs]))
    else:
        await update.message.reply_text("暂无常用描述。")

# 本月统计命令
async def month_stat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin_or_authorized(user_id):
        return
    text = update.message.text.strip()
    # 智能解析时间
    import re
    from datetime import datetime
    today = date.today()
    # 默认本月
    year = today.year
    month = today.month
    # 支持“上月统计”“3月统计”“去年1月统计”等
    m1 = re.match(r"^(去年|[12]?\d{3,4}年)?(\d{1,2})?月?统计$", text)
    m2 = re.match(r"^上月统计$", text)
    if m2:
        if month == 1:
            year -= 1
            month = 12
        else:
            month -= 1
    elif m1:
        y, m = m1.group(1), m1.group(2)
        if y:
            if y == "去年":
                year -= 1
            else:
                year = int(re.sub(r"年", "", y))
        if m:
            month = int(m)
    # 生成查询字符串
    month_str = f"{year}-{month:02d}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 分类统计支出
    c.execute("SELECT category, SUM(amount) FROM bills WHERE user_id=? AND type='expense' AND strftime('%Y-%m', date)=? GROUP BY category", (str(user_id), month_str))
    expense_rows = c.fetchall()
    # 分类统计收入
    c.execute("SELECT category, SUM(amount) FROM bills WHERE user_id=? AND type='income' AND strftime('%Y-%m', date)=? GROUP BY category", (str(user_id), month_str))
    income_rows = c.fetchall()
    # 总支出
    c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='expense' AND strftime('%Y-%m', date)=?", (str(user_id), month_str))
    total_expense = c.fetchone()[0] or 0.0
    # 总收入
    c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='income' AND strftime('%Y-%m', date)=?", (str(user_id), month_str))
    total_income = c.fetchone()[0] or 0.0
    conn.close()
    msg = f"{month_str}统计\n"
    msg += "\n【支出分类】\n"
    if expense_rows:
        for cat, amt in expense_rows:
            msg += f"{cat or '未分类'}：{amt:.2f}\n"
    else:
        msg += "无支出记录\n"
    msg += f"总支出：{total_expense:.2f}\n"
    msg += "\n【收入分类】\n"
    if income_rows:
        for cat, amt in income_rows:
            msg += f"{cat or '未分类'}：{amt:.2f}\n"
    else:
        msg += "无收入记录\n"
    msg += f"总收入：{total_income:.2f}"
    await update.message.reply_text(msg)
    reset_state(user_id)
logger = logging.getLogger(__name__)

CONFIG_PATH = "config.json"
DB_PATH = "data.db"
USERS_PATH = "users.json"
TYPO_DICT_PATH = "typo_dict.json"

WAITING, BILL_TYPE, BILL_MONTH, REPORT_MONTH, CLEAR_TYPE, AUTH_TYPE, AUTH_USER, UNAUTH_USER, QUERY_TYPE, QUERY_DATE, INCOME_MONTH, PENDING_NL_RECORD, AUTH_DAYS = range(13)
# 用户自定义分类命令
async def category_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        if is_admin(user_id):
            return
        if not is_authorized(user_id):
            return
    else:
        if not (is_admin(user_id) or is_authorized(user_id)):
            return
    text = update.message.text.strip()
    global category_map
    if text.startswith("添加分类"):
        m = re.match(r"添加分类\s+(.+?)=(.+)", text)
        if m:
            k, v = m.group(1).strip(), m.group(2).strip()
            category_map[k] = v
            save_category_map(category_map)
            await update.message.reply_text(f"已添加分类映射：{k} → {v}")
            return
        else:
            await update.message.reply_text("格式错误，应为：添加分类 描述=分类")
            return
    if text.startswith("删除分类"):
        m = re.match(r"删除分类\s+(.+)", text)
        if m:
            k = m.group(1).strip()
            if k in category_map:
                del category_map[k]
                save_category_map(category_map)
                await update.message.reply_text(f"已删除分类映射：{k}")
                return
            await update.message.reply_text("未找到该分类映射")
            return
        await update.message.reply_text("格式错误，应为：删除分类 描述")
        return
    if text == "查看分类":
        if category_map:
            msg = "当前分类映射：\n" + "\n".join([f"{k} → {v}" for k,v in category_map.items()])
        else:
            msg = "当前无自定义分类映射。"
        await update.message.reply_text(msg)
        return
    await update.message.reply_text("用法：\n添加分类 描述=分类\n删除分类 描述\n查看分类")
    return
    with open(TYPO_DICT_PATH, "w", encoding="utf-8") as f:
        json.dump(typo_dict, f, ensure_ascii=False, indent=2)

typo_dict = load_typo_dict()

# 记录每个用户最近一条有效记账（用于“同上”“上次”补全）
user_last_record = {}
# 用户自定义分类映射
CATEGORY_MAP_PATH = "category_map.json"
def load_category_map():
    if not os.path.exists(CATEGORY_MAP_PATH):
        with open(CATEGORY_MAP_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)
        return {}
    else:
        with open(CATEGORY_MAP_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
def save_category_map(category_map):
    with open(CATEGORY_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(category_map, f, ensure_ascii=False, indent=2)
category_map = load_category_map()

# 记录每个用户常用描述（用于推荐/补全）
user_common_desc = {}

# 记录每个用户最近一条账单ID（用于撤销/修改）
user_last_bill_id = {}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            type TEXT,
            amount REAL,
            category TEXT,
            description TEXT,
            date TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

import os
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    # 本地调试可直接写死
    TOKEN = "7536100847:AAHslrzRe8eo9NmquNBSaYwSg0cgBU28GyM"

def is_admin_or_authorized(user_id):
    return str(user_id) in config["admins"] or str(user_id) in config["authorized"]

def is_admin(user_id):
    return str(user_id) in config["admins"]

def is_authorized(user_id):
    # 检查授权有效期
    uid = str(user_id)
    if uid in config["authorized"]:
        # 检查授权时间戳
        if "auth_expire" in config and uid in config["auth_expire"]:
            if time.time() < config["auth_expire"][uid]:
                return True
            else:
                # 过期自动移除
                config["authorized"].remove(uid)
                del config["auth_expire"][uid]
                save_config(config)
                return False
        else:
            # 没有时间戳，视为无效
            config["authorized"].remove(uid)
            save_config(config)
            return False
    return False

user_state = {}
user_temp = {}
user_timeouts = {}
user_owner = {}  # 记录每个用户当前状态的 owner
# 增加超时自动返回待命状态功能
user_timer = {}
TIMEOUT_SECONDS = 5

def reset_state(user_id):
    user_state[user_id] = WAITING
    user_temp[user_id] = {}
    if user_id in user_owner:
        del user_owner[user_id]
    # 主动取消并清理定时器，防止泄漏
    if user_id in user_timer:
        user_timer[user_id].cancel()
        del user_timer[user_id]

def set_timeout(user_id):
    def timeout():
        user_state[user_id] = WAITING
        user_temp[user_id] = {}
        if user_id in user_timer:
            del user_timer[user_id]
        if user_id in user_owner:
            del user_owner[user_id]
    if user_id in user_timer:
        user_timer[user_id].cancel()
    timer = threading.Timer(TIMEOUT_SECONDS, timeout)
    user_timer[user_id] = timer
    timer.start()

async def show_menu(update):
    menu = (
        "欢迎使用记账机器人！\n\n"
        "可用指令：\n"
        "1.账单、报表、清除、查询、返回、授权、收入、帮助\n"
        "2.直接输入“收入 金额”或“收入 金额 描述”为收入；\n"
        "3.输入“+或-金额 描述”为支出。\n"
        "4.支持自然语言记账，如“昨天买菜 50”“今天买菜 30元”等，需本人确认。\n"
        "5.支持关键词快捷查询：如“今天收入”“本月支出”“上月收入”等。"
    )
    await update.message.reply_text(menu)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        if is_admin(user_id):
            return
        if not is_authorized(user_id):
            return
    else:
        if not (is_admin(user_id) or is_authorized(user_id)):
            return
    help_text = (
        "1.直接输入“收入 金额”或“收入 金额 描述”为收入；\n"
        "2.输入“+或-金额 描述”为支出。\n"
        "3.支持自然语言记账，如“昨天买菜 50”“今天买菜 30元”等，需本人确认。\n"
        "4.支持关键词快捷查询：如“今天收入”“本月支出”“上月收入”等。"
    )
    await update.message.reply_text(help_text)
    reset_state(user_id)

async def bill_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin_or_authorized(user_id):
        return
    await update.message.reply_text("您要查询的是收入账单还是支出账单？1“收入” 2“支出”")
    user_state[user_id] = BILL_TYPE
    user_temp[user_id] = {}
    user_owner[user_id] = user_id
    set_timeout(user_id)

async def handle_bill_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # 只允许 owner 继续操作
    if user_state.get(user_id, WAITING) != BILL_TYPE or user_owner.get(user_id) != user_id:
        return
    if user_state.get(user_id, WAITING) != BILL_TYPE:
        return
    text = update.message.text.strip()
    if text == "1":
        user_temp[user_id]["bill_type"] = "income"
        await update.message.reply_text("请输入月份：")
        user_state[user_id] = BILL_MONTH
        set_timeout(user_id)
    elif text == "2":
        user_temp[user_id]["bill_type"] = "expense"
        await update.message.reply_text("请输入月份：")
        user_state[user_id] = BILL_MONTH
        set_timeout(user_id)
    else:
        await update.message.reply_text("您的输入有误。")
        reset_state(user_id)
        return

async def handle_bill_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # 只允许 owner 继续操作
    if user_state.get(user_id, WAITING) != BILL_MONTH or user_owner.get(user_id) != user_id:
        return
    if user_state.get(user_id, WAITING) != BILL_MONTH:
        return
    text = update.message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 12):
        await update.message.reply_text("您的输入有误。")
        reset_state(user_id)
        return
    month = int(text)
    bill_type = user_temp[user_id].get("bill_type")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, amount, category, description, date FROM bills WHERE user_id=? AND type=? AND strftime('%m', date)=? ORDER BY id ASC",
        (str(user_id), bill_type, f"{month:02d}")
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text(f"{month}月无账单信息。")
    else:
        msg = f"{month}月{'收入' if bill_type=='income' else '支出'}账单明细：\n"
        for i, row in enumerate(rows, 1):
            msg += f"{i} | {row[1]:.2f} | {row[2]} | {row[3]} | {row[4]}\n"
        await update.message.reply_text(msg)
    reset_state(user_id)

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin_or_authorized(user_id):
        return
    await update.message.reply_text("请输入月份：")
    user_state[user_id] = REPORT_MONTH
    user_temp[user_id] = {}
    user_owner[user_id] = user_id
    set_timeout(user_id)

async def handle_report_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # 只允许 owner 继续操作
    if user_state.get(user_id, WAITING) != REPORT_MONTH or user_owner.get(user_id) != user_id:
        return
    if user_state.get(user_id, WAITING) != REPORT_MONTH:
        return
    text = update.message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 12):
        await update.message.reply_text("您的输入有误。")
        reset_state(user_id)
        return
    month = int(text)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT type, category, SUM(amount) FROM bills WHERE user_id=? AND strftime('%m', date)=? GROUP BY type, category",
        (str(user_id), f"{month:02d}")
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text(f"{month}月无账单信息。")
    else:
        msg = f"{month}月账单分类汇总：\n"
        for row in rows:
            msg += f"{'收入' if row[0]=='income' else '支出'} | {row[1]} | {row[2]:.2f}\n"
        await update.message.reply_text(msg)
    reset_state(user_id)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin_or_authorized(user_id):
        return
    await update.message.reply_text("清除全部还是今天的记录？（请回复“1 全部”或“2 今天”）")
    user_state[user_id] = CLEAR_TYPE
    user_temp[user_id] = {}
    user_owner[user_id] = user_id
    set_timeout(user_id)

async def handle_clear_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # 只允许 owner 继续操作
    if user_state.get(user_id, WAITING) != CLEAR_TYPE or user_owner.get(user_id) != user_id:
        return
    if user_state.get(user_id, WAITING) != CLEAR_TYPE:
        return
    text = update.message.text.strip()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if text == "1":
        c.execute("DELETE FROM bills WHERE user_id=?", (str(user_id),))
        conn.commit()
        await update.message.reply_text("您已清除所有记录。")
    elif text == "2":
        today = date.today().strftime("%Y-%m-%d")
        c.execute("DELETE FROM bills WHERE user_id=? AND date=?", (str(user_id), today))
        conn.commit()
        await update.message.reply_text("您已清除今天记录。")
    else:
        await update.message.reply_text("您的输入有误。")
        conn.close()
        reset_state(user_id)
        return
    conn.close()
    reset_state(user_id)

async def query_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin_or_authorized(user_id):
        return
    await update.message.reply_text("您要查询收入还是支出？\n1 收入 2 支出")
    user_state[user_id] = QUERY_TYPE
    user_temp[user_id] = {}
    user_owner[user_id] = user_id
    set_timeout(user_id)

async def handle_query_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # 只允许 owner 继续操作
    if user_state.get(user_id, WAITING) != QUERY_TYPE or user_owner.get(user_id) != user_id:
        return
    if user_state.get(user_id, WAITING) != QUERY_TYPE:
        return
    text = update.message.text.strip()
    if text == "1":
        user_temp[user_id]["query_type"] = "income"
        await update.message.reply_text("请输入查询日期：（比如2025-6-1或2025-6-1至2025-7-31,这样的时间格式）")
        user_state[user_id] = QUERY_DATE
        set_timeout(user_id)
    elif text == "2":
        user_temp[user_id]["query_type"] = "expense"
        await update.message.reply_text("请输入查询日期：（比如2025-6-1或2025-6-1至2025-7-31,这样的时间格式）")
        user_state[user_id] = QUERY_DATE
        set_timeout(user_id)
    else:
        await update.message.reply_text("您的输入有误。")
        reset_state(user_id)
        return

async def handle_query_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_state.get(user_id, WAITING) != QUERY_DATE or user_owner.get(user_id) != user_id:
        return
    text = update.message.text.strip()
    today = date.today()
    # 支持多种日期格式和自然语言时间词
    def parse_date_range(s):
        s = s.replace('/', '-').replace('.', '-')
        # 匹配区间
        m_range = re.match(r"(.+)[至到](.+)", s)
        if m_range:
            start = m_range.group(1).strip()
            end = m_range.group(2).strip()
            start_date = parse_single_date(start, is_start=True)
            end_date = parse_single_date(end, is_start=False)
            return start_date, end_date
        else:
            d1, d2 = parse_single_date(s, is_start=True), parse_single_date(s, is_start=False)
            return d1, d2

    def parse_single_date(s, is_start=True):
        s = s.strip()
        today = date.today()
        # 支持“昨天/前天/今日/今天/上周/上月/本月/今年/去年”
        if s in ["昨天", "昨日"]:
            d = today - timedelta(days=1)
            return d.strftime("%Y-%m-%d")
        if s == "前天":
            d = today - timedelta(days=2)
            return d.strftime("%Y-%m-%d")
        if s in ["今天", "今日"]:
            return today.strftime("%Y-%m-%d")
        if s == "上月" or s == "上个月":
            first = today.replace(day=1)
            last_month_end = first - timedelta(days=1)
            if is_start:
                return last_month_end.replace(day=1).strftime("%Y-%m-%d")
            else:
                return last_month_end.strftime("%Y-%m-%d")
        if s == "本月":
            if is_start:
                return today.replace(day=1).strftime("%Y-%m-%d")
            else:
                return today.strftime("%Y-%m-%d")
        if s == "上周":
            start = today - timedelta(days=today.weekday()+7)
            end = start + timedelta(days=6)
            return start.strftime("%Y-%m-%d") if is_start else end.strftime("%Y-%m-%d")
        if s == "本周":
            start = today - timedelta(days=today.weekday())
            return start.strftime("%Y-%m-%d") if is_start else today.strftime("%Y-%m-%d")
        # 2024年7月2日、2024-7-2、7月2日、7-2、7月、7
        m1 = re.match(r'^(\d{4})[年-](\d{1,2})[月-](\d{1,2})[日号]?$', s)
        if m1:
            return f"{int(m1.group(1)):04d}-{int(m1.group(2)):02d}-{int(m1.group(3)):02d}"
        m2 = re.match(r'^(\d{1,2})[月-](\d{1,2})[日号]?$', s)
        if m2:
            return f"{today.year:04d}-{int(m2.group(1)):02d}-{int(m2.group(2)):02d}"
        m3 = re.match(r'^(\d{4})[年-](\d{1,2})[月]$', s)
        if m3:
            if is_start:
                return f"{int(m3.group(1)):04d}-{int(m3.group(2)):02d}-01"
            else:
                y, m = int(m3.group(1)), int(m3.group(2))
                if m == 12:
                    next_month = date(y+1, 1, 1)
                else:
                    next_month = date(y, m+1, 1)
                last_day = (next_month - timedelta(days=1)).day
                return f"{y:04d}-{m:02d}-{last_day:02d}"
        m4 = re.match(r'^(\d{1,2})[月]$', s)
        if m4:
            y, m = today.year, int(m4.group(1))
            if is_start:
                return f"{y:04d}-{m:02d}-01"
            else:
                if m == 12:
                    next_month = date(y+1, 1, 1)
                else:
                    next_month = date(y, m+1, 1)
                last_day = (next_month - timedelta(days=1)).day
                return f"{y:04d}-{m:02d}-{last_day:02d}"
        m5 = re.match(r'^(\d{4})$', s)
        if m5:
            if is_start:
                return f"{int(m5.group(1)):04d}-01-01"
            else:
                return f"{int(m5.group(1)):04d}-12-31"
        m6 = re.match(r'^(\d{1,2})$', s)
        if m6:
            y, m = today.year, int(m6.group(1))
            if is_start:
                return f"{y:04d}-{m:02d}-01"
            else:
                if m == 12:
                    next_month = date(y+1, 1, 1)
                else:
                    next_month = date(y, m+1, 1)
                last_day = (next_month - timedelta(days=1)).day
                return f"{y:04d}-{m:02d}-{last_day:02d}"
        m7 = re.match(r'^(\d{1,2})[日号]$', s)
        if m7:
            y, m, d = today.year, today.month, int(m7.group(1))
            return f"{y:04d}-{m:02d}-{d:02d}"
        m8 = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', s)
        if m8:
            return f"{int(m8.group(1)):04d}-{int(m8.group(2)):02d}-{int(m8.group(3)):02d}"
        return None

    start, end = parse_date_range(text)
    if not start or not end:
        await update.message.reply_text("您的输入有误。")
        reset_state(user_id)
        return
    qtype = user_temp[user_id].get("query_type")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND date BETWEEN ? AND ?",
        (str(user_id), qtype, start, end)
    )
    total = c.fetchone()[0]
    c.execute(
        "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? AND date BETWEEN ? AND ? ORDER BY date ASC",
        (str(user_id), qtype, start, end)
    )
    rows = c.fetchall()
    conn.close()
    total = total if total else 0.0
    if rows:
        msg = f"{'收入' if qtype=='income' else '支出'}总金额：{total:.2f}\n明细：\n"
        for row in rows:
            msg += f"{row[3]} | {row[0]:.2f} | {row[1]} | {row[2]}\n"
    else:
        msg = f"{'收入' if qtype=='income' else '支出'}总金额：{total:.2f}\n无明细记录。"
    await update.message.reply_text(msg)
    reset_state(user_id)

async def return_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin_or_authorized(user_id):
        return
    await update.message.reply_text("已返回到待命状态。")
    reset_state(user_id)

async def auth_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.message.chat.type if hasattr(update.message, 'chat') else 'private'
    # 只允许群组管理员触发授权
    if not (chat_type != "private" and is_admin(user_id)):
        return
    await update.message.reply_text("1 授权，2 取消被授权人")
    user_state[user_id] = AUTH_TYPE
    user_temp[user_id] = {}
    user_owner[user_id] = user_id
    set_timeout(user_id)

async def handle_auth_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.message.chat.type if hasattr(update.message, 'chat') else 'private'
    # 只允许 owner 继续操作，且只在 AUTH_TYPE 状态下处理
    if user_state.get(user_id, WAITING) != AUTH_TYPE or user_owner.get(user_id) != user_id:
        return
    text = update.message.text.strip()
    # 只禁止管理员在私聊授权，群组管理员可继续输入1/2
    if chat_type == "private" and is_admin(user_id):
        return
    if text == "1":
        await update.message.reply_text("请输入被授权人用户名：")
        user_state[user_id] = AUTH_USER
        set_timeout(user_id)
    elif text == "2":
        await update.message.reply_text("请输入将要取消被授权人的用户名：")
        user_state[user_id] = UNAUTH_USER
        set_timeout(user_id)
    else:
        await update.message.reply_text("您的输入有误。")
        reset_state(user_id)
        return

async def handle_auth_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_state.get(user_id, WAITING) != AUTH_USER:
        return
    text = update.message.text.strip()
    if not text.startswith("@"): 
        await update.message.reply_text("您的输入有误。"); reset_state(user_id); return
    username = text.split()[0]
    users = load_users()
    if username in users:
        user_temp[user_id] = {"auth_username": username}
        await update.message.reply_text("请输入授权天数：")
        user_state[user_id] = AUTH_DAYS
        set_timeout(user_id)
    else:
        await update.message.reply_text("群组内无该用户。")
        reset_state(user_id)

# 新增处理授权天数的状态
async def handle_auth_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_state.get(user_id, WAITING) != AUTH_DAYS:
        return
    text = update.message.text.strip()
    # 支持中文数字一/二
    cn_digit_map = {'一': 1, '二': 2}
    if text in cn_digit_map:
        days = cn_digit_map[text]
    else:
        try:
            days = int(text)
            if days <= 0 or days > 365:
                raise ValueError()
        except Exception:
            await update.message.reply_text("请输入正确的天数（1-365）！"); reset_state(user_id); return
    username = user_temp[user_id].get("auth_username")
    users = load_users()
    if username in users:
        uid = users[username]
        if uid not in config["authorized"]:
            config["authorized"].append(uid)
        if "auth_expire" not in config:
            config["auth_expire"] = {}
        config["auth_expire"][uid] = time.time() + days*24*3600
        save_config(config)
        await update.message.reply_text(f"你已授权{username} {days}天。\n授权时间到期后被授权人需要重新授权。")
    else:
        await update.message.reply_text("群组内无该用户。")
    reset_state(user_id)

async def handle_unauth_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_state.get(user_id, WAITING) != UNAUTH_USER:
        return
    username = update.message.text.strip()
    if not username.startswith("@"):
        await update.message.reply_text("您的输入有误。")
        reset_state(user_id)
        return
    users = load_users()
    if username in users:
        uid = users[username]
        if uid in config["authorized"]:
            config["authorized"].remove(uid)
            if "auth_expire" in config and uid in config["auth_expire"]:
                del config["auth_expire"][uid]
            save_config(config)
            await update.message.reply_text(f"已取消{username}所有权限。")
        else:
            await update.message.reply_text("该用户未授权。")
    else:
        await update.message.reply_text("群组内无该用户。")
    reset_state(user_id)

async def income_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin_or_authorized(user_id):
        return
    text = update.message.text.strip()
    if "支出" in text:
        user_temp[user_id]["query_type"] = "expense"
    else:
        user_temp[user_id]["query_type"] = "income"
    await update.message.reply_text("请输入月份：")
    user_state[user_id] = INCOME_MONTH
    user_temp[user_id] = user_temp.get(user_id, {})
    user_owner[user_id] = user_id
    set_timeout(user_id)

async def handle_income_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # 只允许 owner 继续操作
    if user_state.get(user_id, WAITING) != INCOME_MONTH or user_owner.get(user_id) != user_id:
        return
    if user_state.get(user_id, WAITING) != INCOME_MONTH:
        return
    text = update.message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 12):
        await update.message.reply_text("您的输入有误。")
        reset_state(user_id)
        return
    month = int(text)
    query_type = user_temp[user_id].get("query_type", "income")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? AND strftime('%m', date)=? ORDER BY id ASC",
        (str(user_id), query_type, f"{month:02d}")
    )
    rows = c.fetchall()
    c.execute(
        "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND strftime('%m', date)=?",
        (str(user_id), query_type, f"{month:02d}")
    )
    total = c.fetchone()[0] or 0.0
    conn.close()
    type_str = "收入" if query_type == "income" else "支出"
    if not rows:
        await update.message.reply_text(f"{month}月无{type_str}账单信息。")
    else:
        msg = f"{month}月{type_str}明细：\n"
        for i, row in enumerate(rows, 1):
            msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
        msg += f"{month}月总{type_str}：{total:.2f}"
        await update.message.reply_text(msg)
    reset_state(user_id)

async def reply_record_success(update, user_id, record_type, amount, desc, record_date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? ORDER BY id ASC",
        (str(user_id), record_type)
    )
    all_rows = c.fetchall()
    rows = all_rows[-5:]
    today = date.today().strftime("%Y-%m-%d")
    c.execute(
        "SELECT COUNT(*) FROM bills WHERE user_id=? AND type=? AND date=?",
        (str(user_id), record_type, today)
    )
    today_count = c.fetchone()[0]
    c.execute(
        "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND date=?",
        (str(user_id), record_type, today)
    )
    day_total = c.fetchone()[0] or 0.0
    c.execute(
        "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=?",
        (str(user_id), record_type, record_date[:7])
    )
    month_total = c.fetchone()[0] or 0.0
    conn.close()
    msg = f"记录成功：{amount}，{desc}\n\n最近5笔{'收入' if record_type=='income' else '支出'}: (今天{'收入' if record_type=='income' else '支出'}:{today_count}笔)\n"
    start_num = len(all_rows) - len(rows) + 1
    for i, row in enumerate(rows, start_num):
        msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} |"
        if row[3] != today:
            msg += f" ({row[3]})"
        msg += "\n"
    msg += f"\n当天累计{'收入' if record_type=='income' else '支出'}：{day_total:.2f}\n本月累计{'收入' if record_type=='income' else '支出'}：{month_total:.2f}"
    await update.message.reply_text(msg)

# 优化自然语言记账解析
# 智能自然语言记账解析

def parse_natural_language_record(text):
    # 错别字/拼音映射（内置+用户自定义）
    typo_map = {
        'maicai': '买菜', 'mai cai': '买菜', 'zhifubao': '支付宝', 'zfb': '支付宝',
        'shuifei': '水费', 'dianfei': '电费', 'huafei': '话费', 'shuiguo': '水果',
        'kuaidi': '快递', 'fandian': '饭店', 'chongzhi': '充值', 'huankuan': '还款',
        'gongzi': '工资', 'jiangjin': '奖金', 'fangzu': '房租', 'jiaotong': '交通',
        'yule': '娱乐', 'xuexi': '学习', 'yiliao': '医疗', 'tongxun': '通讯',
        'gouwu': '购物', 'suining': '苏宁', 'pinduoduo': '拼多多', 'taobao': '淘宝', 'jingdong': '京东',
    }
    global typo_dict
    typo_map.update(typo_dict)
    for k, v in typo_map.items():
        if k in text:
            text = text.replace(k, v)
    text = text.replace('元', '').replace('块', '').replace('￥', '').replace('。', '').replace(',', '').replace('，', '').strip()

    # 口语金额映射
    cn_num = {'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'百':100,'千':1000,'万':10000}
    def chinese_to_digit(s):
        total = 0
        unit = 1
        num = 0
        s = s.replace('佰','百').replace('仟','千')
        for c in reversed(s):
            if c in cn_num:
                val = cn_num[c]
                if val >= 10:
                    if num == 0:
                        num = 1
                    unit = val
                else:
                    total += val * unit
                    num = 0
        if total > 0:
            return total
        return None


    m_cn = re.search(r'([一二两三四五六七八九十百千万]+)\s*$', text)
    if m_cn:
        digit = chinese_to_digit(m_cn.group(1))
        if digit:
            text = text.replace(m_cn.group(1), str(digit))

    # 解析“描述 金额”或“金额 描述”
    m1 = re.match(r"(.+?)\s*([0-9]+(?:\.[0-9]+)?)$", text)
    m2 = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*(.+)$", text)
    today_str = date.today().strftime("%Y-%m-%d")
    if m1:
        desc = m1.group(1).strip()
        amount = float(m1.group(2))
        return {
            "type": "expense",
            "amount": amount,
            "category": "其他",
            "description": desc,
            "date": today_str
        }
    if m2:
        amount = float(m2.group(1))
        desc = m2.group(2).strip()
        return {
            "type": "expense",
            "amount": amount,
            "category": "其他",
            "description": desc,
            "date": today_str
        }
    return None
    # 清理多余else和死代码，保证缩进正确
    amount_match = re.search(r'([+-]?\d+[.]?\d*)', text)
    if amount_match:
        amount = float(amount_match.group(1))
        text = text.replace(amount_match.group(1), '', 1)
    income_keywords = ['收入', '到账', '工资', '奖金', '报销', '收到', '进账', '转账', '返现', '红包', '利息', '发了', '发工资', '奖金', '入账', '进账']
    expense_keywords = ['支出', '花了', '花费', '买', '付', '缴', '交', '消费', '花', '支', '扣', '充值', '还款', '转出', '提现', '购买', '缴费', '支付', '用了', '扣费', '支出去', '花出去']
    type_ = None
    for word in income_keywords:
        if word in text:
            type_ = 'income'
            text = text.replace(word, '')
            break
    if not type_:
        for word in expense_keywords:
            if word in text:
                type_ = 'expense'
                text = text.replace(word, '')
                break
    if amount is not None and amount < 0:
        type_ = 'expense'
        amount = abs(amount)
    if not type_:
        if any(w in text for w in income_keywords):
            type_ = 'income'
        elif any(w in text for w in expense_keywords):
            type_ = 'expense'
        else:
            type_ = 'expense'
    desc = text.strip() if text.strip() else '未填写'
    category = get_category(desc)
    if amount is not None:
        return {
            'type': type_,
            'amount': amount,
            'description': desc,
            'category': category,
            'date': record_date.strftime('%Y-%m-%d')
        }
    return None
# 根据描述内容自动分类
def get_category(desc):
    # 用户自定义分类优先
    global category_map
    if desc in category_map:
        return category_map[desc]
    # 自动学习：如果历史上该描述多次归为同一类，则自动记忆
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT category, COUNT(*) FROM bills WHERE description=? GROUP BY category ORDER BY COUNT(*) DESC", (desc,))
    row = c.fetchone()
    conn.close()
    if row and row[1] >= 2:
        return row[0]
    # 关键词/模糊匹配
    category_keywords = {
        '餐饮': ['买菜', 'maicai', 'mai cai', '午餐', '晚餐', '早餐', '饭', '餐饮', '外卖', '聚餐', '饮料', '奶茶', '水果', '零食'],
        '生活': ['水电', 'shuifei', 'dianfei', '水费', '电费', '燃气', '物业', '生活', '日用品', '洗衣', '家政'],
        '交通': ['公交', '地铁', '打车', '滴滴', '加油', '停车', '交通', '高铁', '火车', '飞机', '机票', 'jiaotong'],
        '娱乐': ['电影', 'KTV', '游戏', '娱乐', '旅游', '门票', '聚会', 'yule'],
        '学习': ['学费', '培训', '书', '教材', '学习', '考试', '课程', 'xuexi'],
        '医疗': ['医院', '药', '医疗', '体检', '挂号', 'yiliao'],
        '通讯': ['话费', '流量', '宽带', '通讯', '手机', 'huafei', 'tongxun'],
        '房租': ['房租', '租金', 'fangzu'],
        '购物': ['购物', '网购', '京东', '淘宝', '拼多多', '苏宁', 'gouwu', 'jingdong', 'taobao', 'pinduoduo', 'suining'],
        '工资': ['工资', '薪水', '奖金', 'gongzi', 'jiangjin'],
        '其他': []
    }
    import difflib
    desc_lower = desc.lower()
    for cat, keywords in category_keywords.items():
        for k in keywords:
            if k in desc or k in desc_lower:
                return cat
    all_keywords = [(cat, k) for cat, keys in category_keywords.items() for k in keys]
    best = None
    best_score = 0.0
    for cat, k in all_keywords:
        score = difflib.SequenceMatcher(None, desc_lower, k).ratio()
        if score > best_score:
            best_score = score
            best = cat
    if best_score > 0.7:
        return best
    return '其他'

# 智能关键词/语义查询
def parse_keyword_query(text):
    text = text.replace('元', '').replace('块', '').replace('￥', '').replace('。', '').replace(',', '').replace('，', '').strip().lower()
    today = date.today()
    if '近7天' in text:
        start = today - timedelta(days=6)
        end = today
    elif '本周' in text:
        start = today - timedelta(days=today.weekday())
        end = today
    elif '上周' in text:
        start = today - timedelta(days=today.weekday()+7)
        end = start + timedelta(days=6)
    elif '去年' in text:
        start = date(today.year-1, 1, 1)
        end = date(today.year-1, 12, 31)
    elif '今年' in text:
        start = date(today.year, 1, 1)
        end = today
    elif '本月' in text:
        start = today.replace(day=1)
        end = today
    elif '上月' in text or '上个月' in text:
        first = today.replace(day=1)
        last_month_end = first - timedelta(days=1)
        start = last_month_end.replace(day=1)
        end = last_month_end
    else:
        start = end = None
    if '收入' in text:
        type_ = 'income'
    elif '支出' in text or '花' in text or '买' in text or '消费' in text:
        type_ = 'expense'
    else:
        type_ = None
    desc_keywords = ['买菜', '水电费', '工资', '房租', '购物', '餐饮', '交通', '娱乐', '学习', '医疗', '旅游', '通讯', '日用品', '其他', '衣服', '手机', '书']
    fuzzy_desc = None
    for word in desc_keywords:
        if word in text:
            fuzzy_desc = word
            break
    m = re.search(r'查.*?(\w+).*?花.*?多少', text)
    if m:
        fuzzy_desc = m.group(1)
        type_ = 'expense'
    return {
        'type': type_,
        'start_date': start,
        'end_date': end,
        'category': None,
        'fuzzy_desc': fuzzy_desc
    } if (type_ and (start or fuzzy_desc)) else None

async def try_natural_language_record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    global user_last_record
    if not is_admin_or_authorized(user_id):
        return False
    # 避免与其他格式冲突
    if text.startswith("收入") or re.match(r'^[+-]\d', text):
        return False
    # 语义增强：同上/上次补全
    # 口语化表达：再来一条/帮我记一笔/再记一次
    if text in ["同上", "上次"] and user_id in user_last_record:
        rec = copy.deepcopy(user_last_record[user_id])
        if rec:
            user_temp[user_id] = {"nl_record": rec}
            user_state[user_id] = PENDING_NL_RECORD
            user_owner[user_id] = user_id
            await update.message.reply_text(
                f"检测到记账意图（同上）：\n类型：{'收入' if rec['type']=='income' else '支出'}\n金额：{rec['amount']}\n描述：{rec['description']}\n日期：{rec['date']}\n\n1 确认记账 2 取消"
            )
            set_timeout(user_id)
            return True
        return False
    if text in ["再来一条", "再记一次", "帮我记一笔"] and user_id in user_last_record:
        rec = copy.deepcopy(user_last_record[user_id])
        if rec:
            user_temp[user_id] = {"nl_record": rec}
            user_state[user_id] = PENDING_NL_RECORD
            user_owner[user_id] = user_id
            await update.message.reply_text(
                f"检测到记账意图（口语化）：\n类型：{'收入' if rec['type']=='income' else '支出'}\n金额：{rec['amount']}\n描述：{rec['description']}\n日期：{rec['date']}\n\n1 确认记账 2 取消"
            )
            set_timeout(user_id)
            return True
        return False
    # 使用优化后的解析
    rec = parse_natural_language_record(text)
    if rec:
        user_temp[user_id] = {"nl_record": rec}
        user_state[user_id] = PENDING_NL_RECORD
        user_owner[user_id] = user_id  # 修正：设置owner，保证确认流程可用
        await update.message.reply_text(
            f"检测到记账意图：\n类型：{'收入' if rec['type']=='income' else '支出'}\n金额：{rec['amount']}\n描述：{rec['description']}\n日期：{rec['date']}\n\n1 确认记账 2 取消"
        )
        set_timeout(user_id)
        return True
    return False

import asyncio
async def handle_nl_record_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global user_last_record, user_common_desc, user_last_bill_id
    user_id = update.effective_user.id
    # 只允许 owner 继续操作
    if user_state.get(user_id) != PENDING_NL_RECORD or user_owner.get(user_id) != user_id:
        return
    text = update.message.text.strip()
    if text == "1":
        rec = user_temp[user_id].get("nl_record")
        if not rec:
            await update.message.reply_text("数据异常，未能记账。5秒后自动返回待命状态。")
            await asyncio.sleep(5)
            reset_state(user_id)
            return
        # 记录本用户最近一条有效记账
        user_last_record[user_id] = copy.deepcopy(rec)
        user_last_record[user_id] = copy.deepcopy(rec)
        if user_id not in user_common_desc:
            user_common_desc[user_id] = {}
        d = rec["description"]
        user_common_desc[user_id][d] = user_common_desc[user_id].get(d, 0) + 1
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO bills (user_id, type, amount, category, description, date) VALUES (?, ?, ?, ?, ?, ?)",
            (str(user_id), rec["type"], rec["amount"], rec["category"], rec["description"], rec["date"])
        )
        bill_id = c.lastrowid
        user_last_bill_id[user_id] = bill_id
        conn.commit()
        conn.close()
        await reply_record_success(update, user_id, rec["type"], rec["amount"], rec["description"], rec["date"])
        # 记账回复后即刻回到待命状态，无需等待
        reset_state(user_id)
        return
    if text == "2":
        await update.message.reply_text("已取消。")
        reset_state(user_id)
        return
    await update.message.reply_text("您的输入有误。5秒后自动返回待命状态。")
    await asyncio.sleep(5)
    reset_state(user_id)
    return
    def parse_date_range(s):
        s = s.replace('/', '-').replace('.', '-')
        m_range = re.match(r"(.+)[至到](.+)", s)
        if m_range:
            start = m_range.group(1).strip()
            end = m_range.group(2).strip()
            start_date = parse_single_date(start, is_start=True)
            end_date = parse_single_date(end, is_start=False)
            return start_date, end_date
        else:
            d1 = parse_single_date(s, is_start=True)
            d2 = parse_single_date(s, is_start=False)
            return d1, d2

    def parse_single_date(s, is_start=True):
        s = s.strip()
        today = date.today()
        if s in ["昨天", "昨日"]:
            d = today - timedelta(days=1)
            return d.strftime("%Y-%m-%d")
        if s == "前天":
            d = today - timedelta(days=2)
            return d.strftime("%Y-%m-%d")
        if s in ["今天", "今日"]:
            return today.strftime("%Y-%m-%d")
        if s == "上月" or s == "上个月":
            first = today.replace(day=1)
            last_month_end = first - timedelta(days=1)
            if is_start:
                return last_month_end.replace(day=1).strftime("%Y-%m-%d")
            else:
                return last_month_end.strftime("%Y-%m-%d")
        if s in ["本月", "这个月"]:
            first = today.replace(day=1)
            return first.strftime("%Y-%m-%d") if is_start else today.strftime("%Y-%m-%d")
        if s in ["今年"]:
            return today.replace(month=1, day=1).strftime("%Y-%m-%d") if is_start else today.strftime("%Y-%m-%d")
        if s == "去年":
            first = today.replace(month=1, day=1)
            last_year = first - timedelta(days=1)
            first_last_year = last_year.replace(month=1, day=1)
            return first_last_year.strftime("%Y-%m-%d") if is_start else last_year.strftime("%Y-%m-%d")
        if s in ["本周", "这周"]:
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
            return start.strftime("%Y-%m-%d") if is_start else end.strftime("%Y-%m-%d")
        if s == "上周":
            start = today - timedelta(days=today.weekday()+7)
            end = start + timedelta(days=6)
            return start.strftime("%Y-%m-%d") if is_start else end.strftime("%Y-%m-%d")
        return s
    if text.startswith("添加错别字"):
        m = re.match(r"添加错别字\s+(.+?)=(.+)", text)
        if m:
            k, v = m.group(1).strip(), m.group(2).strip()
            typo_dict[k] = v
            save_typo_dict(typo_dict)
            await update.message.reply_text(f"已添加错别字映射：{k} → {v}")
            return
        await update.message.reply_text("格式错误，应为：添加错别字 错别词=正确词")
        return
    if text.startswith("删除错别字"):
        m = re.match(r"删除错别字\s+(.+)", text)
        if m:
            k = m.group(1).strip()
            if k in typo_dict:
                del typo_dict[k]
                save_typo_dict(typo_dict)
                await update.message.reply_text(f"已删除错别字映射：{k}")
                return
            await update.message.reply_text("未找到该错别字映射")
            return
        await update.message.reply_text("格式错误，应为：删除错别字 错别词")
        return
    if text == "查看错别字":
        if typo_dict:
            msg = "当前错别字映射：\n" + "\n".join([f"{k} → {v}" for k,v in typo_dict.items()])
        else:
            msg = "当前无自定义错别字映射。"
        await update.message.reply_text(msg)
        return
    await update.message.reply_text("用法：\n添加错别字 错别词=正确词\n删除错别字 错别词\n查看错别字")
    return

async def handle_record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    if not is_admin_or_authorized(user_id):
        return False
    if username:
        users = load_users()
        at_name = f"@{username}"
        if at_name not in users:
            users[at_name] = str(user_id)
            save_users(users)
    text = update.message.text.strip()
    # 直接记账，无需确认
    m = re.match(r"^收入\s+([0-9]+(?:\.[0-9]+)?)(?:\s+(.+))?", text)
    if m:
        amount = float(m.group(1))
        desc = m.group(2) if m.group(2) else "未填写"
        today = date.today().strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO bills (user_id, type, amount, category, description, date) VALUES (?, 'income', ?, '其他', ?, ?)",
            (str(user_id), amount, desc, today)
        )
        conn.commit()
        conn.close()
        await reply_record_success(update, user_id, "income", amount, desc, today)
        reset_state(user_id)
        return True
    m = re.match(r"^([+-])\d+([0-9]+(?:\.[0-9]+)?)\s+(.+)", text)
    if m:
        amount = float(m.group(1))
        desc = m.group(2)
        today = date.today().strftime("%Y-%m-%d")
        type_ = "income" if m.group(1) == "+" else "expense"
        amount = amount if type_ == "income" else abs(amount)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO bills (user_id, type, amount, category, description, date) VALUES (?, ?, ?, '其他', ?, ?)",
            (str(user_id), type_, abs(amount), desc, today)
        )
        conn.commit()
        conn.close()
        await reply_record_success(update, user_id, type_, abs(amount), desc, today)
        reset_state(user_id)
        return True
    return False  # 防止其他格式如纯数字触发

async def quick_keyword_query(update, user_id, text):
    today = date.today()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    msg = ""
    if text in ["今天收入", "今日收入"]:
        qtype = "income"
        qdate = today.strftime("%Y-%m-%d")
        c.execute(
            "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? AND date=? ORDER BY id ASC",
            (str(user_id), qtype, qdate)
        )
        rows = c.fetchall()
        c.execute(
            "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND date=?", 
            (str(user_id), qtype, qdate)
        )
        total = c.fetchone()[0] or 0.0
        msg = f"今天收入明细：\n"
        for i, row in enumerate(rows, 1):
            msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
        msg += f"今天总收入：{total:.2f}"
    elif text in ["今天支出", "今日支出"]:
        qtype = "expense"
        qdate = today.strftime("%Y-%m-%d")
        c.execute(
            "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? AND date=? ORDER BY id ASC",
            (str(user_id), qtype, qdate)
        )
        rows = c.fetchall()
        c.execute(
            "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND date=?",
            (str(user_id), qtype, qdate)
        )
        total = c.fetchone()[0] or 0.0
        msg = f"今天支出明细：\n"
        for i, row in enumerate(rows, 1):
            msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
        msg += f"今天总支出：{total:.2f}"
    elif text in ["本月收入"]:
        qtype = "income"
        qmonth = today.strftime("%Y-%m")
        rows = c.execute(
            "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=? ORDER BY id ASC",
            (str(user_id), qtype, qmonth)
        )
        rows = c.fetchall()
        c.execute(
            "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=?",
            (str(user_id), qtype, qmonth)
        )
        total = c.fetchone()[0] or 0.0
        msg = f"本月收入明细：\n"
        for i, row in enumerate(rows, 1):
            msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
        msg += f"本月总收入：{total:.2f}"
    elif text in ["本月支出"]:
        qtype = "expense"
        qmonth = today.strftime("%Y-%m")
        rows = c.execute(
            "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=? ORDER BY id ASC",
            (str(user_id), qtype, qmonth)
        )
        rows = c.fetchall()
        c.execute(
            "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=?",
            (str(user_id), qtype, qmonth)
        )
        total = c.fetchone()[0] or 0.0
        msg = f"本月支出明细：\n"
        for i, row in enumerate(rows, 1):
            msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
        msg += f"本月总支出：{total:.2f}"
    elif text in ["上月收入"]:
        qtype = "income"
        last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        c.execute(
            "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=? ORDER BY id ASC",
            (str(user_id), qtype, last_month)
        )
        rows = c.fetchall()
        c.execute(
            "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=?",
            (str(user_id), qtype, last_month)
        )
        total = c.fetchone()[0] or 0.0
        msg = f"上月收入明细：\n"
        for i, row in enumerate(rows, 1):
            msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
        msg += f"上月总收入：{total:.2f}"
    elif text in ["上月支出"]:
        qtype = "expense"
        last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        rows = c.execute(
            "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=? ORDER BY id ASC",
            (str(user_id), qtype, last_month)
        )
        rows = c.fetchall()
        c.execute(
            "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=?",
            (str(user_id), qtype, last_month)
        )
        total = c.fetchone()[0] or 0.0
        msg = f"上月支出明细：\n"
        for i, row in enumerate(rows, 1):
            msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
        msg += f"上月总支出：{total:.2f}"
    conn.close()
    if msg:
        await update.message.reply_text(msg)
        return True
    return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    chat_type = update.message.chat.type
    state = user_state.get(user_id, WAITING)
    text = update.message.text.strip()

    # 私聊：管理员除“授权”外拥有所有权限，被授权人全部允许
    if chat_type == "private":
        if is_admin(user_id):
            if state == WAITING and text == "授权":
                return  # 管理员私聊禁止“授权”
            # 其它全部允许
        elif not is_authorized(user_id):
            return
        # 被授权人全部允许
    else:
        # 群组
        if is_admin(user_id):
            # 只在待命状态下限制只能输入“授权”
            if state == WAITING and text != "授权":
                return
        elif is_authorized(user_id):
            # 被授权人只在待命状态下禁止“授权”指令，其它全部允许
            if state == WAITING and text == "授权":
                return
            # 其它指令全部允许，继续往下走
        else:
            # 非授权人/非管理员无任何权限
            return
    set_timeout(user_id)  # 每次操作重置超时

    if username:
        users = load_users()
        at_name = f"@{username}"
        if at_name not in users:
            users[at_name] = str(user_id)
            save_users(users)
    text = update.message.text.strip()

    # 只允许指令和自然语言记账
    valid_cmds = ["账单", "报表", "清除", "查询", "返回", "授权", "收入", "帮助", "开始"]
    m_income = re.match(r"收入\s+([0-9]+(?:\.[0-9]+)?)(?:\s+(.+))?", text)
    m_expense = re.match(r"([+-][0-9]+(?:\.[0-9]+)?)\s+(.+)", text)
    is_valid_cmd = text in valid_cmds or m_income or m_expense

    if state == WAITING:
        if m_income:
            amount = float(m_income.group(1))
            desc = m_income.group(2) if m_income.group(2) else "未填写"
            today_str = date.today().strftime("%Y-%m-%d")
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute(
                "INSERT INTO bills (user_id, type, amount, category, description, date) VALUES (?, 'income', ?, ?, ?, ?)",
                (str(user_id), amount, "其他", desc, today_str)
            )
            conn.commit()
            conn.close()
            await reply_record_success(update, user_id, "income", amount, desc, today_str)
            reset_state(user_id)
            return
        elif m_expense:
            amount = float(m_expense.group(1))
            desc = m_expense.group(2)
            today_str = date.today().strftime("%Y-%m-%d")
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute(
                "INSERT INTO bills (user_id, type, amount, category, description, date) VALUES (?, 'expense', ?, ?, ?, ?)",
                (str(user_id), abs(amount), "其他", desc, today_str)
            )
            conn.commit()
            conn.close()
            await reply_record_success(update, user_id, "expense", abs(amount), desc, today_str)
            reset_state(user_id)
            return
        if text.isdigit():
            return
        query_info = parse_keyword_query(text)
        if query_info:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            params = [str(user_id)]
            sql = "SELECT amount, category, description, date FROM bills WHERE user_id=?"
            if query_info['type']:
                sql += " AND type=?"
                params.append(query_info['type'])
            if query_info['start_date'] and query_info['end_date']:
                sql += " AND date BETWEEN ? AND ?"
                params.append(query_info['start_date'].strftime('%Y-%m-%d'))
                params.append(query_info['end_date'].strftime('%Y-%m-%d'))
            if query_info['fuzzy_desc']:
                sql += " AND description LIKE ?"
                params.append(f"%{query_info['fuzzy_desc']}%")
            sql += " ORDER BY date ASC"
            c.execute(sql, tuple(params))
            rows = c.fetchall()
            c.execute(f"SELECT SUM(amount) FROM bills WHERE user_id=?" + (" AND type=?" if query_info['type'] else "") + (" AND date BETWEEN ? AND ?" if query_info['start_date'] and query_info['end_date'] else "") + (" AND description LIKE ?" if query_info['fuzzy_desc'] else ""), tuple(params))
            total = c.fetchone()[0] or 0.0
            conn.close()
            msg = ""
            if query_info['start_date'] and query_info['end_date']:
                msg += f"{query_info['start_date'].strftime('%Y-%m-%d')}至{query_info['end_date'].strftime('%Y-%m-%d')}"
            if query_info['type']:
                msg += f"{'收入' if query_info['type']=='income' else '支出'}"
            if query_info['fuzzy_desc']:
                msg += f"（{query_info['fuzzy_desc']}）"
            msg += f"总金额：{total:.2f}\n"
            if rows:
                msg += "明细：\n"
                for i, row in enumerate(rows, 1):
                    msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
            else:
                msg += "无明细记录。"
            await update.message.reply_text(msg)
            set_timeout(user_id)
            return
        if await quick_keyword_query(update, user_id, text):
            set_timeout(user_id)
            return
        if text == "返回":
            await return_cmd(update, context)
            return
        if text == "开始":
            await show_menu(update)
            reset_state(user_id)
            return
        if text == "帮助":
            await help_cmd(update, context)
            return
        elif text == "账单":
            await bill_cmd(update, context)
            return
        elif text == "报表":
            await report_cmd(update, context)
            return
        elif text == "清除":
            await clear_cmd(update, context)
            return
        elif text == "查询":
            await query_cmd(update, context)
            return
        elif text == "授权":
            await auth_cmd(update, context)
            return
        elif text == "收入" or text == "支出":
            await income_cmd(update, context)
            return
        # 直接记账或自然语言
        if not await handle_record(update, context):
            if not await try_natural_language_record(update, context):
                return
        return
    elif state == BILL_TYPE:
        await handle_bill_type(update, context)
        set_timeout(user_id)
    elif state == BILL_MONTH:
        await handle_bill_month(update, context)
        set_timeout(user_id)
    elif state == REPORT_MONTH:
        await handle_report_month(update, context)
        set_timeout(user_id)
    elif state == CLEAR_TYPE:
        await handle_clear_type(update, context)
        set_timeout(user_id)
    elif state == QUERY_TYPE:
        await handle_query_type(update, context)
        set_timeout(user_id)
    elif state == QUERY_DATE:
        await handle_query_date(update, context)
        set_timeout(user_id)
    elif state == AUTH_TYPE:
        await handle_auth_type(update, context)
        set_timeout(user_id)
    elif state == AUTH_USER:
        await handle_auth_user(update, context)
        set_timeout(user_id)
    elif state == UNAUTH_USER:
        await handle_unauth_user(update, context)
        set_timeout(user_id)
    elif state == INCOME_MONTH:
        await handle_income_month(update, context)
        set_timeout(user_id)
    elif state == PENDING_NL_RECORD:
        await handle_nl_record_confirm(update, context)
        # 不再 set_timeout，让 handle_nl_record_confirm 自己控制
        return
    else:
        reset_state(user_id)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin_or_authorized(user_id):
        return
    await show_menu(update)
    reset_state(user_id)

def main():
    application = Application.builder().token(TOKEN).build()
    # 授权类型选择（1/2）
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^[12]$"), handle_auth_type))
    # 授权天数输入处理（正则允许前后空格和中文一二，注册顺序在 handle_auth_type 之后）
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^\s*(\d{1,3}|[一二])\s*$"), handle_auth_days))
    application.add_handler(CommandHandler("start", start))
    # 先注册所有文本命令，保证优先匹配
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^(添加错别字|删除错别字|查看错别字)"), typo_cmd))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^(添加分类|删除分类|查看分类)"), category_cmd))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^撤销$"), undo_cmd))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^常用描述$"), suggest_cmd))
    # 支持“本月统计”“上月统计”“\d+月统计”“去年\d+月统计”等
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^(本月统计|上月统计|[12]?\d{1,3}月统计|去年\d{1,2}月统计)$"), month_stat_cmd))
    # 最后注册通用文本处理
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == "__main__":
    main()

# 代码结束
