
import os
import re
import sqlite3
import asyncio
from datetime import datetime, date, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, ChatMemberHandler
from telegram.constants import ChatType
from telegram.error import BadRequest
from apscheduler.schedulers.background import BackgroundScheduler

OWNER_ID = 6557638908

# 授权到期提醒定时任务
async def remind_authorization_expiry(app: Application):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().date()
    for delta in [2, 1]:
        target_date = (today + timedelta(days=delta)).strftime('%Y-%m-%d')
        c.execute("SELECT chat_id, username, end_date FROM authorizations WHERE end_date=?", (target_date,))
        rows = c.fetchall()
        for chat_id, username, end_date in rows:
            # 获取群组名
            try:
                chat = await app.bot.get_chat(chat_id)
                group_title = chat.title or "群组"
            except Exception:
                group_title = "群组"
            # 群主提醒
            owner_msg = f"@{username} 在“{group_title}”群组记账权限还剩{delta}天。"
            try:
                await app.bot.send_message(chat_id=OWNER_ID, text=owner_msg)
            except BadRequest:
                pass
            except Exception:
                pass
            # 被授权人提醒
            user_msg = f"您在“{group_title}”记账权限还有{delta}天。"
            try:
                # 需被授权人私聊过机器人才能发私信
                await app.bot.send_message(chat_id=f"@{username}", text=user_msg)
            except BadRequest:
                pass
            except Exception:
                pass
    conn.close()

import sqlite3
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, ChatMemberHandler
from telegram.constants import ChatType
from datetime import datetime, date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
import asyncio
import os

OWNER_ID = 6557638908
import re

# 私聊“群组”自动回复所有群组信息
async def private_group_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 仅私聊触发
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    # 仅允许管理员私聊（除授权/取消授权外）
    user_id = update.effective_user.id
    try:
        admins = await context.bot.get_chat_administrators(update.effective_user.id)
        admin_ids = [a.user.id for a in admins]
    except Exception:
        admin_ids = []
    if user_id != OWNER_ID and user_id not in admin_ids:
        return
    text = update.message.text.strip()
    if text != '群组':
        return
    group_ids = context.bot_data.get('group_ids', set())
    if not group_ids:
        await update.message.reply_text("暂无群组信息。")
        return
    msg_list = []
    import sqlite3
    from datetime import date
    for gid in group_ids:
        try:
            chat = await context.bot.get_chat(gid)
            if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
                continue
            group_title = chat.title or "(无群名)"
            try:
                member_count = await context.bot.get_chat_member_count(gid)
                member_str = f"成员总数：{member_count}人"
            except Exception as e:
                member_str = f"成员总数：未知\n（无法获取成员数，请确保机器人为管理员并已开启‘查看群成员’权限。如权限无误但仍无法获取，可能为 Telegram 平台限制。\n错误信息：{e}）"
            try:
                admins = await context.bot.get_chat_administrators(gid)
                admin_lines = []
                for a in admins:
                    uname = f"@{a.user.username}" if a.user.username else ""
                    admin_lines.append(f"{a.user.full_name}：{uname}")
            except Exception:
                admin_lines = ["（获取失败，需机器人为管理员）"]

            # 查询被授权人列表
            try:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                today = date.today()
                c.execute("SELECT username, end_date FROM authorizations WHERE chat_id=? AND end_date>=?", (gid, today.strftime('%Y-%m-%d')))
                rows = c.fetchall()
                conn.close()
                if rows:
                    auth_lines = []
                    for username, end_date in rows:
                        try:
                            d1 = today
                            d2 = date.fromisoformat(end_date)
                            left = (d2 - d1).days
                        except Exception:
                            left = '?'
                        auth_lines.append(f"@{username}（剩余{left}天）")
                    auth_str = '被授权人：\n' + '\n'.join(auth_lines)
                else:
                    auth_str = '被授权人：无'
            except Exception:
                auth_str = '被授权人：查询失败'

            msg = f'群组名称：“{group_title}”\n{member_str}\n{auth_str}\n管理员：\n' + '\n'.join(admin_lines)
            msg_list.append(msg)
        except Exception:
            continue
    if not msg_list:
        await update.message.reply_text("暂无群组信息。")
        return
    await update.message.reply_text('\n\n'.join(msg_list))

# 记账机器人，严格按用户要求分块收入/支出、格式化输出

import sqlite3
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, ChatMemberHandler
from telegram.constants import ChatType
from datetime import datetime, date

# 群组消息自动补录群组ID
async def ensure_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return
    group_ids = context.bot_data.setdefault('group_ids', set())
    if chat.id not in group_ids:
        group_ids.add(chat.id)
        try:
            save_group_ids(group_ids)
        except Exception:
            pass

# 删除账单对话状态
DELETE_WAIT_DATE = 10
DELETE_WAIT_CHOICE = 11

async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 权限判断：OWNER_ID私聊有全部业务权限，群组无业务权限，被授权人群组有业务权限，未授权人无权限
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == ChatType.PRIVATE:
        if user.id == OWNER_ID:
            pass
        else:
            return ConversationHandler.END
    elif chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if user.id == OWNER_ID:
            return ConversationHandler.END
        if not user.username:
            await update.message.reply_text("请先设置 Telegram 用户名（@用户名）后再使用记账功能。"); return ConversationHandler.END
        username = user.username.lower()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT 1 FROM authorizations WHERE chat_id=? AND username=? AND end_date>=?", (chat.id, username, date.today().strftime('%Y-%m-%d')))
        row = c.fetchone()
        conn.close()
        if not row:
            return ConversationHandler.END
    else:
        return ConversationHandler.END
    await update.message.reply_text(
        "请输入日期，例如：\n"
        "-----------------------------\n"
        "指定哪天：8月8日\n"
        "-----------------------------\n"
        "本年月份：1-12\n"
        "-----------------------------\n"
        "年份：2025年\n"
        "-----------------------------\n"
        "年月：2025年8月\n"
        "-----------------------------\n"
        "输入【所有】删除全部账单。"
    )
    return DELETE_WAIT_DATE

async def delete_wait_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 权限判断：OWNER_ID私聊有全部业务权限，群组无业务权限，被授权人群组有业务权限，未授权人无权限
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == ChatType.PRIVATE:
        if user.id == OWNER_ID:
            pass
        else:
            return ConversationHandler.END
    elif chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if user.id == OWNER_ID:
            return ConversationHandler.END
        if not user.username:
            await update.message.reply_text("请先设置 Telegram 用户名（@用户名）后再使用记账功能。"); return ConversationHandler.END
        username = user.username.lower()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT 1 FROM authorizations WHERE chat_id=? AND username=? AND end_date>=?", (chat.id, username, date.today().strftime('%Y-%m-%d')))
        row = c.fetchone()
        conn.close()
        if not row:
            return ConversationHandler.END
    else:
        return ConversationHandler.END
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    # 处理“所有”
    if text == '所有':
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, amount, description, date FROM bills WHERE user_id=? ORDER BY date DESC, id DESC", (user_id,))
        rows = c.fetchall()
        conn.close()
        if not rows:
            await update.message.reply_text("无记录，无需删除。")
            return ConversationHandler.END
        context.user_data['delete_rows'] = rows
        msg = "所有账单明细：\n"
        for idx, (rid, amt, desc, dt) in enumerate(rows, 1):
            msg += f"{idx}| {amt:.2f} | {desc} | {dt}\n"
        msg += "\n请输入要删除的编号，多个请用逗号分隔，全部删除请输入 all："
        await update.message.reply_text(msg)
        return DELETE_WAIT_CHOICE
    # 解析日期
    parsed = parse_bill_date(text)
    if not parsed:
        await update.message.reply_text("输入格式错误")
        return ConversationHandler.END
    kind, val, label = parsed
    now = date.today()
    year = now.year
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if kind == 'yearmonth':
        date_like = f'{val}%'
        c.execute("SELECT id, amount, description, date FROM bills WHERE user_id=? AND date LIKE ? ORDER BY date DESC, id DESC", (user_id, date_like))
    elif kind == 'month':
        date_like = f'{year}-{val}%'
        c.execute("SELECT id, amount, description, date FROM bills WHERE user_id=? AND date LIKE ? ORDER BY date DESC, id DESC", (user_id, date_like))
    elif kind == 'year':
        date_like = f'{val}-%'
        c.execute("SELECT id, amount, description, date FROM bills WHERE user_id=? AND date LIKE ? ORDER BY date DESC, id DESC", (user_id, date_like))
    elif kind == 'day':
        # 只查本年
        date_eq = f'{year}-{val}'
        c.execute("SELECT id, amount, description, date FROM bills WHERE user_id=? AND date = ? ORDER BY date DESC, id DESC", (user_id, date_eq))
    else:
        await update.message.reply_text("输入格式错误")
        return ConversationHandler.END
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("该范围内无记录，无需删除。")
        return ConversationHandler.END
    context.user_data['delete_rows'] = rows
    msg = f"{label}账单明细：\n"
    for idx, (rid, amt, desc, dt) in enumerate(rows, 1):
        msg += f"{idx}| {amt:.2f} | {desc} | {dt}\n"
    msg += "\n请输入要删除的编号，多个请用逗号分隔，全部删除请输入 all："
    await update.message.reply_text(msg)
    return DELETE_WAIT_CHOICE

async def delete_wait_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 权限判断：OWNER_ID私聊有全部业务权限，群组无业务权限，被授权人群组有业务权限，未授权人无权限
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == ChatType.PRIVATE:
        if user.id == OWNER_ID:
            pass
        else:
            return ConversationHandler.END
    elif chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if user.id == OWNER_ID:
            return ConversationHandler.END
        if not user.username:
            await update.message.reply_text("请先设置 Telegram 用户名（@用户名）后再使用记账功能。"); return ConversationHandler.END
        username = user.username.lower()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT 1 FROM authorizations WHERE chat_id=? AND username=? AND end_date>=?", (chat.id, username, date.today().strftime('%Y-%m-%d')))
        row = c.fetchone()
        conn.close()
        if not row:
            await update.message.reply_text("你未被授权或授权已过期，请联系群主授权。")
            return ConversationHandler.END
    else:
        return ConversationHandler.END
    user_id = str(update.effective_user.id)
    text = update.message.text.strip().lower()
    rows = context.user_data.get('delete_rows', [])
    if not rows:
        await update.message.reply_text("操作超时或无可删记录。")
        return ConversationHandler.END
    if text == 'all':
        ids = [str(r[0]) for r in rows]
    else:
        try:
            idxs = [int(x) for x in text.replace('，', ',').split(',') if x.strip()]
        except Exception:
            await update.message.reply_text("编号格式错误，请重新输入编号，多个用逗号分隔，全部删除请输入 all：")
            return ConversationHandler.END
        if not idxs or any(i < 1 or i > len(rows) for i in idxs):
            await update.message.reply_text("编号超出范围，请重新输入。")
            return ConversationHandler.END
        ids = [str(rows[i-1][0]) for i in idxs]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"DELETE FROM bills WHERE user_id=? AND id IN ({','.join(['?']*len(ids))})", (user_id, *ids))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    await update.message.reply_text(f"删除成功，共删除{deleted}条记录。\n\n可继续输入账单、报表、删除等指令。")
    return ConversationHandler.END

# 记账机器人，严格按用户要求分块收入/支出、格式化输出
import sqlite3
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from datetime import datetime, date

# 报表对话状态
REPORT_WAIT_DATE = 2

async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 权限判断：OWNER_ID私聊有全部业务权限，群组无业务权限，被授权人群组有业务权限，未授权人无权限
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == ChatType.PRIVATE:
        if user.id == OWNER_ID:
            pass
        else:
            return ConversationHandler.END
    elif chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        # 拥有者和被授权人均有权限
        if user.id == OWNER_ID:
            pass
        else:
            if not user.username:
                await update.message.reply_text("请先设置 Telegram 用户名（@用户名）后再使用记账功能。")
                return ConversationHandler.END
            username = user.username.lower()
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT 1 FROM authorizations WHERE chat_id=? AND username=? AND end_date>=?", (chat.id, username, date.today().strftime('%Y-%m-%d')))
            row = c.fetchone()
            conn.close()
            if not row:
                return ConversationHandler.END
    else:
        return ConversationHandler.END
    await update.message.reply_text(
        "请输入例如：\n"
        "-----------------------------\n"
        "月份：1-12\n"
        "-----------------------------\n"
        "年份：2024年\n"
        "-----------------------------\n"
        "年月：2024年8月"
    )
    return REPORT_WAIT_DATE

def auto_category(desc: str) -> str:
    """
    自动分类：根据描述内容归入固定分类。
    分类：餐饮、购物、娱乐、日常生活、交通、医疗、学习、通讯、住房、其他
    """
    if not desc or not desc.strip():
        return ('其他', None)
    desc = desc.strip().lower()
    # 收入/支出分开映射
    income_mapping = [
        ('工资', ['工资', '薪资', '薪水', '奖金', '津贴', '补贴', '绩效', '年终奖', '提成', '全勤奖']),
        ('理财', ['理财', '利息', '分红', '基金', '股票', '收益', '投资', '回报', '理财收益', '理财产品', '理财分红']),
        ('转账', ['转账', '收款', '还款', '退款', '借款', '借入', '借出', '支付宝转账', '微信转账', '银行卡转账']),
        ('其他', ['红包', '奖励', '报销', '赔偿', '补发', '返现', '补助', '其他', '补贴', '补偿', '补款'])
    ]
    expense_mapping = [
        ('住房', ['住房', '房租', '房贷', '买房', '装修', '中介', '物业', '租金', '水电', '燃气', '取暖', '供暖', '管理费', '房产税', '房屋', '电费', '水费', '煤气', '物业费', '押金', '维修费', '房屋保险']),
        ('餐饮', ['餐', '饭', '食', '早餐', '午餐', '晚餐', '外卖', '饮', '奶茶', '咖啡', '小吃', '聚餐', '酒', '水果', '买菜', '买零食', '吃晚餐', '夜宵', '下午茶', '饮料', '甜品', '烧烤', '快餐', '自助', '点心']),
        ('购物', ['购物', '买', '超市', '商场', '网购', '京东', '淘宝', '拼多多', '衣', '鞋', '化妆', '数码', '家电', '日用品', '买东西', '买衣服', '买烟', '食品', '图书', '母婴', '宠物', '美妆', '护肤', '饰品', '箱包', '玩具', '文具']),
        ('娱乐', ['娱乐', '电影', '游戏', 'ktv', '演出', '旅游', '门票', '聚会', '唱歌', '运动', '健身', '球', '游泳', '展览', '展会', '马拉松', '棋牌', '桌游', '剧本杀', '密室', '电竞']),
        ('日常生活', ['生活', '卫生', '洗衣', '理发', '快递', '家政', '维修', '清洁', '日常', '杂费', '垃圾', '保洁', '照明', '饮用水', '买矿泉水', '给小孩', '家教', '保姆', '月嫂', '托管', '家电维修', '家居', '搬家']),
        ('交通', ['交通', '公交', '地铁', '打车', '滴滴', '高铁', '火车', '飞机', '加油', '停车', '过路', '租车', '油', '车险', '地面交通', '地面费', '地铁卡', '地铁票', '公交卡', '公交票', '顺风车', '共享单车', '高速', '打的', '网约车', '的士']),
        ('医疗', ['医疗', '医院', '药', '体检', '挂号', '看病', '保险', '疫苗', '诊疗', '药品', '门诊', '住院', '手术', '化验', '检查', '医药费', '医保', '牙科', '眼科', '疫苗接种']),
        ('学习', ['学习', '培训', '学费', '书', '考试', '辅导', '课程', '教育', '资料', '教材', '讲座', '报名', '网课', '考证', '考级', '学杂费', '兴趣班', '补课', '学具']),
        ('通讯', ['通讯', '话费', '手机', '流量', '宽带', '网费', '电信', '联通', '移动', '座机', '固话', '充值', '电话卡', 'SIM卡', '宽带费', '上网费']),
        ('转账', ['退回', '转账', '还款', '借款', '收款', '退款']),
        ('其他', ['卖废品', '捐款', '慈善', '公益', '罚款', '违章', '手续费', '服务费', '手续费', '其他']),
    ]
    # 二级分类（如“餐饮-早餐”）
    sub_mapping = {
        '餐饮': ['早餐', '午餐', '晚餐', '夜宵', '下午茶', '饮料', '奶茶', '咖啡', '水果', '外卖', '买菜', '买零食', '吃晚餐', '甜品', '烧烤', '快餐', '自助', '点心'],
        '购物': ['数码', '家电', '衣', '鞋', '化妆', '日用品', '食品', '图书', '母婴', '宠物', '买东西', '买衣服', '买烟', '美妆', '护肤', '饰品', '箱包', '玩具', '文具'],
        '日常生活': ['买矿泉水', '给小孩', '家教', '保姆', '月嫂', '托管', '家电维修', '家居', '搬家'],
        '娱乐': ['电影', '游戏', 'ktv', '演出', '旅游', '门票', '运动', '健身', '游泳', '聚会', '展览', '展会', '马拉松', '棋牌', '桌游', '剧本杀', '密室', '电竞'],
        '住房': ['房租', '房贷', '物业', '水电', '燃气', '取暖', '管理费', '电费', '水费', '煤气', '物业费', '押金', '维修费', '房屋保险'],
        '交通': ['打车', '公交', '地铁', '滴滴', '高铁', '火车', '飞机', '加油', '停车', '过路', '租车', '油', '车险', '地面交通', '地面费', '地铁卡', '地铁票', '公交卡', '公交票', '顺风车', '共享单车', '高速', '打的', '网约车', '的士', '违章'],
        '医疗': ['医院', '药', '体检', '挂号', '看病', '保险', '疫苗', '诊疗', '药品', '门诊', '住院', '手术', '化验', '检查', '医药费', '医保', '牙科', '眼科', '疫苗接种'],
        '学习': ['培训', '学费', '书', '考试', '辅导', '课程', '教育', '资料', '教材', '讲座', '报名', '网课', '考证', '考级', '学杂费', '兴趣班', '补课', '学具'],
        '通讯': ['话费', '手机', '流量', '宽带', '网费', '电信', '联通', '移动', '座机', '固话', '充值', '电话卡', 'SIM卡', '宽带费', '上网费'],
        '转账': ['退回', '转账', '还款', '借款', '收款', '退款'],
        '其他': ['卖废品', '捐款', '慈善', '公益', '罚款', '手续费', '服务费', '其他'],
    }
    # 判断收入/支出
    import inspect
    frame = inspect.currentframe().f_back
    is_income = False
    if frame and 'income_rows' in frame.f_locals:
        is_income = True
    mapping = income_mapping if is_income else expense_mapping
    # 优先判断子类
    for cat, sub_keys in sub_mapping.items():
        for sub in sub_keys:
            if sub in desc:
                return (cat, sub)
    # 再判断主类
    for cat, keys in mapping:
        for k in keys:
            if k in desc:
                return (cat, None)
    return ('其他', None)

async def report_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 权限判断：OWNER_ID私聊有全部业务权限，群组无业务权限，被授权人群组有业务权限，未授权人无权限
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == ChatType.PRIVATE:
        if user.id == OWNER_ID:
            pass
        else:
            return ConversationHandler.END
    elif chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if user.id == OWNER_ID:
            return ConversationHandler.END
        username = user.username.lower()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT 1 FROM authorizations WHERE chat_id=? AND username=? AND end_date>=?", (chat.id, username, date.today().strftime('%Y-%m-%d')))
        row = c.fetchone()
        conn.close()
        if not row:
            await update.message.reply_text("你未被授权或授权已过期，请联系群主授权。")
            return ConversationHandler.END
    else:
        return ConversationHandler.END
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    parsed = parse_bill_date(text)
    if not parsed:
        await update.message.reply_text("输入格式错误")
        return ConversationHandler.END
    kind, val, label = parsed
    now = date.today()
    year = now.year
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 构造查询区间
    if kind == 'yearmonth':
        ym = val
        label_fmt = f"{ym.replace('-', '年')}月报表"
        date_like = f'{ym}%'
    elif kind == 'month':
        label_fmt = f"{year}年{int(val):02d}月报表"
        date_like = f'{year}-{val}%'
    elif kind == 'year':
        label_fmt = f"{val}年报表"
        date_like = f'{val}-%'
    else:
        await update.message.reply_text("输入格式错误")
        return ConversationHandler.END
    # 收入分类统计
    c.execute("SELECT description, amount FROM bills WHERE user_id=? AND type='收入' AND date LIKE ?", (user_id, date_like))
    income_rows = c.fetchall()
    income_total = len(income_rows)
    income_cat = {}
    income_uncat = []
    for desc, amt in income_rows:
        cat, sub = auto_category(desc)
        if cat == '未分类':
            income_uncat.append((amt, desc))
            continue
        # 只按主类统计
        if cat not in income_cat:
            income_cat[cat] = {'sum': 0.0, 'count': 0}
        income_cat[cat]['sum'] += amt
        income_cat[cat]['count'] += 1
    # 支出分类统计
    c.execute("SELECT description, amount FROM bills WHERE user_id=? AND type='支出' AND date LIKE ?", (user_id, date_like))
    expense_rows = c.fetchall()
    expense_total = len(expense_rows)
    expense_cat = {}
    expense_uncat = []
    for desc, amt in expense_rows:
        cat, sub = auto_category(desc)
        if cat == '未分类':
            expense_uncat.append((amt, desc))
            continue
        # 只按主类统计
        if cat not in expense_cat:
            expense_cat[cat] = {'sum': 0.0, 'count': 0}
        expense_cat[cat]['sum'] += amt
        expense_cat[cat]['count'] += 1
    conn.close()
    # 输出
    msg = f"{label_fmt}\n\n"
    # 收入部分
    msg += f"收入【共{income_total}笔】\n"
    if income_cat:
        for cat, v in income_cat.items():
            msg += f"{cat}：{v['sum']:.2f}【{v['count']}笔】\n"
    if income_uncat:
        msg += "未分类收入明细：\n"
        for amt, desc in income_uncat:
            msg += f"  {amt:.2f} | {desc}\n"
        uncat_income_sum = sum([amt for amt, _ in income_uncat])
        msg += f"未分类收入合计：{uncat_income_sum:.2f}\n"
    if not income_cat and not income_uncat:
        msg += ("本月无收入记录\n" if kind != 'year' else "本年无收入记录\n")
    msg += "\n"
    # 支出部分
    msg += f"支出【共{expense_total}笔】\n"
    if expense_cat:
        for cat, v in expense_cat.items():
            msg += f"{cat}：{v['sum']:.2f}【{v['count']}笔】\n"
    if expense_uncat:
        msg += "未分类支出明细：\n"
        for amt, desc in expense_uncat:
            msg += f"  {amt:.2f} | {desc}\n"
        uncat_expense_sum = sum([amt for amt, _ in expense_uncat])
        msg += f"未分类支出合计：{uncat_expense_sum:.2f}\n"
    if not expense_cat and not expense_uncat:
        msg += ("本月无支出记录\n" if kind != 'year' else "本年无支出记录\n")
    # 统计总收入、总支出
    total_income = sum([amt for _, amt in income_rows])
    total_expense = sum([amt for _, amt in expense_rows])
    msg += f"\n总收入：{total_income:.2f}\n总支出：{total_expense:.2f}"
    await update.message.reply_text(msg.strip())
    return ConversationHandler.END

# 记账机器人，严格按用户要求分块收入/支出、格式化输出
import sqlite3
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from datetime import datetime, date

DB_PATH = 'data.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            type TEXT,
            amount REAL,
            description TEXT,
            date TEXT
        )
    ''')
    # 新增授权表
    c.execute('''
        CREATE TABLE IF NOT EXISTS authorizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            username TEXT,
            start_date TEXT,
            end_date TEXT
        )
    ''')
    conn.commit()
    conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "欢迎使用记账机器人！\n\n"
        "收入记账：收入 金额 描述（如：收入 5000 工资 或 收入 -100 退款）\n"
        "支出记账：+金额 描述 或 -金额 描述（如：+50 买东西 或 -20 卖废品）\n"
        "查询收入：/income\n"
        "查询支出：/expense\n"
        "直接与我私聊即可，无需加群。"
    )

def parse_add_command(text: str):
    # 返回(type, amount, desc) 或 None
    text = text.strip()
    if text.startswith('收入'):
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            return None
        try:
            amount = float(parts[1])
        except Exception:
            return None
        desc = parts[2] if len(parts) > 2 else '未填写'
        return ('收入', amount, desc)
    elif text.startswith('+') or text.startswith('-'):
        # 支出
        try:
            sign = 1 if text[0] == '+' else -1
            rest = text[1:].strip()
            parts = rest.split(maxsplit=1)
            amount = float(parts[0]) * sign
            desc = parts[1] if len(parts) > 1 else '未填写'
            return ('支出', amount, desc)
        except Exception:
            return None
    return None

async def add_bill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # 排除对话流程关键词，防止重复捕获
        exclude_keywords = ['账单', '报表', '清除', '删除']
        text_raw = update.message.text.strip()
        # 若整行为关键词或以关键词开头（如“账单 1”），均排除
        import re
        if any(re.fullmatch(k, text_raw) or re.match(f'^{k}(\\s|$)', text_raw) for k in exclude_keywords):
            # 彻底避免对话流程关键词被add_bill兜底时出现格式错误提示，直接return
            return
        chat = update.effective_chat
        user = update.effective_user
        # 私聊
        if chat.type == ChatType.PRIVATE:
            if user.id == OWNER_ID:
                pass
            else:
                return
        # 群组
        elif chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            # OWNER_ID群组内无业务权限
            if user.id == OWNER_ID:
                return
            # 检查用户名
            if not user.username:
                await update.message.reply_text("请先设置 Telegram 用户名（@用户名）后再使用记账功能。")
                return
            # 检查是否被授权（被授权人有权限，统一用小写）
            username = user.username.lower()
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT 1 FROM authorizations WHERE chat_id=? AND username=? AND end_date>=?", (chat.id, username, date.today().strftime('%Y-%m-%d')))
            row = c.fetchone()
            conn.close()
            if not row:
                return
        else:
            return
        user_id = str(user.id)
        text = text_raw.replace('/add', '', 1).strip()
        parsed = parse_add_command(text)
        if not parsed:
            # 若输入为对话流程关键词或其开头，已在前面return，这里只对真正的记账格式错误提示
            return
        type_, amount, desc = parsed
        today_str = date.today().strftime('%Y-%m-%d')
        month_str = date.today().strftime('%Y-%m')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO bills (user_id, type, amount, description, date) VALUES (?, ?, ?, ?, ?)",
                  (user_id, type_, amount, desc, today_str))
        conn.commit()
        # 查询最近一笔
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type=? ORDER BY id DESC LIMIT 1", (user_id, type_))
        last = c.fetchone()
        # 查询今天所有收入/支出
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type=? AND date=? ORDER BY id DESC", (user_id, type_, today_str))
        today_all_rows = c.fetchall()
        today_count = len(today_all_rows)
        # 只显示最近5笔
        today_rows = today_all_rows[:5]
        # 查询今天累计
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND date=?", (user_id, type_, today_str))
        today_sum = c.fetchone()[0] or 0.0
        # 查询本月累计
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type=? AND date LIKE ?", (user_id, type_, f'{month_str}%'))
        month_sum = c.fetchone()[0] or 0.0
        conn.close()
        # 记录成功提示
        msg = f"记录成功：{last[0]:.2f}，{last[1]}\n"
        # 最近5笔
        if type_ == '收入':
            msg += f"\n最近5笔收入【今天累计{today_count}笔】\n"
        else:
            msg += f"\n最近5笔支出【今天累计{today_count}笔】\n"
        # 明细编号从今天累计N递减
        for idx, (amt, dsc) in zip(range(today_count, today_count-len(today_rows), -1), today_rows):
            msg += f"{idx}| {amt:.2f} | {dsc} |\n"
        msg += "\n"
        if type_ == '收入':
            msg += f"今天累计收入：{today_sum:.2f}\n本月累计收入：{month_sum:.2f}"
        else:
            msg += f"今天累计支出：{today_sum:.2f}\n本月累计支出：{month_sum:.2f}"
        await update.message.reply_text(msg)
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        await update.message.reply_text(f"发生错误：{e}\n请联系管理员。\n调试信息：\n{err}")

async def income_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if user.id == OWNER_ID:
            return
        if not user.username:
            return
        username = user.username.lower()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT 1 FROM authorizations WHERE chat_id=? AND username=? AND end_date>=?", (chat.id, username, date.today().strftime('%Y-%m-%d')))
        row = c.fetchone()
        conn.close()
        if not row:
            return
    user_id = str(update.effective_user.id)
    today_str = date.today().strftime('%Y-%m-%d')
    month_str = date.today().strftime('%Y-%m')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 查询今天所有收入
    c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='收入' AND date=? ORDER BY id DESC", (user_id, today_str))
    today_all_rows = c.fetchall()
    today_count = len(today_all_rows)
    # 只显示最近5笔
    today_rows = today_all_rows[:5]
    c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='收入' AND date=?", (user_id, today_str))
    today_sum = c.fetchone()[0] or 0.0
    c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='收入' AND date LIKE ?", (user_id, f'{month_str}%'))
    month_sum = c.fetchone()[0] or 0.0
    conn.close()
    msg = f"最近5笔收入（今天累计{today_count}笔）：\n"
    # 明细编号从今天累计N递减
    for idx, (amt, dsc) in zip(range(today_count, today_count-len(today_rows), -1), today_rows):
        msg += f"{idx}| {amt:.2f} | {dsc} |\n"
    msg += "\n今天累计收入：{:.2f}\n本月累计收入：{:.2f}".format(today_sum, month_sum)
    await update.message.reply_text(msg)


async def expense_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if user.id == OWNER_ID:
            return
        if not user.username:
            return
        username = user.username.lower()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT 1 FROM authorizations WHERE chat_id=? AND username=? AND end_date>=?", (chat.id, username, date.today().strftime('%Y-%m-%d')))
        row = c.fetchone()
        conn.close()
        if not row:
            return
    user_id = str(update.effective_user.id)
    today_str = date.today().strftime('%Y-%m-%d')
    month_str = date.today().strftime('%Y-%m')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 查询今天所有支出
    c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='支出' AND date=? ORDER BY id DESC", (user_id, today_str))
    today_all_rows = c.fetchall()
    today_count = len(today_all_rows)
    # 只显示最近5笔
    today_rows = today_all_rows[:5]
    c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='支出' AND date=?", (user_id, today_str))
    today_sum = c.fetchone()[0] or 0.0
    c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='支出' AND date LIKE ?", (user_id, f'{month_str}%'))
    month_sum = c.fetchone()[0] or 0.0
    conn.close()
    msg = f"最近5笔支出（今天累计{today_count}笔）：\n"
    # 明细编号从今天累计N递减
    for idx, (amt, dsc) in zip(range(today_count, today_count-len(today_rows), -1), today_rows):
        msg += f"{idx}| {amt:.2f} | {dsc} |\n"
    msg += "\n今天累计支出：{:.2f}\n本月累计支出：{:.2f}".format(today_sum, month_sum)
    await update.message.reply_text(msg)



# 账单对话状态
BILL_WAIT_DATE = 1

async def bill_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 权限判断：OWNER_ID私聊有全部业务权限，群组无业务权限，被授权人群组有业务权限，未授权人无权限
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == ChatType.PRIVATE:
        if user.id == OWNER_ID:
            pass
        else:
            return ConversationHandler.END
    elif chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if user.id == OWNER_ID:
            return ConversationHandler.END
        if not user.username:
            await update.message.reply_text("请先设置 Telegram 用户名（@用户名）后再使用记账功能。"); return ConversationHandler.END
        username = user.username.lower()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT 1 FROM authorizations WHERE chat_id=? AND username=? AND end_date>=?", (chat.id, username, date.today().strftime('%Y-%m-%d')))
        row = c.fetchone()
        conn.close()
        if not row:
            return ConversationHandler.END
    else:
        return ConversationHandler.END
    await update.message.reply_text(
        "请输入日期，例如：\n"
        "-----------------------------\n"
        "指定哪天：8月8日\n"
        "-----------------------------\n"
        "本年月份：1-12\n"
        "-----------------------------\n"
        "年份：2025年\n"
        "-----------------------------\n"
        "年月：2025年8月"
    )
    return BILL_WAIT_DATE

def parse_bill_date(text):
    # 允许: 日期（8月8日）、任意月份、任意年份、任意年月
    import re
    t = text.strip()
    # 1. 年月（2024年8月、2024-8、2024/8）
    m = re.fullmatch(r'(\d{4})[年/-/.]?(\d{1,2})月?', t)
    if m:
        year = m.group(1)
        month = int(m.group(2))
        return ('yearmonth', f'{year}-{month:02d}', f'{year}年{month:02d}月')
    # 2. 年份（2024年）
    m = re.fullmatch(r'(\d{4})年', t)
    if m:
        year = m.group(1)
        return ('year', year, f'{year}年')
    # 3. 月-日（8月8日、8-8、08月08日）
    m = re.fullmatch(r'(\d{1,2})[月/-](\d{1,2})日?', t)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        return ('day', f'{month:02d}-{day:02d}', f'{month:02d}月{day:02d}日')
    # 4. 纯数字（1-12），视为月份
    if re.fullmatch(r'0?[1-9]|1[0-2]', t):
        m = int(t)
        return ('month', f'{m:02d}', f'{m:02d}月')
    return None

async def bill_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 权限判断：OWNER_ID私聊有全部业务权限，群组无业务权限，被授权人群组有业务权限，未授权人无权限
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == ChatType.PRIVATE:
        if user.id == OWNER_ID:
            pass
        else:
            return ConversationHandler.END
    elif chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if user.id == OWNER_ID:
            return ConversationHandler.END
        username = user.username.lower()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT 1 FROM authorizations WHERE chat_id=? AND username=? AND end_date>=?", (chat.id, username, date.today().strftime('%Y-%m-%d')))
        row = c.fetchone()
        conn.close()
        if not row:
            await update.message.reply_text("你未被授权或授权已过期，请联系群主授权。")
            return ConversationHandler.END
    else:
        return ConversationHandler.END
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    parsed = parse_bill_date(text)
    if not parsed:
        await update.message.reply_text("输入格式错误")
        return ConversationHandler.END
    kind, val, label = parsed
    now = date.today()
    year = now.year
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if kind == 'yearmonth':
        ym = val
        y, m = ym.split('-')
        label_fmt = f"{y}年{int(m):02d}月"
        # 收入
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='收入' AND date LIKE ? ORDER BY id DESC", (user_id, f'{ym}%'))
        income_rows = c.fetchall()
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='收入' AND date LIKE ?", (user_id, f'{ym}%'))
        income_sum = c.fetchone()[0] or 0.0
        # 支出
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='支出' AND date LIKE ? ORDER BY id DESC", (user_id, f'{ym}%'))
        expense_rows = c.fetchall()
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='支出' AND date LIKE ?", (user_id, f'{ym}%'))
        expense_sum = c.fetchone()[0] or 0.0
        conn.close()
        msg = f"{label_fmt}收入明细（共{len(income_rows)}笔）：\n"
        if income_rows:
            total = len(income_rows)
            for idx, (amt, desc) in zip(range(total, 0, -1), income_rows):
                msg += f"{idx}| {amt:.2f} | {desc} |\n"
        else:
            msg += "无收入记录\n"
        msg += "\n"
        msg += f"{label_fmt}累计收入：{income_sum:.2f}\n"
        msg += "\n"
        msg += f"{label_fmt}支出明细（共{len(expense_rows)}笔）：\n"
        if expense_rows:
            total = len(expense_rows)
            for idx, (amt, desc) in zip(range(total, 0, -1), expense_rows):
                msg += f"{idx}| {amt:.2f} | {desc} |\n"
        else:
            msg += "无支出记录\n"
        msg += "\n"
        msg += f"{label_fmt}累计支出：{expense_sum:.2f}"
        await update.message.reply_text(msg)
        return ConversationHandler.END
    elif kind == 'day':
        # 只查本年
        date_eq = f'{year}-{val}'
        label_fmt = f"{year}年{val.replace('-', '月')}日"
        # 收入
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='收入' AND date = ? ORDER BY id DESC", (user_id, date_eq))
        income_rows = c.fetchall()
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='收入' AND date = ?", (user_id, date_eq))
        income_sum = c.fetchone()[0] or 0.0
        # 支出
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='支出' AND date = ? ORDER BY id DESC", (user_id, date_eq))
        expense_rows = c.fetchall()
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='支出' AND date = ?", (user_id, date_eq))
        expense_sum = c.fetchone()[0] or 0.0
        conn.close()
        msg = f"{label_fmt}收入明细（共{len(income_rows)}笔）：\n"
        if income_rows:
            total = len(income_rows)
            for idx, (amt, desc) in zip(range(total, 0, -1), income_rows):
                msg += f"{idx}| {amt:.2f} | {desc} |\n"
        else:
            msg += "无收入记录\n"
        msg += "\n"
        msg += f"{label_fmt}累计收入：{income_sum:.2f}\n"
        msg += "\n"
        msg += f"{label_fmt}支出明细（共{len(expense_rows)}笔）：\n"
        if expense_rows:
            total = len(expense_rows)
            for idx, (amt, desc) in zip(range(total, 0, -1), expense_rows):
                msg += f"{idx}| {amt:.2f} | {desc} |\n"
        else:
            msg += "无支出记录\n"
        msg += "\n"
        msg += f"{label_fmt}累计支出：{expense_sum:.2f}"
        await update.message.reply_text(msg)
        return ConversationHandler.END
    elif kind == 'month':
        # 只处理无年份的“5”或“05”输入，年份用当前年
        month_str = f"{year}-{val}"
        label_fmt = f"{year}年{int(val):02d}月"
        # 收入
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='收入' AND date LIKE ? ORDER BY id DESC", (user_id, f'{month_str}%'))
        income_rows = c.fetchall()
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='收入' AND date LIKE ?", (user_id, f'{month_str}%'))
        income_sum = c.fetchone()[0] or 0.0
        # 支出
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='支出' AND date LIKE ? ORDER BY id DESC", (user_id, f'{month_str}%'))
        expense_rows = c.fetchall()
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='支出' AND date LIKE ?", (user_id, f'{month_str}%'))
        expense_sum = c.fetchone()[0] or 0.0
        conn.close()
        msg = f"{label_fmt}收入明细（共{len(income_rows)}笔）：\n"
        if income_rows:
            total = len(income_rows)
            for idx, (amt, desc) in zip(range(total, 0, -1), income_rows):
                msg += f"{idx}| {amt:.2f} | {desc} |\n"
        else:
            msg += "无收入记录\n"
        msg += "\n"
        msg += f"{label_fmt}累计收入：{income_sum:.2f}\n"
        msg += "\n"
        msg += f"{label_fmt}支出明细（共{len(expense_rows)}笔）：\n"
        if expense_rows:
            total = len(expense_rows)
            for idx, (amt, desc) in zip(range(total, 0, -1), expense_rows):
                msg += f"{idx}| {amt:.2f} | {desc} |\n"
        else:
            msg += "无支出记录\n"
        msg += "\n"
        msg += f"{label_fmt}累计支出：{expense_sum:.2f}"
        await update.message.reply_text(msg)
        return ConversationHandler.END
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='收入' AND date=? ORDER BY id DESC", (user_id, date_str))
        income_rows = c.fetchall()
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='收入' AND date=?", (user_id, date_str))
        income_sum = c.fetchone()[0] or 0.0
        # 支出
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='支出' AND date=? ORDER BY id DESC", (user_id, date_str))
        expense_rows = c.fetchall()
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='支出' AND date=?", (user_id, date_str))
        expense_sum = c.fetchone()[0] or 0.0
        conn.close()
        msg = f"{label}收入明细（共{len(income_rows)}笔）：\n"
        if income_rows:
            total = len(income_rows)
            for idx, (amt, desc) in zip(range(total, 0, -1), income_rows):
                msg += f"{idx}| {amt:.2f} | {desc} |\n"
        else:
            msg += "无收入记录\n"
        msg += "\n"
        msg += f"{label}累计收入：{income_sum:.2f}\n"
        msg += "\n"
        msg += f"{label}支出明细（共{len(expense_rows)}笔）：\n"
        if expense_rows:
            total = len(expense_rows)
            for idx, (amt, desc) in zip(range(total, 0, -1), expense_rows):
                msg += f"{idx}| {amt:.2f} | {desc} |\n"
        else:
            msg += "无支出记录\n"
        msg += "\n"
        msg += f"{label}累计支出：{expense_sum:.2f}"
        await update.message.reply_text(msg)
        return ConversationHandler.END
    elif kind == 'year':
        y = val
        label_fmt = f"{y}年"
        # 收入
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='收入' AND date LIKE ? ORDER BY id DESC", (user_id, f'{y}-%'))
        income_rows = c.fetchall()
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='收入' AND date LIKE ?", (user_id, f'{y}-%'))
        income_sum = c.fetchone()[0] or 0.0
        # 支出
        c.execute("SELECT amount, description FROM bills WHERE user_id=? AND type='支出' AND date LIKE ? ORDER BY id DESC", (user_id, f'{y}-%'))
        expense_rows = c.fetchall()
        c.execute("SELECT SUM(amount) FROM bills WHERE user_id=? AND type='支出' AND date LIKE ?", (user_id, f'{y}-%'))
        expense_sum = c.fetchone()[0] or 0.0
        conn.close()
        msg = f"{label_fmt}收入明细（共{len(income_rows)}笔）：\n"
        if income_rows:
            total = len(income_rows)
            for idx, (amt, desc) in zip(range(total, 0, -1), income_rows):
                msg += f"{idx}| {amt:.2f} | {desc} |\n"
        else:
            msg += "无收入记录\n"
        msg += "\n"
        msg += f"{label_fmt}累计收入：{income_sum:.2f}\n"
        msg += "\n"
        msg += f"{label_fmt}支出明细（共{len(expense_rows)}笔）：\n"
        if expense_rows:
            total = len(expense_rows)
            for idx, (amt, desc) in zip(range(total, 0, -1), expense_rows):
                msg += f"{idx}| {amt:.2f} | {desc} |\n"
        else:
            msg += "无支出记录\n"
        msg += "\n"
        msg += f"{label_fmt}累计支出：{expense_sum:.2f}"
        await update.message.reply_text(msg)
        return ConversationHandler.END
    else:
        await update.message.reply_text("输入格式错误")
        return ConversationHandler.END

import os
GROUP_IDS_FILE = 'group_ids.txt'

def load_group_ids():
    if not os.path.exists(GROUP_IDS_FILE):
        return set()
    with open(GROUP_IDS_FILE, 'r', encoding='utf-8') as f:
        return set(int(line.strip()) for line in f if line.strip())

def save_group_ids(group_ids):
    with open(GROUP_IDS_FILE, 'w', encoding='utf-8') as f:
        for gid in group_ids:
            f.write(f'{gid}\n')


def main():
    init_db()
    app = Application.builder().token('7536100847:AAHslrzRe8eo9NmquNBSaYwSg0cgBU28GyM').build()
    # 私聊“群组”关键词，展示所有群组信息（仅 OWNER_ID 或管理员）
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.Regex(r'^(群组)$'),
        private_group_list
    ))
    # 授权命令处理（所有人都能触发 handler，但函数体内首行强制权限校验，仅 OWNER_ID 可用，其他人一律无权并提示）
    app.add_handler(MessageHandler(
        filters.TEXT & (~filters.COMMAND) & filters.Regex(r'^授权\s+@\S+\s+\d+$') & filters.User(user_id=OWNER_ID),
        authorize_user
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & (~filters.COMMAND) & filters.Regex(r'^取消授权\s+@\S+$') & filters.User(user_id=OWNER_ID),
        cancel_authorization
    ))
    # 启动定时任务（每天中午12点提醒授权到期）
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: asyncio.create_task(remind_authorization_expiry(app)), 'cron', hour=12, minute=0)
    scheduler.start()
    # 启动时加载群组ID
    app.bot_data['group_ids'] = load_group_ids()
    # 报表对话，支持/报表和“报表”关键词
    report_conv = ConversationHandler(
        entry_points=[
            CommandHandler('report', report_start),
            MessageHandler(filters.Regex(r'^(报表)$'), report_start)
        ],
        states={
            REPORT_WAIT_DATE: [MessageHandler(filters.TEXT & (~filters.COMMAND), report_show)]
        },
        fallbacks=[]
    )
    app.add_handler(report_conv)
    # 账单对话，支持/账单和“账单”关键词
    bill_conv = ConversationHandler(
        entry_points=[
            CommandHandler('bill', bill_start),
            MessageHandler(filters.Regex(r'^(账单)$'), bill_start)
        ],
        states={
            BILL_WAIT_DATE: [MessageHandler(filters.TEXT & (~filters.COMMAND), bill_show)]
        },
        fallbacks=[]
    )
    app.add_handler(bill_conv)
    # 删除账单对话，支持/delete、删除、清除
    delete_conv = ConversationHandler(
        entry_points=[
            CommandHandler('delete', delete_start),
            MessageHandler(filters.Regex(r'^(删除|清除)$'), delete_start)
        ],
        states={
            DELETE_WAIT_DATE: [MessageHandler(filters.TEXT & (~filters.COMMAND), delete_wait_date)],
            DELETE_WAIT_CHOICE: [MessageHandler(filters.TEXT & (~filters.COMMAND), delete_wait_choice)]
        },
        fallbacks=[]
    )
    app.add_handler(delete_conv)
    # 其他命令
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('income', income_list))
    app.add_handler(CommandHandler('expense', expense_list))
    app.add_handler(CommandHandler('groupinfo', groupinfo))
    # 新增“开始”关键词美观菜单
    app.add_handler(MessageHandler(filters.Regex(r'^(开始)$'), show_pretty_start_menu))
    # 新增“帮助”关键词美观帮助菜单
    app.add_handler(MessageHandler(filters.Regex(r'^(帮助)$'), show_pretty_help_menu))
    # 群组消息自动补录群组ID（必须在所有群组相关消息 handler 前注册）
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.GROUPS, ensure_group_id), group=0)
    # 私聊和群组消息都交由add_bill统一权限判断（必须最后注册，优先级最低）
    # 排除“账单”、“报表”、“清除”、“删除”关键词，防止这些指令被add_bill捕获
    exclude_keywords = ['账单', '报表', '清除', '删除']
    pattern = r'^(?!(' + '|'.join(exclude_keywords) + r')$).*'
    app.add_handler(MessageHandler(
        filters.TEXT & (~filters.COMMAND) & (filters.ChatType.PRIVATE | filters.ChatType.GROUPS) & filters.Regex(pattern),
        add_bill
    ), group=99)
    app.add_handler(ChatMemberHandler(track_group, ChatMemberHandler.MY_CHAT_MEMBER))
    app.run_polling()

# "帮助"关键词美观帮助菜单实现
async def show_pretty_help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "【记账机器人帮助】\n\n"
        "1. 记账\n"
        "- 收入：收入 金额 描述\n"
        "  例：收入 5000 工资\n"
        "- 支出：+金额 描述 或 -金额 描述\n"
        "  例：+50 买菜\n\n"
        "2. 查询\n"
        "- 账单明细：账单，按提示输入日期\n\n"
        "3. 删除账单\n"
        "- 删除，按提示输入日期或“所有”，可按编号批量删除\n\n"
        "4. 报表统计\n"
        "- 报表，按提示输入月份/年份/年月\n\n"
        "5. 群组授权（仅群主可用）\n"
        "- 授权成员：授权 @用户名 天数\n"
        "  例：授权 @alice 7\n"
        "- 取消授权：取消授权 @用户名\n"
        "  例：取消授权 @alice\n\n"
        "\n"
        "【注意事项】\n"
        "- 群组内账单隔离，每人只能操作自己的账单\n"
        "- 未授权成员无法在群组内使用业务功能\n"
        "- 输入格式错误有统一提示\n\n"
        "如有疑问请联系：@Daddywu999"
    )
    await update.message.reply_text(msg)

# "开始"关键词美观菜单实现
async def show_pretty_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 欢迎使用【记账机器人】！\n\n"
        "📒 主要功能\n"
        "——————————————\n"
        "• 个人记账（收入/支出）\n"
        "• 账单查询\n"
        "• 报表统计\n"
        "• 群组授权管理\n\n"
        "📝 常用指令\n"
        "——————————————\n"
        "• 记账：收入 5000 工资\n"
        "• 记账：+50 买菜\n"
        "• 账单明细： 账单\n"
        "• 删除账单： 删除\n"
        "• 报表统计： 报表\n\n"
        " 输入【帮助】查看详细帮助与命令说明"
    )
    await update.message.reply_text(msg)

# 监听机器人被拉入新群组，自动记录群组ID
from telegram import ChatMemberUpdated

async def track_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 仅处理机器人被加入群组的事件
    if update.my_chat_member is None:
        return
    chat = update.effective_chat
    # ...existing code...

# 群组信息命令实现
async def groupinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    # ...existing code...
if __name__ == '__main__':
    import re

    # 授权命令处理函数
    async def authorize_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        user_id = update.effective_user.id
        # 只允许OWNER_ID操作，其他人一律无权
        if user_id != OWNER_ID:
            await update.message.reply_text("无权限，仅机器人拥有者可操作。")
            return
        if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
            return
        text = update.message.text.strip()
        m = re.match(r'^授权\s+@(\S+)\s+(\d+)$', text)
        if not m:
            await update.message.reply_text("格式错误！请使用：授权 @用户名 天数\n如：授权 @alice 7")
            return
        username = m.group(1).lower()
        days = int(m.group(2))
        today = date.today()
        end_date = (today + timedelta(days=days)).strftime('%Y-%m-%d')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("REPLACE INTO authorizations (chat_id, username, start_date, end_date) VALUES (?, ?, ?, ?)", (chat.id, username, today.strftime('%Y-%m-%d'), end_date))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"已授权 @{username} 记账权限 {days} 天。")

    # 取消授权命令处理函数
    async def cancel_authorization(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        user_id = update.effective_user.id
        # 只允许OWNER_ID操作，其他人一律无权
        if user_id != OWNER_ID:
            await update.message.reply_text("无权限，仅机器人拥有者可操作。")
            return
        if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
            return
        text = update.message.text.strip()
        m = re.match(r'^取消授权\s+@(\S+)$', text)
        if not m:
            await update.message.reply_text("格式错误！请使用：取消授权 @用户名\n如：取消授权 @alice")
            return
        username = m.group(1)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM authorizations WHERE chat_id=? AND username=?", (chat.id, username))
        deleted = c.rowcount
        conn.commit()
        conn.close()
        if deleted:
            await update.message.reply_text(f"已取消@{username}的授权。")
        else:
            await update.message.reply_text(f"@{username}当前无授权记录。")

    # 定时任务：检查授权到期并提醒OWNER_ID
    async def check_authorization_expiry(app):
        from datetime import datetime, timedelta
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        tomorrow = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
        c.execute("SELECT chat_id, username, end_date FROM authorizations WHERE end_date=?", (tomorrow,))
        rows = c.fetchall()
        conn.close()
        if not rows:
            return
        # 组装提醒消息，自动获取群组名
        for chat_id, username, end_date in rows:
            try:
                chat = await app.bot.get_chat(chat_id)
                group_title = chat.title or str(chat_id)
            except Exception:
                group_title = str(chat_id)
            msg = f'您好！【“{group_title}”】里“{username}”记账机器人使用权限仅剩1天。'
            try:
                await app.bot.send_message(chat_id=OWNER_ID, text=msg)
            except Exception:
                pass
    main()
