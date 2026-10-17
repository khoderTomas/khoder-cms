# -*- coding: utf-8 -*-
"""Přihlášení do samostatného editoru (editor.py): soubor uživatelů, hash hesla, brzda a sezení.

Soubor uživatelů (CMS_USERS), JSON s právy 600:
  {"jana": {"heslo": "pbkdf2_sha256$600000$<sůl>$<hash>", "role": "editor",
            "jmeno": "Jana Nováková", "email": "jana@example.cz"}}
Role admin smí v editoru navíc celý zdroj stránky a odemknout owner stránku, editor všechno ostatní
včetně Publikovat. Jméno a e-mail jsou autor commitu publikace. Uživatele přidá, heslo změní nebo
uživatele smaže `python3 cms/editor.py user <login>` (save_user, delete_user). Editor soubor čte
znovu po každé změně, restart netřeba.

Hash: PBKDF2-SHA256 (ITERATIONS iterací, sůl 16 bajtů). Hesla se ověřují po jednom, zkoušení hesel
tak vytíží nejvýš jedno jádro. Neznámé jméno se počítá stejně dlouho jako známé, odpověď neprozradí,
kdo existuje.

Brzda: po limit špatných heslech z jedné adresy během block sekund je adresa block sekund
zablokovaná. Úspěšné přihlášení počítadlo adresy smaže.

Sezení: po přihlášení dostane prohlížeč cookie COOKIE s náhodným tokenem. V databázi (SQLite
CMS_SESSIONS, bez ní paměť procesu: restart odhlásí) leží jen otisk tokenu, login a otisk hashe
hesla, takže změna hesla nebo smazání uživatele sezení zneplatní. Platnost VALID_S od posledního
otevření editoru, posouvá se nejvýš jednou denně.
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

ITERATIONS = 600_000
MIN_LEN = 12
ROLES = ("admin", "editor")
LOGIN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
EMAIL_RE = re.compile(r"[^<>\s@]+@[^<>\s@]+")
COOKIE = "__Host-cms"
VALID_S = 30 * 24 * 3600


def _b64(b):
    return base64.b64encode(b).decode("ascii")


def hash_password(password, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, _b64(salt), _b64(dk))


def _parse_hash(pw_hash):
    try:
        algo, it, salt, dk = (pw_hash or "").split("$")
        if algo != "pbkdf2_sha256":
            raise ValueError
        return int(it), base64.b64decode(salt, validate=True), base64.b64decode(dk, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("čekám pbkdf2_sha256$<iterace>$<sůl>$<hash>") from None


def _check_user(login, u):
    """(záznam pro Users, nebo ValueError s důvodem) jednoho uživatele ze souboru."""
    if not isinstance(login, str) or not LOGIN_RE.fullmatch(login):
        raise ValueError("neplatné jméno uživatele %r (malá písmena, číslice, . _ -)" % login)
    if not isinstance(u, dict):
        raise ValueError("uživatel %r: čekám objekt s heslem a rolí" % login)
    if u.get("role") not in ROLES:
        raise ValueError("uživatel %r: role musí být %s" % (login, " nebo ".join(ROLES)))
    try:
        it, salt, dk = _parse_hash(u.get("heslo"))
    except ValueError as e:
        raise ValueError("uživatel %r: %s" % (login, e)) from None
    email = u.get("email")
    if email is not None and not (isinstance(email, str) and EMAIL_RE.fullmatch(email)):
        raise ValueError("uživatel %r: neplatný e-mail %r" % (login, email))
    return {"iterations": it, "salt": salt, "hash": dk, "raw": u["heslo"],
            "info": {"login": login, "role": u["role"], "name": u.get("jmeno") or None, "email": email or None}}


class Users:
    """Uživatelé ze souboru: Users(cesta). Soubor se čte znovu, když se změní (otisk času a velikosti);
    rozbitý soubor nechá platit poslední dobrý stav a chybu vrátí v error."""

    def __init__(self, path):
        self.path = Path(path)
        self._key = None
        self._users = {}
        self.error = None
        self._lock = threading.Lock()
        self._verify = threading.Lock()
        self._load()
        if self.error:
            raise ValueError(self.error)

    def _load(self):
        try:
            st = self.path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError as e:
            self.error = "soubor uživatelů %s nejde přečíst: %s" % (self.path, e)
            return
        if key == self._key:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data:
                raise ValueError("čekám neprázdný JSON objekt {login: {heslo, role, jmeno, email}}")
            users = {login: _check_user(login, u) for login, u in data.items()}
        except (OSError, ValueError) as e:
            self.error = "soubor uživatelů %s: %s" % (self.path, e)
            return
        self._key, self._users, self.error = key, users, None

    def _get(self, login):
        with self._lock:
            self._load()
            return self._users.get(login)

    def check(self, login, password):
        """{login, role, name, email} pro správné jméno a heslo, jinak None."""
        if not login or not password:
            return None
        u = self._get(login)
        it, salt, want = (u["iterations"], u["salt"], u["hash"]) if u else (ITERATIONS, b"\0" * 16, b"\0" * 32)
        with self._verify:
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, it)
        if not (hmac.compare_digest(dk, want) and u):
            return None
        return dict(u["info"])

    def info(self, login):
        u = self._get(login)
        return dict(u["info"]) if u else None

    def password_hash(self, login):
        """Dnešní hash hesla (sezení se změnou hesla zneplatní), None = uživatel neexistuje."""
        u = self._get(login)
        return u["raw"] if u else None


def read_raw(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("soubor uživatelů %s: čekám JSON objekt" % path)
    return data


def _write_raw(path, data):
    """Zápis přes dočasný soubor s právy 600 a přejmenování: editor nikdy nepřečte půlku souboru."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".users-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_user(path, login, password=None, role=None, name=None, email=None):
    """Založí uživatele, nebo změní jeho heslo, roli, jméno či e-mail (None = beze změny).
    Nový uživatel potřebuje heslo, jméno a e-mail (autor commitu publikace). Vrací True = nový."""
    data = read_raw(path)
    new = login not in data
    u = dict(data.get(login) or {})
    if password is not None:
        if len(password) < MIN_LEN:
            raise ValueError("heslo musí mít aspoň %d znaků" % MIN_LEN)
        u["heslo"] = hash_password(password)
    if role is not None:
        u["role"] = role
    if name is not None:
        u["jmeno"] = name.strip()
    if email is not None:
        u["email"] = email.strip()
    if new:
        u.setdefault("role", "editor")
        need = {"heslo": "heslo", "jmeno": "jméno (--name)", "email": "e-mail (--email)"}
        missing = [what for k, what in need.items() if not u.get(k)]
        if missing:
            raise ValueError("nový uživatel potřebuje %s" % ", ".join(missing))
    _check_user(login, u)
    data[login] = u
    _write_raw(path, data)
    return new


def delete_user(path, login):
    """Smaže uživatele (jeho sezení tím zneplatní). Vrací False, když neexistoval."""
    data = read_raw(path)
    if login not in data:
        return False
    if len(data) == 1:
        raise ValueError("%s je poslední uživatel, soubor by zůstal prázdný a editor by nenastartoval" % login)
    del data[login]
    _write_raw(path, data)
    return True


class Brake:
    """Brzda proti hádání hesla: po limit špatných pokusech z adresy během block sekund je adresa
    block sekund zablokovaná. Úspěšné přihlášení počítadlo adresy smaže."""

    def __init__(self, limit=5, block=15 * 60, clock=time.monotonic):
        self.limit, self.block, self.clock = limit, block, clock
        self._fails, self._until = {}, {}
        self._lock = threading.Lock()

    def blocked(self, ip):
        """Kolik sekund ještě blokuje (0 = neblokuje)."""
        with self._lock:
            left = self._until.get(ip, 0) - self.clock()
            if left <= 0 and ip in self._until:
                del self._until[ip]
            return max(0, int(left + 0.999))

    def fail(self, ip):
        now = self.clock()
        with self._lock:
            ts = [t for t in self._fails.get(ip, []) if now - t < self.block] + [now]
            if len(ts) >= self.limit:
                self._until[ip] = now + self.block
                ts = []
            self._fails[ip] = ts
            if len(self._fails) > 10000:                    # paměť drží jen čerstvé adresy
                self._fails = {k: v for k, v in self._fails.items() if v and now - v[-1] < self.block}

    def ok(self, ip):
        with self._lock:
            self._fails.pop(ip, None)


# ── sezení ──
SCHEMA = """CREATE TABLE IF NOT EXISTS sessions (
  digest TEXT PRIMARY KEY,
  login TEXT NOT NULL,
  pw TEXT NOT NULL,
  created INTEGER NOT NULL,
  moved INTEGER NOT NULL,
  valid_until INTEGER NOT NULL
)"""


def digest(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def token_from(header):
    """Hodnota cookie COOKIE z hlavičky Cookie, nebo None."""
    for part in (header or "").split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k == COOKIE and v:
            return v
    return None


def set_cookie(token, max_age=VALID_S):
    return "%s=%s; Path=/; Max-Age=%d; Secure; HttpOnly; SameSite=Lax" % (COOKIE, token, max_age)


def clear_cookie():
    return set_cookie("", 0)


class Sessions:
    """db = cesta k SQLite (CMS_SESSIONS), nebo None = paměť procesu. clock jde injektovat kvůli testům."""

    def __init__(self, db=None, clock=time.time):
        self.clock = clock
        self._db = str(db) if db else None
        self._lock = threading.Lock()
        self._mem = None if self._db else sqlite3.connect(":memory:", check_same_thread=False)
        with self._con() as con:
            con.execute(SCHEMA)
        if self._db:
            os.chmod(self._db, 0o600)

    class _Con:
        def __init__(self, owner):
            self.owner = owner

        def __enter__(self):
            o = self.owner
            if o._mem is not None:
                o._lock.acquire()
                self.con = o._mem
            else:
                self.con = sqlite3.connect(o._db, timeout=5)
            return self.con

        def __exit__(self, exc, *_):
            o = self.owner
            try:
                if exc is None:
                    self.con.commit()
                else:
                    self.con.rollback()
            finally:
                if o._mem is not None:
                    o._lock.release()
                else:
                    self.con.close()

    def _con(self):
        return self._Con(self)

    def create(self, login, pw_hash):
        """Nové sezení, vrací token do cookie. Prošlá sezení se při tom smažou."""
        now = int(self.clock())
        token = secrets.token_urlsafe(32)
        with self._con() as con:
            con.execute("DELETE FROM sessions WHERE valid_until < ?", (now,))
            con.execute("INSERT INTO sessions (digest, login, pw, created, moved, valid_until) VALUES (?, ?, ?, ?, ?, ?)",
                        (digest(token), login, digest(pw_hash), now, now, now + VALID_S))
        return token

    def check(self, token, pw_hash_for, refresh=False):
        """(login, posunuto) pro platný token, jinak (None, False). pw_hash_for(login) = dnešní hash
        hesla, nebo None, když uživatel už neexistuje (sezení se pak smaže)."""
        if not token or len(token) > 100:
            return None, False
        now = int(self.clock())
        key = digest(token)
        with self._con() as con:
            row = con.execute("SELECT login, pw, moved, valid_until FROM sessions WHERE digest = ?", (key,)).fetchone()
            if not row:
                return None, False
            login, pw, moved, until = row
            current = pw_hash_for(login)
            if until < now or current is None or digest(current) != pw:
                con.execute("DELETE FROM sessions WHERE digest = ?", (key,))
                return None, False
            if refresh and time.strftime("%Y-%m-%d", time.localtime(moved)) != time.strftime("%Y-%m-%d", time.localtime(now)):
                con.execute("UPDATE sessions SET moved = ?, valid_until = ? WHERE digest = ?", (now, now + VALID_S, key))
                return login, True
        return login, False

    def close(self):
        if self._mem is not None:
            self._mem.close()

    def drop(self, token):
        if token:
            with self._con() as con:
                con.execute("DELETE FROM sessions WHERE digest = ?", (digest(token),))

    def count(self, login=None):
        now = int(self.clock())
        with self._con() as con:
            if login:
                return con.execute("SELECT COUNT(*) FROM sessions WHERE valid_until >= ? AND login = ?",
                                   (now, login)).fetchone()[0]
            return con.execute("SELECT COUNT(*) FROM sessions WHERE valid_until >= ?", (now,)).fetchone()[0]
