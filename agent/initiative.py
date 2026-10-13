"""Opt-in conversational check-ins; scheduling state contains no conversation text."""
from contextlib import closing
from datetime import datetime
import json
import sqlite3
import time

DEFAULTS = {"enabled": False, "interval_minutes": 120, "quiet_start": 22, "quiet_end": 8}


class Initiative:
    def __init__(self, database):
        self.database = database
        with closing(sqlite3.connect(database)) as db:
            row = db.execute("SELECT value FROM settings WHERE name = 'initiative'").fetchone()
        self.data = {**DEFAULTS, "last_attempt": 0, "last_interaction": time.time(), "last_user_id": 0}
        if row:
            self.data.update(json.loads(row[0]))

    def persist(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT OR REPLACE INTO settings(name,value) VALUES ('initiative', ?)",
                       (json.dumps(self.data),))

    def settings(self):
        return {key: self.data[key] for key in DEFAULTS}

    def configure(self, payload):
        if not isinstance(payload, dict) or type(payload.get('enabled')) is not bool:
            raise ValueError('Choose whether check-ins are enabled.')
        if type(payload.get('interval_minutes')) is not int or payload['interval_minutes'] not in (15, 30, 60, 120, 240):
            raise ValueError('Choose a supported interval.')
        for key in ('quiet_start', 'quiet_end'):
            if type(payload.get(key)) is not int or not 0 <= payload[key] <= 23:
                raise ValueError('Quiet hours must be between 0 and 23.')
        self.data.update({key: payload[key] for key in DEFAULTS})
        # Enabling starts a new waiting period; no surprise immediate call.
        self.data['last_interaction'] = time.time()
        self.persist()

    def interacted(self, now=None):
        self.data['last_interaction'] = time.time() if now is None else now
        self.persist()

    def status(self, user_id, *, now=None, hour=None, manual=False):
        now = time.time() if now is None else now
        if now - self.data['last_attempt'] < 60:
            return "Waiting a minute after the last check-in request."
        if manual:
            return "Ready for a manual check-in."
        if not self.data['enabled']:
            return "Check-ins paused."
        if not user_id:
            return "Send a chat message first so Veyra has something to follow up on."
        if user_id <= self.data['last_user_id']:
            return "Waiting for your reply to the last check-in."
        hour = datetime.fromtimestamp(now).hour if hour is None else hour
        start, end = self.data['quiet_start'], self.data['quiet_end']
        quiet = (start <= hour < end) if start < end else (hour >= start or hour < end) if start > end else False
        if quiet:
            return f"Quiet hours on Veyra’s computer; end at {end:02d}:00."
        remaining = self.data['interval_minutes'] * 60 - (now - max(self.data['last_attempt'], self.data['last_interaction']))
        if remaining > 0:
            return f"Next check-in eligible in about {max(1, int((remaining + 59) // 60))} minute(s) of inactivity."
        return "Ready for an automatic check-in while Chat is visible and idle."

    def due(self, user_id, *, now=None, hour=None, manual=False):
        return self.status(user_id, now=now, hour=hour, manual=manual).startswith('Ready for')

    def reserve(self, user_id, now=None, *, delivered=True):
        self.data['last_attempt'] = time.time() if now is None else now
        if delivered:
            self.data['last_user_id'] = user_id
        self.persist()

    def delivered(self, user_id):
        self.data['last_user_id'] = user_id
        self.persist()
