import os
import re
import time
import sqlite3
import threading

from supybot import callbacks, commands, schedule, ircmsgs


class RemindMe(callbacks.Plugin):
    threaded = True

    def __init__(self, irc):
        super().__init__(irc)

        # Store DB in plugin directory (safe fallback)
        base_dir = os.path.dirname(__file__)
        self.db_path = os.path.join(base_dir, "reminders.db")

        self._db_lock = threading.Lock()

        self._init_db()
        self._load_reminders()

    # -------------------------
    # DB SETUP
    # -------------------------
    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nick TEXT NOT NULL,
                    target TEXT NOT NULL,
                    message TEXT NOT NULL,
                    execute_at INTEGER NOT NULL
                )
            """)
            conn.commit()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")  # safer concurrent access
        return conn

    # -------------------------
    # LOAD ON STARTUP
    # -------------------------
    def _load_reminders(self):
        now = int(time.time())

        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, nick, target, message, execute_at FROM reminders WHERE execute_at > ?",
                (now,)
            ).fetchall()

        for rid, nick, target, message, execute_at in rows:
            delay = execute_at - now
            self._schedule_reminder(rid, nick, target, message, delay)

    # -------------------------
    # CORE SCHEDULER
    # -------------------------
    def _schedule_reminder(self, rid, nick, target, message, delay):

        def reminder():
            action_text = f"reminds {nick} to {message}"

            try:
                self._send_reminder(target, action_text)
            finally:
                # Always clean up DB entry
                with self._db_lock:
                    with self._get_conn() as conn:
                        conn.execute(
                            "DELETE FROM reminders WHERE id = ?",
                            (rid,)
                        )
                        conn.commit()

        schedule.addEvent(reminder, time.time() + delay)

    def _send_reminder(self, target, text):
        msg = ircmsgs.action(target, text)
        # NOTE: schedule callbacks don't have direct irc context,
        # so we use the global irc object from closure
        self.irc.queueMsg(msg)

    # -------------------------
    # USER COMMAND
    # -------------------------
    def remindme(self, irc, msg, args, text):
        """
        <time><s|m|h> <message>

        Example:
        @remindme 15m work on task A
        """

        nick = msg.nick

        match = re.match(r'(\d+)([smh])\s+(.*)', text.strip())
        if not match:
            irc.reply("Usage: @remindme <time><s|m|h> <message>")
            return

        amount, unit, message = match.groups()
        amount = int(amount)

        if unit == 's':
            delay = amount
        elif unit == 'm':
            delay = amount * 60
        elif unit == 'h':
            delay = amount * 3600
        else:
            irc.reply("Invalid time unit (use s, m, or h).")
            return

        execute_at = int(time.time() + delay)
        target = msg.args[0]

        # Insert safely (NO SQL injection possible)
        with self._db_lock:
            with self._get_conn() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO reminders (nick, target, message, execute_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (nick, target, message, execute_at)
                )
                rid = cur.lastrowid
                conn.commit()

        self._schedule_reminder(rid, nick, target, message, delay)

        irc.reply(f"Okay {nick}, I’ll remind you in {amount}{unit}.")


Class = RemindMe
