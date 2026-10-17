# -*- coding: utf-8 -*-
"""Zámek buildu: build webu z CLI, editor textů a další nástroje nesmí zapisovat
do stránek a zdrojů generátoru současně.

  with buildlock.hold("build_all"):
      ...

Zámek je soubor .build.lock vedle tohoto modulu (PID, kdo, kdy). Mrtvý zámek (proces už
neběží nebo je starší než STALE_S) se převezme. Na Windows se živost procesu
ověřuje přes OpenProcess: os.kill(pid, 0) by tam proces ukončil.
"""
import contextlib
import json
import os
import sys
import time

LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".build.lock")
STALE_S = 15 * 60


class Busy(Exception):
    pass


def _alive(pid):
    if pid == os.getpid():
        return True
    if pid <= 0:
        return False                                # os.kill(0, …) by mířil na celou skupinu procesů
    if sys.platform == "win32":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259         # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True                                 # proces žije, jen patří jinému uživateli
    except OSError:
        return False
    return True


def holder():
    """Kdo zámek drží, nebo None."""
    try:
        with open(LOCK, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None
    if not _alive(int(info.get("pid", 0))) or time.time() - info.get("t", 0) > STALE_S:
        return None
    return info


def acquire(who, wait=60.0):
    end = time.time() + wait
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if holder() is None:
                try:
                    os.remove(LOCK)                   # mrtvý zámek
                except OSError:
                    pass
                continue
            if time.time() > end:
                raise Busy("Build právě běží jinde (%s)." % holder().get("who", "?"))
            time.sleep(0.2)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "who": who, "t": time.time()}, f)
        return


def release():
    try:
        with open(LOCK, encoding="utf-8") as f:
            if json.load(f).get("pid") != os.getpid():
                return
    except (OSError, ValueError):
        return
    try:
        os.remove(LOCK)
    except OSError:
        pass


ENV = "CMS_BUILD_LOCK_OWNER"


def child_env():
    """Prostředí pro podproces, který běží pod zámkem tohohle procesu."""
    return dict(os.environ, **{ENV: str(os.getpid())})


@contextlib.contextmanager
def hold(who, wait=60.0):
    h = holder()
    if h and str(h.get("pid")) == os.environ.get(ENV):
        yield                                       # zámek drží rodič (editor při publikaci)
        return
    acquire(who, wait)
    try:
        yield
    finally:
        release()
