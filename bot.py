import json
import os

def load_typo_dict():
    if not os.path.exists('typo_dict.json'):
        return {}
    with open('typo_dict.json', "r", encoding="utf-8") as f:
        return json.load(f)

def save_typo_dict(typo_dict):
    with open('typo_dict.json', "w", encoding="utf-8") as f:
        json.dump(typo_dict, f, ensure_ascii=False, indent=2)
import threading
import json
import os
import sqlite3
import re
from datetime import date, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import logging

# --- 路径常量提前，确保所有初始化前可用 ---
CONFIG_PATH = "config.json"
DB_PATH = "data.db"
USERS_PATH = "users.json"
TYPO_DICT_PATH = "typo_dict.json"

import copy

# --- 配置文件操作 ---
def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"admins": [], "authorized": {}, "auth_expire": {}}, f, ensure_ascii=False, indent=2)
        return {"admins": [], "authorized": {}, "auth_expire": {}}
    else:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 兼容旧结构
            if "authorized" in data and isinstance(data["authorized"], list):
                data["authorized"] = {}
            if "auth_expire" not in data:
                data["auth_expire"] = {}
            return data

def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

import time

# --- 分类映射操作 ---
def load_category_map():
    if not os.path.exists("category_map.json"):
        with open("category_map.json", "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)
        return {}
    else:
        with open("category_map.json", "r", encoding="utf-8") as f:
            return json.load(f)

def save_category_map(category_map):
    with open("category_map.json", "w", encoding="utf-8") as f:
        json.dump(category_map, f, ensure_ascii=False, indent=2)

config = load_config()
category_map = load_category_map()

# 全局变量替换为 user_session
user_timer = {}
TIMEOUT_SECONDS = 5

def reset_state(user_id):
    user_session.reset(user_id)

def set_timeout(user_id):
    help_text = (
        "当前机器人支持的主要指令如下：\n"
        "\n"
        "记账指令（严格格式）\n"
        "●收入 金额 描述\n"
        "●支出 金额 描述\n"
        "●+金额 描述\n"
        "●-金额 描述\n"
        "示例：\n"
        "\n"
        "查询与统计指令（关键词查询）\n"
        "●今天收入\n"
        "●今天支出\n"
        "●本月收入\n"
        "●本月支出\n"
        "●7月、去年6月、去年、2024年 等灵活年月表达\n"
        "●账单\n"
        "●报表\n"
        "●查询\n"
        "\n"
        "管理与辅助指令\n"
        "●添加分类 描述=分类\n"
        "●删除分类 描述\n"
        "●查看分类\n"
        "●添加错别字 错别词=正确词\n"
        "●删除错别字 错别词\n"
        "●查看错别字\n"
        "●撤销\n"
        "●常用描述\n"
        "●清除\n"
        "●返回\n"
        "●开始\n"
        "●帮助\n"
        "●授权（仅管理员群组内使用）\n"
        "呈现后机器人返回待命状态"
    )
async def typo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat = update.effective_chat
    # 只有管理员可以私聊
    if chat.type == "private" and not is_admin(user_id):
        return
    # 群组内管理员仅能授权
    if chat.type in ["group", "supergroup"]:
        if is_admin(user_id) and not text.startswith("授权"):
            return
        if not (is_admin(user_id) or is_authorized(user_id, chat.id)):
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
    # 只有管理员可以私聊
    if chat.type == "private" and not is_admin(user_id):
        return
    if chat.type in ["group", "supergroup"]:
        if is_admin(user_id):
            return
        if not is_authorized(user_id, chat.id):
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
    # 只有管理员可以私聊
    if chat.type == "private" and not is_admin(user_id):
        return
    if chat.type in ["group", "supergroup"]:
        if is_admin(user_id):
            return
        if not is_authorized(user_id, chat.id):
            return
    if not is_admin_or_authorized(user_id, chat.id):
        return
    global user_common_desc
    descs = sorted(user_common_desc.get(user_id, {}).items(), key=lambda x: -x[1])[:5]
    if descs:
        await update.message.reply_text("常用描述：" + ", ".join([d for d,c in descs]))
    else:
        await update.message.reply_text("暂无常用描述。")

# 本月统计命令
async def month_stat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 所有用户均可使用“开始”菜单，无需权限判断
    text = update.message.text.strip()
    # 智能解析时间
    import re
    from datetime import datetime
    today = date.today()
    # 默认本月
    year = today.year
    menu = (
        "欢迎使用记账机器人！\n"
        "\n"
        "可用指令：\n"
        "●输入“收入 金额”或“收入 金额 描述”为收入；\n"
        "\n"
        "● 输入“+或-金额 描述”为支出；\n"
        "\n"
        "●输入“账单”可按月份查询明细；\n"
        "\n"
        "●输入“报表”可按月份查询分类汇总；\n"
        "\n"
        "输入“查询”可查：\n"
        "● “昨天”、“前天”、“今天”、“本月”、“上月”、“今年”；\n"
        "\n"
        "●“去年6月”、“去年3月”、“2024年5月”；\n"
        "\n"
        "●“2025年6月1至2025年7月31”（区间）；\n"
        "\n"
        "●“今天收入”、“本月支出”、“上月收入”\n"
        "\n"
        "如需帮助请回复“帮助”。"
    )
    month = today.month
    # 支持“上月统计”“3月统计”“去年1月统计”等
    m1 = re.match(r"^(去年|[12]?\d{3,4}年)?(\d{1,2})?月?统计$", text)
    m2 = re.match(r"^上月统计$", text)
    m_year = re.match(r"^(今年|去年|[12]?\d{3,4}年)统计$", text)
    # 菜单展示函数只需展示menu，无需统计分支，移除无意义if分支
    await update.message.reply_text(menu)
    return
    # ...菜单展示已完成，移除所有统计分支残留...
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 分类统计支出
    c.execute(f"SELECT category, SUM(amount) FROM bills WHERE user_id=? AND type='expense' AND {where} GROUP BY category", (str(user_id),))
    expense_rows = c.fetchall()
    # 分类统计收入
    c.execute(f"SELECT category, SUM(amount) FROM bills WHERE user_id=? AND type='income' AND {where} GROUP BY category", (str(user_id),))
    income_rows = c.fetchall()
    # 总支出
    c.execute(f"SELECT SUM(amount) FROM bills WHERE user_id=? AND type='expense' AND {where}", (str(user_id),))
    total_expense = c.fetchone()[0] or 0.0
    # 总收入
    c.execute(f"SELECT SUM(amount) FROM bills WHERE user_id=? AND type='income' AND {where}", (str(user_id),))
    total_income = c.fetchone()[0] or 0.0
    conn.close()
    msg = f"{label}\n"
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
    # 只有管理员可以私聊
    if chat.type == "private" and not is_admin(user_id):
        return
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


# --- 数据库操作封装 ---
import json
import os

def load_typo_dict():
    if not os.path.exists(TYPO_DICT_PATH):
        return {}
    with open(TYPO_DICT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_typo_dict(typo_dict):
    with open(TYPO_DICT_PATH, "w", encoding="utf-8") as f:
        json.dump(typo_dict, f, ensure_ascii=False, indent=2)
class BillDB:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
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
    def insert_bill(self, user_id, type_, amount, category, desc, date):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "INSERT INTO bills (user_id, type, amount, category, description, date) VALUES (?, ?, ?, ?, ?, ?)",
            (str(user_id), type_, amount, category, desc, date)
        )
        bill_id = c.lastrowid
        conn.commit()
        conn.close()
        return bill_id
    def fetch_bills(self, user_id, type_=None, date=None, month=None):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        sql = "SELECT amount, category, description, date FROM bills WHERE user_id=?"
        params = [str(user_id)]
        if type_:
            sql += " AND type=?"
            params.append(type_)
        if date:
            sql += " AND date=?"
            params.append(date)
        if month:
            sql += " AND strftime('%Y-%m', date)=?"
            params.append(month)
        sql += " ORDER BY id ASC"
        c.execute(sql, tuple(params))
        rows = c.fetchall()
        conn.close()
        return rows
    def sum_bills(self, user_id, type_, date=None, month=None):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        sql = "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=?"
        params = [str(user_id), type_]
        if date:
            sql += " AND date=?"
            params.append(date)
        if month:
            sql += " AND strftime('%Y-%m', date)=?"
            params.append(month)
        c.execute(sql, tuple(params))
        total = c.fetchone()[0] or 0.0
        conn.close()
        return total
    def delete_bills(self, user_id, date=None):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        if date:
            c.execute("DELETE FROM bills WHERE user_id=? AND date=?", (str(user_id), date))
        else:
            c.execute("DELETE FROM bills WHERE user_id=?", (str(user_id),))
        conn.commit()
        conn.close()

bill_db = BillDB(DB_PATH)

# --- 用户状态管理封装 ---
def load_users():
    if not os.path.exists(USERS_PATH):
        return {}
    with open(USERS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
class UserSession:
    def __init__(self):
        self.state = {}
        self.temp = {}
        self.owner = {}
        self.last_record = {}
        self.common_desc = {}
        self.last_bill_id = {}
    def get_state(self, user_id):
        return self.state.get(user_id, WAITING)
    def set_state(self, user_id, value):
        self.state[user_id] = value
    def get_temp(self, user_id):
        return self.temp.get(user_id, {})
    def set_temp(self, user_id, value):
        self.temp[user_id] = value
    def get_owner(self, user_id):
        return self.owner.get(user_id)
    def set_owner(self, user_id, value):
        self.owner[user_id] = value
    def reset(self, user_id):
        self.state[user_id] = WAITING
        self.temp[user_id] = {}
        if user_id in self.owner:
            del self.owner[user_id]
        if user_id in user_timer:
            user_timer[user_id].cancel()
            del user_timer[user_id]
    def set_last_record(self, user_id, rec):
        self.last_record[user_id] = rec
    def get_last_record(self, user_id):
        return self.last_record.get(user_id)
    def set_common_desc(self, user_id, desc):
        if user_id not in self.common_desc:
            self.common_desc[user_id] = {}
        self.common_desc[user_id][desc] = self.common_desc[user_id].get(desc, 0) + 1
    def get_common_desc(self, user_id):
        return self.common_desc.get(user_id, {})
    def set_last_bill_id(self, user_id, bill_id):
        self.last_bill_id[user_id] = bill_id
    def get_last_bill_id(self, user_id):
        return self.last_bill_id.get(user_id)

user_session = UserSession()

TOKEN = "7536100847:AAHslrzRe8eo9NmquNBSaYwSg0cgBU28GyM"

def is_admin_or_authorized(user_id, chat_id):
    uid = str(user_id)
    chat_id = str(chat_id)
    if uid in config["admins"]:
        return True
    return chat_id in config["authorized"] and uid in config["authorized"][chat_id]

def is_admin(user_id):
    return str(user_id) in config["admins"]

def is_authorized(user_id, chat_id):
    uid = str(user_id)
    chat_id = str(chat_id)
    if chat_id in config["authorized"] and uid in config["authorized"][chat_id]:
        key = f"{chat_id}:{uid}"
        if "auth_expire" in config and key in config["auth_expire"]:
            if time.time() < config["auth_expire"][key]:
                return True
            else:
                config["authorized"][chat_id].remove(uid)
                del config["auth_expire"][key]
                save_config(config)
                return False
        else:
            config["authorized"][chat_id].remove(uid)
            save_config(config)
            return False
    return False

user_state = {}
user_temp = {}
user_timeouts = {}
user_owner = {}  # 记录每个用户当前状态的 owner
# 增加超时自动返回待命状态功能
user_timer = {}

# 授权到期提醒（每天检查一次）
def remind_auth_expire():
    now = time.time()
    for chat_id in config.get("authorized", {}):
        for uid in config["authorized"][chat_id]:
            key = f"{chat_id}:{uid}"
            expire = config.get("auth_expire", {}).get(key)
            if expire and expire - now < 24*3600 and expire - now > 0:
                # 到期前一天提醒管理员
                # 这里假设有 send_admin_message(chat_id, msg) 工具函数
                msg = f"⚠️ 用户 {uid} 在群 {chat_id} 的授权将于 {time.strftime('%Y-%m-%d %H:%M', time.localtime(expire))} 到期，请及时处理。"
                send_admin_message(chat_id, msg)
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
        "欢迎使用记账机器人！\n"
        "\n"
        "可用指令：\n"
        "●输入“收入 金额”或“收入 金额 描述”为收入；\n"
        "\n"
        "● 输入“+或-金额 描述”为支出；\n"
        "\n"
        "●输入“账单”可按月份查询明细；\n"
        "\n"
        "●输入“报表”可按月份查询分类汇总；\n"
        "\n"
        "输入“查询”可查：\n"
        "● “昨天”、“前天”、“今天”、“本月”、“上月”、“今年”；\n"
        "\n"
        "●“去年6月”、“去年3月”、“2024年5月”；\n"
        "\n"
        "●“2025年6月1至2025年7月31”（区间）；\n"
        "\n"
        "●“今天收入”、“本月支出”、“上月收入”\n"
        "\n"
        "如需帮助请回复“帮助”。"
    )
    await update.message.reply_text(menu)
    reset_state(update.effective_user.id)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 所有用户均可使用“帮助”指令，无需权限判断
    menu = (
        "当前机器人支持的主要指令如下：\n"
        "\n"
        "记账指令（严格格式）\n"
        "●收入 金额 描述\n"
        "●支出 金额 描述\n"
        "●+金额 描述\n"
        "●-金额 描述\n"
        "示例：\n"
        "\n"
        "查询与统计指令（关键词查询）\n"
        "●今天收入\n"
        "●今天支出\n"
        "●本月收入\n"
        "●本月支出\n"
        "●7月、去年6月、去年、2024年 等灵活年月表达\n"
        "●账单\n"
        "●报表\n"
        "●查询\n"
        "\n"
        "管理与辅助指令\n"
        "●添加分类 描述=分类\n"
        "●删除分类 描述\n"
        "●查看分类\n"
        "●撤销\n"
        "●清除\n"
        "●返回\n"
        "●开始\n"
        "●帮助\n"
        "●授权（仅管理员群组内使用）\n"
    )
    await update.message.reply_text(menu)
    reset_state(update.effective_user.id)
    user_id = update.effective_user.id
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
    if not is_admin_or_authorized(user_id, update.effective_chat.id):
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
    if not is_admin_or_authorized(user_id, update.effective_chat.id):
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
    if not is_admin_or_authorized(user_id, update.effective_chat.id):
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
        await update.message.reply_text(
            "请输入查询日期：\n支持格式如：\n- 2025-6-1（单日）\n- 2025-6-1至2025-7-31（区间）\n- 昨天、前天、今天、本月、上月、今年、去年\n- 6月、去年3月、2024年5月"
        )
        user_state[user_id] = QUERY_DATE
        set_timeout(user_id)
    elif text == "2":
        user_temp[user_id]["query_type"] = "expense"
        await update.message.reply_text(
            "请输入查询日期：\n支持格式如：\n- 2025-6-1（单日）\n- 2025-6-1至2025-7-31（区间）\n- 昨天、前天、今天、本月、上月、今年、去年\n- 6月、去年3月、2024年5月"
        )
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
        m3 = re.match(r'^({4})[年-]({1,2})[月]$', s)
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
            y, m = date.today().year, int(m4.group(1))
            if is_start:
                return f"{y:04d}-{m:02d}-01"
            else:
                if m == 12:
                    next_month = date(y+1, 1, 1)
                else:
                    next_month = date(y, m+1, 1)
                last_day = (next_month - timedelta(days=1)).day
                return f"{y:04d}-{m:02d}-{last_day:02d}"
        # 默认返回当天
        return date.today().strftime('%Y-%m-%d')

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
    if not is_admin_or_authorized(user_id, update.effective_chat.id):
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
        await update.message.reply_text("请输入被授权人用户名和天数：（比如@***** 3）输入3代表授权3天，输入其他数字代表授权授权天数")
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
    chat = update.effective_chat
    chat_id = str(chat.id)
    if user_state.get(user_id, WAITING) != AUTH_USER:
        return
    text = update.message.text.strip()
    parts = text.split()
    if len(parts) != 2 or not parts[0].startswith("@"):
        await update.message.reply_text("输入格式有误，请输入：@用户名 天数，例如@user 3")
        reset_state(user_id)
        return
    username = parts[0]
    try:
        days = int(parts[1])
        if days <= 0 or days > 365:
            raise ValueError()
    except Exception:
        await update.message.reply_text("请输入正确的天数（1-365）！")
        reset_state(user_id)
        return
    users = load_users()
    if username in users:
        uid = str(users[username])
        key = f"{chat_id}:{uid}"
        now = time.time()
        expire = config.get("auth_expire", {}).get(key)
        if expire and expire > now:
            left_days = int((expire - now) // (24*3600)) + 1
            expire_date = time.strftime('%Y-%m-%d %H:%M', time.localtime(expire))
            await update.message.reply_text(
                f"⚠️ 用户 {username} 已授权，剩余 {left_days} 天。\n"
                f"⏰ 到期时间：{expire_date}\n"
                f"如需延长请重新授权。"
            )
        else:
            if chat_id not in config["authorized"]:
                config["authorized"][chat_id] = []
            if uid not in config["authorized"][chat_id]:
                config["authorized"][chat_id].append(uid)
            if "auth_expire" not in config:
                config["auth_expire"] = {}
            config["auth_expire"][key] = now + days*24*3600
            save_config(config)
            expire_date = time.strftime('%Y-%m-%d %H:%M', time.localtime(now + days*24*3600))
            await update.message.reply_text(
                f"✅ 用户 {username} 已成功授权 {days} 天！\n"
                f"⏰ 到期时间：{expire_date}\n"
                f"⚠️ 到期后需重新授权。"
            )
    else:
        await update.message.reply_text("群组内无该用户。")
    reset_state(user_id)

# 新增处理授权天数的状态
    # 已合并到 handle_auth_user，一步输入 @用户名 天数
    pass

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
    if not is_admin_or_authorized(user_id, update.effective_chat.id):
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
    today = date.today().strftime("%Y-%m-%d")
    # 最近5笔当天支出/收入
    c.execute(
        "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? AND date=? ORDER BY id ASC",
        (str(user_id), record_type, today)
    )
    today_rows = c.fetchall()
    rows = today_rows[-5:]
    today_count = len(today_rows)
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
    def fmt_amt(val):
        return f"{val:.2f}"

    msg = f"记录成功：{fmt_amt(amount)}，{desc}\n\n最近5笔{'收入' if record_type=='income' else '支出'}（今天{today_count}笔）:\n"
    start_num = today_count - len(rows) + 1
    for i, row in enumerate(rows, start_num):
        amt_str = fmt_amt(row[0])
        msg += f"{i}| {amt_str} | {row[2]} |\n"
    if record_type == 'expense':
        msg += f"\n当天累计支出：{day_total:.2f}\n本月累计支出：{month_total:.2f}"
    else:
        msg += f"\n当天累计收入：{day_total:.2f}\n本月累计收入：{month_total:.2f}"
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

    # 1. 支持“昨天”“前天”
    import re as _re
    _today = date.today()
    if text.startswith("昨天"):
        record_date = _today - timedelta(days=1)
        text = text.replace("昨天", "", 1).strip()
    elif text.startswith("前天"):
        record_date = _today - timedelta(days=2)
        text = text.replace("前天", "", 1).strip()
    else:
        record_date = None
    # 2. 支持“2025-6-3 购物 200”/“2025/6/3 购物 200”
    m_full = _re.match(r"^([12][0-9]{3})[年/-]([0-9]{1,2})[月/-]([0-9]{1,2})[日号]?\s*(.+?)\s*([0-9]+(?:\.[0-9]+)?)$", text)
    if m_full:
        year = int(m_full.group(1))
        month = int(m_full.group(2))
        day = int(m_full.group(3))
        desc = m_full.group(4).strip()
        amount = float(m_full.group(5))
        record_date = date(year, month, day)
        if '卖' in desc:
            rtype = 'income'
        elif '买' in desc:
            rtype = 'expense'
        else:
            rtype = 'expense'
        return {
            "type": rtype,
            "amount": amount,
            "category": get_category(desc),
            "description": desc,
            "date": record_date.strftime('%Y-%m-%d')
        }
    # 3. 支持“6月3日 购物 200”“6/3 购物 200”“6-3 购物 200”
    m_md = _re.match(r"^([0-9]{1,2})[月/-]([0-9]{1,2})[日号]?\s*(.+?)\s*([0-9]+(?:\.[0-9]+)?)$", text)
    if m_md:
        month = int(m_md.group(1))
        day = int(m_md.group(2))
        desc = m_md.group(3).strip()
        amount = float(m_md.group(4))
        year = _today.year
        if month > _today.month:
            year -= 1
        record_date = date(year, month, day)
        if '卖' in desc:
            rtype = 'income'
        elif '买' in desc:
            rtype = 'expense'
        else:
            rtype = 'expense'
        return {
            "type": rtype,
            "amount": amount,
            "category": get_category(desc),
            "description": desc,
            "date": record_date.strftime('%Y-%m-%d')
        }
    # 4. 支持“昨天 购物 200”“前天 购物 200”
    if record_date is not None:
        m = _re.match(r"(.+?)\s*([0-9]+(?:\.[0-9]+)?)$", text)
        if m:
            desc = m.group(1).strip()
            amount = float(m.group(2))
            if '卖' in desc:
                rtype = 'income'
            elif '买' in desc:
                rtype = 'expense'
            else:
                rtype = 'expense'
            return {
                "type": rtype,
                "amount": amount,
                "category": get_category(desc),
                "description": desc,
                "date": record_date.strftime('%Y-%m-%d')
            }
    # 解析“描述 金额”或“金额 描述”
    m1 = re.match(r"(.+?)\s*([+-]?[0-9]+(?:\.[0-9]+)?)$", text)
    m2 = re.match(r"^([+-]?[0-9]+(?:\.[0-9]+)?)\s*(.+)$", text)
    today_str = date.today().strftime("%Y-%m-%d")
    if m1:
        desc = m1.group(1).strip()
        amount = float(m1.group(2))
        if '卖' in desc:
            rtype = 'income'
        elif '买' in desc:
            rtype = 'expense'
        else:
            rtype = 'expense'
        return {
            "type": rtype,
            "amount": amount,
            "category": get_category(desc),
            "description": desc,
            "date": today_str
        }
    if m2:
        amount = float(m2.group(1))
        desc = m2.group(2).strip()
        if '卖' in desc:
            rtype = 'income'
        elif '买' in desc:
            rtype = 'expense'
        else:
            rtype = 'expense'
        return {
            "type": rtype,
            "amount": amount,
            "category": get_category(desc),
            "description": desc,
            "date": today_str
        }
    return None
# 根据描述内容自动分类
def get_category(desc):
    # 优先查用户自定义分类
    global category_map
    for k, v in category_map.items():
        if k in desc:
            return v
    # 自动学习：如果历史上该描述多次归为同一类，则自动记忆
    import sqlite3
    import difflib
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT category, COUNT(*) FROM bills WHERE description=? GROUP BY category ORDER BY COUNT(*) DESC", (desc,))
    row = c.fetchone()
    conn.close()
    if row and row[1] >= 2:
        return row[0]
    # 标准化常见描述自动归类，补充更多关键词
    category_keywords = {
        '餐饮': ['买菜', 'maicai', 'mai cai', '午餐', '晚餐', '早餐', '饭', '餐饮', '外卖', '聚餐', '饮料', '奶茶', '水果', '零食', '面包', '牛奶', '火锅', '烧烤', '甜品', '蛋糕', '小吃', '自助', '酒', '啤酒', '咖啡', '茶'],
        '生活': ['水电', 'shuifei', 'dianfei', '水费', '电费', '燃气', '物业', '生活', '日用品', '洗衣', '家政', '清洁', '垃圾', '纸巾', '洗发水', '沐浴露', '牙膏', '牙刷', '洗手液', '消毒', '修理', '搬家', '快递', '邮费'],
        '交通': ['公交', '地铁', '打车', '滴滴', '加油', '停车', '交通', '高铁', '火车', '飞机', '机票', 'jiaotong', '出租', '共享单车', '摩托', '汽车', '车票', '高速', 'ETC', '驾照', '违章'],
        '娱乐': ['电影', 'KTV', '游戏', '娱乐', '旅游', '门票', '聚会', 'yule', '唱歌', '演出', '展览', '游乐园', '剧本杀', '桌游', '漫画', '小说', '视频会员', '音乐', '直播', '打赏'],
        '学习': ['学费', '培训', '书', '教材', '学习', '考试', '课程', 'xuexi', '讲座', '网课', '辅导', '证书', '报名费', '论文', '资料', '文具', '图书馆'],
        '医疗': ['医院', '药', '医疗', '体检', '挂号', 'yiliao', '诊所', '疫苗', '医保', '健康', '理疗', '牙科', '眼科', '药店', '处方', '手术', '住院', '护理', '康复'],
        '通讯': ['话费', '流量', '宽带', '通讯', '手机', 'huafei', 'tongxun', '网络', 'SIM卡', '路由器', '电话', '短信', '固话'],
        '住房': ['房租', '租金', 'fangzu', '水费', '电费', '物业', '燃气', '房贷', '买房', '装修', '家居', '中介费', '押金', '租房', '购房', '房产税'],
        '购物': ['购物', '网购', '京东', '淘宝', '拼多多', '苏宁', 'gouwu', 'jingdong', 'taobao', 'pinduoduo', 'suining', '超市', '衣服', '鞋', '化妆品', '美妆', '数码', '手机', '电脑', '家电', '家居', '家纺', '厨具', '母婴', '宠物', '箱包', '饰品', '玩具', '运动服', '运动鞋', '眼镜', '手表', '家装', '灯具', '窗帘', '床上用品'],
        '工资': ['工资', '薪水', '奖金', 'gongzi', 'jiangjin', '兼职', '转账', '津贴', '补贴', '报酬', '劳务', '薪酬', '年终奖', '提成'],
        '收入': ['红包', '转账', '奖金', '工资', '兼职', '理财收益', '投资收益', '分红', '股息', '报销', '退款', '返现', '奖励', '补助', '津贴', '补贴'],
        '母婴': ['母婴', '奶粉', '尿不湿', '婴儿', '宝宝', '孕妇', '儿童', '玩具', '童装', '早教', '辅食', '婴儿车', '婴儿床', '孕检', '产检'],
        '宠物': ['宠物', '猫', '狗', '宠物粮', '宠物用品', '疫苗', '宠物医院', '猫砂', '狗粮', '宠物美容', '宠物洗澡', '宠物玩具'],
        '保险': ['保险', '保费', '车险', '健康险', '意外险', '寿险', '医保', '商业险', '保险理赔', '保险产品', '保险公司'],
        '投资': ['投资', '理财', '基金', '股票', '证券', '债券', '黄金', '分红', '股息', '收益', '定投', '买入', '卖出', '开户', '理财产品', '投资账户'],
        '教育': ['教育', '学费', '培训', '课程', '教材', '考试', '讲座', '网课', '辅导', '证书', '报名费', '论文', '资料', '文具', '图书馆', '留学', '学杂费'],
        '健康': ['健康', '健身', '运动', '理疗', '体检', '医疗', '瑜伽', '跑步', '健身房', '按摩', '营养', '健康管理', '体脂', '体重', '健康咨询'],
        '运动': ['运动', '健身', '瑜伽', '跑步', '游泳', '球类', '健身房', '器材', '运动鞋', '运动服', '羽毛球', '篮球', '足球', '乒乓球', '网球', '健身卡', '运动会员'],
        '数码': ['数码', '手机', '电脑', '平板', '耳机', '相机', '配件', '智能设备', '充电器', 'U盘', '硬盘', '鼠标', '键盘', '显示器', '路由器', '智能手表'],
        '家居': ['家居', '家具', '家纺', '厨具', '装修', '家电', '装饰', '收纳', '灯具', '窗帘', '床上用品', '地毯', '餐具', '清洁用品', '家政服务'],
        '美妆': ['美妆', '化妆品', '护肤', '口红', '面膜', '香水', '美容', '美发', '美甲', '洗面奶', '爽肤水', '乳液', '粉底', '睫毛膏', '眼影', '腮红', '防晒'],
        '旅游': ['旅游', '机票', '酒店', '门票', '景点', '旅行', '民宿', '跟团游', '自由行', '签证', '导游', '租车', '行程', '旅游保险'],
        '捐赠': ['捐赠', '慈善', '公益', '捐款', '救助', '志愿者', '善款', '捐物'],
        '礼品': ['礼品', '礼物', '赠送', '收礼', '送礼', '礼金', '礼盒', '贺卡', '纪念品'],
        '维修': ['维修', '修理', '保养', '维护', '修车', '修家电', '修手机', '修电脑', '修家具', '修水管', '修电路'],
        '其他': []
    }
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
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    global user_last_record
    if not is_admin_or_authorized(user_id, chat_id):
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
    global user_last_record, user_common_desc, user_last_bill_id, user_state, user_owner, user_temp
    import logging
    logger = logging.getLogger("nl_record_trace")
    user_id = update.effective_user.id
    logger.warning(f"[CONFIRM] user_id={user_id} state={user_state.get(user_id)} owner={user_owner.get(user_id)} text={getattr(update.message, 'text', None)}")
    # 只允许 owner 继续操作
    if user_state.get(user_id) != PENDING_NL_RECORD or user_owner.get(user_id) != user_id:
        logger.warning(f"[CONFIRM] 拒绝: user_id={user_id} state={user_state.get(user_id)} owner={user_owner.get(user_id)}")
        return
    text = update.message.text.strip()
    if text == "1":
        rec = user_temp.get(user_id, {}).get("nl_record")
        if not rec:
            logger.warning(f"[CONFIRM] nl_record丢失: user_id={user_id} user_temp={user_temp.get(user_id)}")
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
        await update.message.reply_text("您已取消记账。机器人已返回待命状态。")
        reset_state(user_id)
        return
    await update.message.reply_text("您的输入有误。（机器人立刻返回待命状态）")
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
        m3 = re.match(r'^({4})[年-]({1,2})[月]$', s)
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
            y, m = date.today().year, int(m4.group(1))
            if is_start:
                return f"{y:04d}-{m:02d}-01"
            else:
                if m == 12:
                    next_month = date(y+1, 1, 1)
                else:
                    next_month = date(y, m+1, 1)
                last_day = (next_month - timedelta(days=1)).day
                return f"{y:04d}-{m:02d}-{last_day:02d}"
        # 默认返回当天
        return date.today().strftime('%Y-%m-%d')
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
    chat_id = update.effective_chat.id
    if not is_admin_or_authorized(user_id, chat_id):
        return False
    if username:
        users = load_users()
        at_name = f"@{username}"
        if at_name not in users:
            users[at_name] = str(user_id)
            save_users(users)
    text = update.message.text.strip()
    # 仅支持如下两种格式自动记账，其它全部忽略：
    # 1. 收入 金额 或 收入 金额 描述
    m = re.match(r"^收入\s+([+-]?[0-9]+(?:\.[0-9]+)?)(?:\s+(.+))?$", text)
    if m:
        amount = float(m.group(1))
        desc = m.group(2)
        if not desc or not desc.strip():
            await update.message.reply_text("请输入描述，格式：收入 金额 描述。机器人已返回待命状态。")
            reset_state(user_id)
            return True
        today = date.today().strftime("%Y-%m-%d")
        bill_db.insert_bill(user_id, 'income', amount, '其他', desc.strip(), today)
        await reply_record_success(update, user_id, "income", amount, desc.strip(), today)
        reset_state(user_id)
        return True
    # 2. +金额 描述 或 -金额 描述（支出）
    m = re.match(r"^([+-]\d+(?:\.\d+)?)\s+(.+)$", text)
    if m:
        amount = float(m.group(1))
        desc = m.group(2)
        today_str = date.today().strftime("%Y-%m-%d")
        type_ = "expense"
        bill_db.insert_bill(user_id, type_, amount, '其他', desc, today_str)
        await reply_record_success(update, user_id, type_, amount, desc, today_str)
        reset_state(user_id)
        return True
    # 其它任何描述都不认为是收入/支出指令
    return False

async def quick_keyword_query(update, user_id, text):
    today = date.today()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    msg = ""
    import re as _re
    # 7月/8月/12月等，自动查询今年该月，呈现所有明细和汇总
    m = _re.match(r"^(\d{1,2})月(收入|支出)?$", text)
    if m:
        month = int(m.group(1))
        qtype = None
        if m.group(2) == "收入":
            qtype = "income"
        elif m.group(2) == "支出":
            qtype = "expense"
        year = date.today().year
        qmonth = f"{year}-{month:02d}"
        msg = ""
        if qtype:
            c.execute(
                "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=? ORDER BY id ASC",
                (str(user_id), qtype, qmonth)
            )
            rows = c.fetchall()
            c.execute(
                "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=?",
                (str(user_id), qtype, qmonth)
            )
            total = c.fetchone()[0] or 0.0
            msg += f"{year}年{month}月{qtype}明细：\n"
            for i, row in enumerate(rows, 1):
                msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
            msg += f"{year}年{month}月总{qtype}：{total:.2f}"
        else:
            # 收入
            c.execute(
                "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type='income' AND strftime('%Y-%m', date)=? ORDER BY id ASC",
                (str(user_id), qmonth)
            )
            rows = c.fetchall()
            c.execute(
                "SELECT SUM(amount) FROM bills WHERE user_id=? AND type='income' AND strftime('%Y-%m', date)=?",
                (str(user_id), qmonth)
            )
            total_income = c.fetchone()[0] or 0.0
            msg += f"{year}年{month}月收入明细：\n"
            for i, row in enumerate(rows, 1):
                msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
            msg += f"{year}年{month}月总收入：{total_income:.2f}\n"
            # 支出
            c.execute(
                "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type='expense' AND strftime('%Y-%m', date)=? ORDER BY id ASC",
                (str(user_id), qmonth)
            )
            rows = c.fetchall()
            c.execute(
                "SELECT SUM(amount) FROM bills WHERE user_id=? AND type='expense' AND strftime('%Y-%m', date)=?",
                (str(user_id), qmonth)
            )
            total_expense = c.fetchone()[0] or 0.0
            msg += f"{year}年{month}月支出明细：\n"
            for i, row in enumerate(rows, 1):
                msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
            msg += f"{year}年{month}月总支出：{total_expense:.2f}"
        conn.close()
        if msg:
            await update.message.reply_text(msg)
            return True
        return False

    # 去年/去年X月等，或任意超过31天的区间，只呈现分类汇总
    m = _re.match(r"^去年(\d{1,2})?月?(收入|支出)?$", text)
    if m:
        year = date.today().year - 1
        month = m.group(1)
        qtype = None
        if m.group(2) == "收入":
            qtype = "income"
        elif m.group(2) == "支出":
            qtype = "expense"
        msg = ""
        if month:
            qmonth = f"{year}-{int(month):02d}"
            # 该月区间天数
            from calendar import monthrange
            days_in_month = monthrange(year, int(month))[1]
            if days_in_month <= 31:
                # 仍然视为月份查询，展示明细和汇总
                if qtype:
                    c.execute(
                        "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=? ORDER BY id ASC",
                        (str(user_id), qtype, qmonth)
                    )
                    rows = c.fetchall()
                    c.execute(
                        "SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=?",
                        (str(user_id), qtype, qmonth)
                    )
                    total = c.fetchone()[0] or 0.0
                    msg += f"{year}年{int(month)}月{qtype}明细：\n"
                    for i, row in enumerate(rows, 1):
                        msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
                    msg += f"{year}年{int(month)}月总{qtype}：{total:.2f}"
                else:
                    # 收入
                    c.execute(
                        "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type='income' AND strftime('%Y-%m', date)=? ORDER BY id ASC",
                        (str(user_id), qmonth)
                    )
                    rows = c.fetchall()
                    c.execute(
                        "SELECT SUM(amount) FROM bills WHERE user_id=? AND type='income' AND strftime('%Y-%m', date)=?",
                        (str(user_id), qmonth)
                    )
                    total_income = c.fetchone()[0] or 0.0
                    msg += f"{year}年{int(month)}月收入明细：\n"
                    for i, row in enumerate(rows, 1):
                        msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
                    msg += f"{year}年{int(month)}月总收入：{total_income:.2f}\n"
                    # 支出
                    c.execute(
                        "SELECT amount, category, description, date FROM bills WHERE user_id=? AND type='expense' AND strftime('%Y-%m', date)=? ORDER BY id ASC",
                        (str(user_id), qmonth)
                    )
                    rows = c.fetchall()
                    c.execute(
                        "SELECT SUM(amount) FROM bills WHERE user_id=? AND type='expense' AND strftime('%Y-%m', date)=?",
                        (str(user_id), qmonth)
                    )
                    total_expense = c.fetchone()[0] or 0.0
                    msg += f"{year}年{int(month)}月支出明细：\n"
                    for i, row in enumerate(rows, 1):
                        msg += f"{i} | {row[0]:.2f} | {row[1]} | {row[2]} | {row[3]}\n"
                    msg += f"{year}年{int(month)}月总支出：{total_expense:.2f}"
                conn.close()
                if msg:
                    await update.message.reply_text(msg)
                    return True
                return False
            # 超过31天，分类汇总
            if qtype:
                c.execute(
                    "SELECT category, SUM(amount) FROM bills WHERE user_id=? AND type=? AND strftime('%Y-%m', date)=? GROUP BY category ORDER BY SUM(amount) DESC",
                    (str(user_id), qtype, qmonth)
                )
                rows = c.fetchall()
                msg += f"{year}年{int(month)}月{qtype}分类汇总：\n"
                for i, row in enumerate(rows, 1):
                    msg += f"{i} | {row[0]} | {row[1]:.2f}\n"
            else:
                # 收入
                c.execute(
                    "SELECT category, SUM(amount) FROM bills WHERE user_id=? AND type='income' AND strftime('%Y-%m', date)=? GROUP BY category ORDER BY SUM(amount) DESC",
                    (str(user_id), qmonth)
                )
                rows = c.fetchall()
                msg += f"{year}年{int(month)}月收入分类汇总：\n"
                for i, row in enumerate(rows, 1):
                    msg += f"{i} | {row[0]} | {row[1]:.2f}\n"
                # 支出
                c.execute(
                    "SELECT category, SUM(amount) FROM bills WHERE user_id=? AND type='expense' AND strftime('%Y-%m', date)=? GROUP BY category ORDER BY SUM(amount) DESC",
                    (str(user_id), qmonth)
                )
                rows = c.fetchall()
                msg += f"{year}年{int(month)}月支出分类汇总：\n"
                for i, row in enumerate(rows, 1):
                    msg += f"{i} | {row[0]} | {row[1]:.2f}\n"
        else:
            # 去年全年等，直接分类汇总
            if qtype:
                c.execute(
                    "SELECT category, SUM(amount) FROM bills WHERE user_id=? AND type=? AND strftime('%Y', date)=? GROUP BY category ORDER BY SUM(amount) DESC",
                    (str(user_id), qtype, str(year))
                )
                rows = c.fetchall()
                msg += f"{year}年{qtype}分类汇总：\n"
                for i, row in enumerate(rows, 1):
                    msg += f"{i} | {row[0]} | {row[1]:.2f}\n"
            else:
                # 收入
                c.execute(
                    "SELECT category, SUM(amount) FROM bills WHERE user_id=? AND type='income' AND strftime('%Y', date)=? GROUP BY category ORDER BY SUM(amount) DESC",
                    (str(user_id), str(year))
                )
                rows = c.fetchall()
                msg += f"{year}年收入分类汇总：\n"
                for i, row in enumerate(rows, 1):
                    msg += f"{i} | {row[0]} | {row[1]:.2f}\n"
                # 支出
                c.execute(
                    "SELECT category, SUM(amount) FROM bills WHERE user_id=? AND type='expense' AND strftime('%Y', date)=? GROUP BY category ORDER BY SUM(amount) DESC",
                    (str(user_id), str(year))
                )
                rows = c.fetchall()
                msg += f"{year}年支出分类汇总：\n"
                for i, row in enumerate(rows, 1):
                    msg += f"{i} | {row[0]} | {row[1]:.2f}\n"
        conn.close()
        if msg:
            await update.message.reply_text(msg)
            return True
        return False

    # 原有关键词匹配
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

def has_permission(user_id, chat_type, chat_id, text, state):
    # 管理员
    if chat_type == "private":
        if is_admin(user_id):
            if state == WAITING and text == "授权":
                return False  # 管理员私聊禁止“授权”
            return True      # 其它全部允许
        else:
            return False     # 非管理员私聊无权限
    else:
        if is_admin(user_id):
            # 群组管理员只能授权，不能记账和其他操作
            if text == "授权":
                return True
            return False
        elif is_authorized(user_id, chat_id):
            if text == "授权":
                return False # 被授权人群组内禁止“授权”
            return True      # 其它指令全部允许
        else:
            return False     # 群组内未授权人无任何权限

# 在 handle_message 里这样用：
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat = update.effective_chat
    chat_type = update.message.chat.type
    state = user_state.get(user_id, WAITING)
    text = update.message.text.strip()

    if not has_permission(user_id, chat_type, chat.id, text, state):
        return  # 没权限直接返回

    # ...后续你的业务逻辑...

    # 只允许严格格式命令和关键词查询，彻底关闭自然语言记账
    valid_cmds = ["账单", "报表", "清除", "查询", "返回", "授权", "收入", "帮助", "开始"]
    m_income = re.match(r"收入\s+([+-]?[0-9]+(?:\.[0-9]+)?)(?:\s+(.+))?", text)
    m_expense = re.match(r"([+-]?[0-9]+(?:\.[0-9]+)?)\s+(.+)", text)

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
                (str(user_id), amount, "其他", desc, today_str)
            )
            conn.commit()
            conn.close()
            await reply_record_success(update, user_id, "expense", amount, desc, today_str)
            reset_state(user_id)
            return
        if text.isdigit():
            return
        # 只允许关键词查询和严格命令
        if await quick_keyword_query(update, user_id, text):
            set_timeout(user_id)
            return
        if text == "返回":
            await return_cmd(update, context)
            return
        if text == "开始":
            await show_menu(update)
            return
        if text == "帮助":
            await help_cmd(update, context)
            return
        elif text == "账单":
            # await bill_cmd(update, context)  # bill_cmd未定义，暂时注释避免报错
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
        # 只支持严格格式命令，彻底关闭自然语言记账
        if not await handle_record(update, context):
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
        logger.warning(f"[MSG] 进入PENDING_NL_RECORD确认流程 user_id={user_id} text={getattr(update.message, 'text', None)}")
        await handle_nl_record_confirm(update, context)
        # 不再 set_timeout，让 handle_nl_record_confirm 自己控制
        return
    else:
        reset_state(user_id)
        logger.warning(f"[CONFIRM] ...已reset user_id={user_id}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin_or_authorized(user_id):
        return
    await show_menu(update)
    reset_state(user_id)
    logger.warning(f"[CONFIRM] ...已reset user_id={user_id}")

def main():
    application = Application.builder().token(TOKEN).build()
    # 授权类型选择（1/2）
    # application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^[12]$"), handle_auth_type))
    # 授权天数输入处理（正则允许前后空格和中文一二，注册顺序在 handle_auth_type 之后）
    # application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^\s*(\d{1,3}|[一二])\s*$"), handle_auth_days))
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
