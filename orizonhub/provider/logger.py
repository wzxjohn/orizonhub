#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import json
import logging
import sqlite3
import collections
from datetime import datetime
from logging.handlers import WatchedFileHandler

from ..model import User, Logger
from .sqlitedict import SqliteMultithread

class TextLogger(Logger):
    '''Logs messages with plain text. Rotating-friendly.'''
    FORMAT = '%(asctime)s [%(protocol)s:%(pid)d] %(srcname)s >> %(text)s'

    def __init__(self, filename, tz):
        self.logger = logging.getLogger('chatlog')
        self.logger.setLevel(logging.INFO)
        self.loghandler = WatchedFileHandler(filename, encoding='utf-8', delay=True)
        self.loghandler.setLevel(logging.INFO)
        self.loghandler.setFormatter(logging.Formatter('%(message)s'))
        self.logger.addHandler(self.loghandler)
        self.tz = tz

    def log(self, msg):
        d = msg._asdict()
        d['asctime'] = datetime.fromtimestamp(msg.time, self.tz).strftime('%Y-%m-%d %H:%M:%S')
        d['srcname'] = msg.src.alias
        d['srcid'] = msg.src.id
        self.logger.info(self.FORMAT % d)

class SQLiteLogger(Logger):
    '''Logs messages with SQLite.'''
    SCHEMA = (
        'CREATE TABLE IF NOT EXISTS messages ('
            'id INTEGER PRIMARY KEY,'
            'protocol TEXT NOT NULL,'
            'pid INTEGER,'
            'src INTEGER,'
            'text TEXT,'
            'media TEXT,'
            'time INTEGER,'
            'fwd_src INTEGER,'
            'fwd_date INTEGER,'
            'reply_id INTEGER,'
            'FOREIGN KEY(src) REFERENCES users(id)'
        ')',
        'CREATE TABLE IF NOT EXISTS user ('
            'id INTEGER PRIMARY KEY,'
            'protocol TEXT NOT NULL,'
            'pid INTEGER,'  # protocol-specified id
            'username TEXT,'
            'first_name TEXT,'
            'last_name TEXT,'
            'alias TEXT NOT NULL'
        ')',
    )

    def __init__(self, filename, tz):
        self.conn = SqliteMultithread(filename)
        for c in self.SCHEMA:
            self.conn.execute(c)
        self.conn.commit()
        self.user_cache = {}
        for row in self.conn.select('SELECT * FROM user'):
            u = User._make(row)
            self.user_cache[u._key()] = u

    def log(self, msg):
        src = self.update_user(msg.src)
        self.conn.execute('INSERT INTO messages (protocol, pid, src, text, media, time, fwd_src, fwd_date, reply_id) VALUES (?,?,?,?,?,?,?,?,?)' % (msg.protocol, msg.pid, src, msg.text, json.dumps(msg.media) if msg.media else None, msg.time, msg.fwd_src, msg.fwd_date, msg.reply.pid if msg.reply else None))

    def update_user(self, user):
        '''
        Update user in database if necessary, returns a User with `id` set.

        Consider these situations:
                      In cache        Not in cache
        Known ID       Check         Cache & Update
        Unknown    Get ID & Check   Check, Update/New
        '''
        def _get_user_id(uk):
            res = self.conn.select_one('SELECT id FROM user WHERE %s=? AND %s=?' % uk._fields, uk)
            if res:
                return res[0]

        def _update_user(uk, user):
            self.conn.execute('UPDATE user SET protocol=?, username=?, first_name=?, last_name=?, alias=? WHERE id=?' % (user.protocol, user.username, user.first_name, user.last_name, user.alias, user.id))
            self.user_cache[uk] = user

        def _new_user(uk, user):
            res = self.conn.change_one('INSERT INTO user (protocol, pid, username, first_name, last_name, alias) VALUES (?,?,?,?,?,?)' % user[1:])
            self.user_cache[uk] = newuser = User(res[1], *user[1:])
            return newuser

        uk = user._key()
        cached = self.user_cache.get(uk)
        ret = user

        if user.id is None:
            # Cache hit, check and update
            if cached:
                ret = User(cached.id, *user[1:])
                if cached != ret:
                    _update_user(uk, ret)
            # Cache miss, get id or create new
            else:
                uid = _get_user_id(uk)
                if uid is None:
                    ret = _new_user(uk, user)
        elif cached != user:
            _update_user(uk, user)
        return ret

    def select(self, req, arg=None):
        return self.conn.select(req, arg)

    def commit(self, blocking=True):
        self.conn.commit(blocking)

    def close(self):
        self.conn.commit()
        self.conn.close()

class BasicStateStore(collections.UserDict):
    def __init__(self, filename):
        if os.path.isfile(filename):
            data = json.load(open(self.filename, 'r', encoding='utf-8'))
            super().__init__(data)
        self.filename = filename

    def commit(self):
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, sort_keys=True, indent=4)

    def close(self):
        self.commit()

class SQLiteStateStore(BasicStateStore):
    def __init__(self, connection):
        self.conn = connection
        self.conn.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)')
        self.conn.commit()
        data = dict(self.conn.select('SELECT key, value FROM state'))
        super(BasicStateStore, self).__init__(data)

    def commit(self):
        for k, v in self.data:
            self.conn.execute('REPLACE INTO state (key, value) VALUES (?,?)', (key, str(value)))
        self.conn.commit()

    def close(self):
        self.commit()

