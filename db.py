import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "records.db")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            is_admin INTEGER,
            is_authorized INTEGER
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            username TEXT,
            amount REAL,
            category TEXT,
            desc TEXT,
            type TEXT,
            date TEXT
        )""")
        await db.commit()

async def add_user(tg_id, username, is_admin=0, is_authorized=0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id, username, is_admin, is_authorized) VALUES (?, ?, ?, ?)",
            (tg_id, username, is_admin, is_authorized)
        )
        await db.commit()

async def is_user_authorized(username):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_admin, is_authorized FROM users WHERE username=?", (username,)) as cursor:
            row = await cursor.fetchone()
            return row and (row[0] == 1 or row[1] == 1)

async def authorize_user(username):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_authorized=1 WHERE username=?", (username,))
        await db.commit()
        return True

async def unauthorize_user(username):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_authorized=0 WHERE username=?", (username,))
        await db.commit()
        return True

async def add_record(tg_id, username, amount, category, desc, type_, date):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO records (tg_id, username, amount, category, desc, type, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tg_id, username, amount, category, desc, type_, date)
        )
        await db.commit()

async def get_month_records(tg_id, month, type_=None):
    async with aiosqlite.connect(DB_PATH) as db:
        if type_:
            async with db.execute(
                "SELECT amount, category, desc, date FROM records WHERE tg_id=? AND strftime('%m', date)=? AND type=? ORDER BY date DESC",
                (tg_id, f"{int(month):02d}", type_)
            ) as cursor:
                return await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT amount, category, desc, date, type FROM records WHERE tg_id=? AND strftime('%m', date)=? ORDER BY date DESC",
                (tg_id, f"{int(month):02d}",)
            ) as cursor:
                return await cursor.fetchall()

async def get_month_report(tg_id, month):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT category, SUM(amount) FROM records WHERE tg_id=? AND strftime('%m', date)=? GROUP BY category",
            (tg_id, f"{int(month):02d}",)
        ) as cursor:
            return await cursor.fetchall()

async def clear_records(tg_id, mode):
    async with aiosqlite.connect(DB_PATH) as db:
        if mode == "all":
            await db.execute("DELETE FROM records WHERE tg_id=?", (tg_id,))
        else:
            today = datetime.date.today().strftime("%Y-%m-%d")
            await db.execute("DELETE FROM records WHERE tg_id=? AND date=?", (tg_id, today))
        await db.commit()

async def get_day_records(tg_id, month, day):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT amount, category, desc, date FROM records WHERE tg_id=? AND strftime('%m', date)=? AND strftime('%d', date)=?",
            (tg_id, f"{int(month):02d}", f"{int(day):02d}")
        ) as cursor:
            return await cursor.fetchall()

async def get_recent_records(tg_id, type_):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT amount, category, desc, date FROM records WHERE tg_id=? AND type=? ORDER BY date DESC LIMIT 5",
            (tg_id, type_)
        ) as cursor:
            return await cursor.fetchall()

async def get_today_total(tg_id, type_):
    today = datetime.date.today().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT SUM(amount) FROM records WHERE tg_id=? AND date=? AND type=?",
            (tg_id, today, type_)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else 0.0