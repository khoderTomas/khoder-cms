#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sladění pracovního checkoutu editoru s větví v gitu (Khoder CMS). Jen stdlib, Python 3.11+.

  gitsync.py [--repo R] sync [--remote origin] [--branch main] [--wait S] [--restart-prefix P …]

Checkout, ve kterém běží editor, se nikdy neresetuje natvrdo: leží v něm neodeslané úpravy.
Posouvá se jen dopředu (merge --ff-only) a jen pod zámkem, který drží i publikace z editoru.

Zámek je soubor <git-common-dir>/cms-publish.lock (flock, na Windows msvcrt). Publikace
i sync ho najdou z repa samy, takže se nepotkají ani bez nastavení: kdo přijde druhý, počká.

Stavební kameny publikace (postup skládá editor webu):
- Git.clean: obsah souborů tak, jak by je uložil git add (konce řádků podle nastavení repa).
- merge_file: třícestné sloučení přes git merge-file, vrací počet konfliktů.
- merge: totéž, u .json při konfliktu ještě sloučení po cestách (jsonsource.merge3).
- Worktree: dočasný worktree z commitu mimo checkout, po použití se smaže.
- Git.push: "ok", nebo "behind", když větev na remote mezitím ujela.
- advance: posun checkoutu na commit. Rozpracovaný soubor, který commit mění, posun zastaví,
  pokud není mezi vlastními (owned, třeba právě publikované) a nemá už obsah z commitu.

sync = zámek, fetch a advance na remote/branch. Návratové kódy:
  0   beze změny, nebo posunuto bez souboru pod --restart-prefix
  10  posunuto a změnil se soubor pod některou --restart-prefix (služba chce restart)
  3   rozpracované úpravy v checkoutu se překrývají se změnami ve větvi, nic se nezměnilo
  1   chyba (síť, zámek, checkout mimo větev)
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LOCK_NAME = "cms-publish.lock"
EXIT_RESTART, EXIT_BLOCKED = 10, 3


class GitError(Exception):
    pass


class Busy(GitError):
    pass


def _tail(text, n=4):
    lines = [x for x in (text or "").strip().splitlines() if x.strip()]
    return "\n".join(lines[-n:])


# ── zámek ──
if os.name == "nt":
    import msvcrt

    def _try_lock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class Lock:
    """Výhradní zámek nad souborem. Čeká nejvýš wait sekund, pak Busy."""

    def __init__(self, path, wait=600.0, who="sync"):
        self.path, self.wait, self.who = Path(path), wait, who
        self.f = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+b")
        end = time.monotonic() + self.wait
        while True:
            try:
                _try_lock(f)
                break
            except OSError:
                if time.monotonic() >= end:
                    f.close()
                    raise Busy("Checkout drží jiná publikace nebo sync déle než %d s (%s)." % (self.wait, self.path))
                time.sleep(0.2)
        self.f = f
        return self

    def __exit__(self, *exc):
        try:
            _unlock(self.f)
        finally:
            self.f.close()
            self.f = None


# ── git ──
class Git:
    def __init__(self, path):
        self.path = Path(path)

    def run(self, *args, input=None, check=True, timeout=300):
        try:
            p = subprocess.run(["git", "-C", str(self.path), *args], input=input, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise GitError("git %s selhal: %s" % (args[0], e))
        if check and p.returncode:
            raise GitError("git %s selhal: %s" % (args[0], _tail(p.stderr.decode("utf-8", "replace"))))
        return p

    def out(self, *args, **kw):
        return self.run(*args, **kw).stdout.decode("utf-8", "replace").strip()

    def rev(self, ref):
        p = self.run("rev-parse", "--verify", "-q", ref + "^{commit}", check=False)
        return p.stdout.decode().strip() if p.returncode == 0 else None

    def branch(self):
        p = self.run("symbolic-ref", "-q", "--short", "HEAD", check=False)
        return p.stdout.decode("utf-8", "replace").strip() if p.returncode == 0 else None

    def is_ancestor(self, a, b):
        p = self.run("merge-base", "--is-ancestor", a, b, check=False)
        if p.returncode not in (0, 1):
            raise GitError("git merge-base selhal: %s" % _tail(p.stderr.decode("utf-8", "replace")))
        return p.returncode == 0

    def common_dir(self):
        d = Path(self.out("rev-parse", "--git-common-dir"))
        return d if d.is_absolute() else (self.path / d).resolve()

    def lock_path(self):
        return self.common_dir() / LOCK_NAME

    def fetch(self, remote, branch):
        self.run("fetch", "-q", remote, "+refs/heads/%s:refs/remotes/%s/%s" % (branch, remote, branch), timeout=180)
        return self.rev("refs/remotes/%s/%s" % (remote, branch))

    def blobs(self, specs):
        """{"rev:cesta": bajty, None = v tom commitu není} jedním git cat-file, bez převodu konců řádků."""
        specs = list(dict.fromkeys(specs))
        if not specs:
            return {}
        data = self.run("cat-file", "--batch", input="".join(s + "\n" for s in specs).encode("utf-8")).stdout
        out, pos = {}, 0
        for s in specs:
            nl = data.index(b"\n", pos)
            head = data[pos:nl].split()
            pos = nl + 1
            if len(head) == 3 and head[1] == b"blob":
                size = int(head[2])
                out[s] = data[pos:pos + size]
                pos += size + 1
            else:
                out[s] = None
        return out

    def clean(self, paths):
        """{cesta: obsah souboru v checkoutu tak, jak by ho uložil git add}, None = soubor chybí."""
        have = [p for p in dict.fromkeys(paths) if (self.path / p).is_file()]
        out = dict.fromkeys(paths)
        if have:
            ids = self.out("hash-object", "-w", "--stdin-paths", input="".join(p + "\n" for p in have).encode("utf-8"))
            blobs = self.blobs(ids.split())
            out.update({p: blobs[i] for p, i in zip(have, ids.split())})
        return out

    def clean_bytes(self, path, data):
        """Bajty tak, jak by je git add uložil pod cestou path (data mimo checkout, třeba z journalu)."""
        oid = self.out("hash-object", "-w", "--stdin", "--path", path, input=data)
        return self.blobs([oid])[oid]

    def changed(self, a, b):
        raw = self.run("diff", "--name-only", "-z", "--no-renames", a, b).stdout
        return [x.decode("utf-8") for x in raw.split(b"\0") if x]

    def dirty(self):
        """Cesty změněné proti HEAD v indexu nebo ve stromu, včetně nesledovaných."""
        raw = self.run("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout.split(b"\0")
        out, i = set(), 0
        while i < len(raw):
            rec = raw[i]
            i += 1
            if len(rec) < 4:
                continue
            out.add(rec[3:].decode("utf-8"))
            if b"R" in rec[:2] or b"C" in rec[:2]:      # přejmenování nese i původní cestu
                out.add(raw[i].decode("utf-8"))
                i += 1
        return out

    def log(self, a, b, paths=(), limit=10):
        """(počet commitů a..b, prvních limit řádků „%h %s (%an)“)."""
        rng = "%s..%s" % (a, b)
        tail = ["--", *paths] if paths else []
        count = int(self.out("rev-list", "--count", rng, *tail) or 0)
        lines = self.out("log", "--format=%h %s (%an)", "-n", str(limit), rng, *tail).splitlines() if count else []
        return count, lines

    def log_detail(self, a, b, paths=(), limit=10):
        """Commity a..b od nejnovějšího: [{sha, subject, author, time (ISO 8601), coauthors}].
        coauthors = jména z trailerů Co-authored-by (commit agenta, třeba Claude Code)."""
        fmt = "%h%x1f%s%x1f%an%x1f%cI%x1f%(trailers:key=Co-authored-by,valueonly,separator=%x1d)%x1e"
        tail = ["--", *paths] if paths else []
        raw = self.out("log", "--format=" + fmt, "-n", str(limit), "%s..%s" % (a, b), *tail)
        out = []
        for rec in raw.split("\x1e"):
            parts = rec.strip("\n").split("\x1f")
            if len(parts) != 5:
                continue
            sha, subject, author, when, co = parts
            out.append({"sha": sha, "subject": subject, "author": author, "time": when,
                        "coauthors": [x.split("<")[0].strip() for x in co.split("\x1d") if x.strip()]})
        return out

    def push(self, remote, branch):
        """"ok", nebo "behind", když větev na remote mezitím ujela (fetch, sloučit a zkusit znovu)."""
        before = self.rev("refs/remotes/%s/%s" % (remote, branch))
        p = self.run("push", remote, "HEAD:refs/heads/" + branch, check=False, timeout=180)
        if p.returncode == 0:
            return "ok"
        err = GitError("git push selhal: %s" % _tail(p.stderr.decode("utf-8", "replace")))
        try:
            now = self.fetch(remote, branch)
        except GitError:
            raise err from None
        if now != before and not self.is_ancestor(now, "HEAD"):
            return "behind"
        raise err


def merge_file(ours, base, theirs):
    """(sloučené bajty, počet konfliktů) z git merge-file. base None = soubor v základu nebyl."""
    if ours == theirs or theirs == base:
        return ours, 0
    if ours == base:
        return theirs, 0
    with tempfile.TemporaryDirectory(prefix="cms-merge-") as d:
        names = []
        for n, data in (("ours", ours), ("base", base or b""), ("theirs", theirs)):
            f = os.path.join(d, n)
            with open(f, "wb") as fh:
                fh.write(data)
            names.append(f)
        p = subprocess.run(["git", "merge-file", "-p", "--diff3", "-L", "editor", "-L", "base", "-L", "branch", *names],
                           capture_output=True, timeout=60)
    if p.returncode > 127:
        raise GitError("git merge-file selhal: %s" % _tail(p.stderr.decode("utf-8", "replace")))
    return p.stdout, p.returncode


def merge(path, ours, base, theirs):
    """merge_file; u datového souboru .json, kde git merge-file po řádcích hlásí konflikt (dvě
    hodnoty na jednom řádku), ještě sloučení po cestách (jsonsource.merge3: změněné hodnoty ours
    se zapíšou do theirs, když se cesty nepřekrývají)."""
    data, n = merge_file(ours, base, theirs)
    if n and base is not None and path.endswith(".json"):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import jsonsource
        try:
            text = jsonsource.merge3(ours.decode("utf-8"), base.decode("utf-8"), theirs.decode("utf-8"))
        except UnicodeDecodeError:
            text = None
        if text is not None:
            return text.encode("utf-8"), 0
    return data, n


class Worktree:
    """Dočasný worktree z commitu (bez větve) mimo checkout. Po použití se smaže i s registrací."""

    def __init__(self, git, commit, base=None):
        self.repo, self.commit, self.base = git, commit, base
        self.tmp = self.path = self.git = None

    def __enter__(self):
        self.repo.run("worktree", "prune", check=False)
        if self.base:
            Path(self.base).mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="cms-publish-", dir=self.base))
        self.path = self.tmp / "w"
        try:
            self.repo.run("worktree", "add", "-q", "--detach", str(self.path), self.commit)
        except GitError:
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise
        self.git = Git(self.path)
        return self

    def __exit__(self, *exc):
        self.repo.run("worktree", "remove", "--force", str(self.path), check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.repo.run("worktree", "prune", check=False)


def advance(git, target, owned=()):
    """Posune checkout na target (jen dopředu). Vrací {"moved", "from", "to", "changed", "blocked"}.

    Rozpracované soubory, které target mění, posun zastaví („blocked“, nic se nezmění). Výjimkou
    jsou cesty z owned a soubory, které už mají přesně obsah z targetu: ty se nejdřív vrátí
    na HEAD (merge --ff-only by je jinak odmítl) a posun je přepíše verzí z targetu."""
    head = git.rev("HEAD")
    res = {"moved": False, "from": head, "to": target, "changed": [], "blocked": []}
    if head == target:
        return res
    if not git.is_ancestor(head, target):
        raise GitError("Checkout (%s) není předkem %s, posunout ho jde jen dopředu." % (head[:12], target[:12]))
    res["changed"] = changed = git.changed(head, target)
    dirty = git.dirty()
    over = [p for p in changed if p in dirty]
    owned = set(owned)
    check = [p for p in over if p not in owned]
    if check:
        want = git.blobs(["%s:%s" % (target, p) for p in check])
        cur = git.clean(check)
        res["blocked"] = [p for p in check if cur[p] != want["%s:%s" % (target, p)]]
        if res["blocked"]:
            return res
    if over:
        inhead = git.blobs(["%s:%s" % (head, p) for p in over])
        tracked = [p for p in over if inhead["%s:%s" % (head, p)] is not None]
        if tracked:
            git.run("checkout", head, "--", *tracked)
        for p in over:
            if p not in tracked and (git.path / p).exists():
                (git.path / p).unlink()
    git.run("merge", "--ff-only", "-q", target)
    res["moved"] = True
    return res


def sync(git, remote="origin", branch="main", wait=600.0):
    """Pod zámkem: fetch a posun checkoutu na remote/branch. Checkout musí být na branch."""
    with Lock(git.lock_path(), wait):
        cur = git.branch()
        if cur != branch:
            raise GitError("Checkout je na %s, ne na %s; posouvá se jen %s." % (cur or "odpojeném HEAD", branch, branch))
        target = git.fetch(remote, branch)
        if target is None:
            raise GitError("Na %s chybí větev %s." % (remote, branch))
        return advance(git, target)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:                                   # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="Posun checkoutu editoru na větev v gitu (jen dopředu, pod zámkem).")
    ap.add_argument("--repo", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="fetch a merge --ff-only pod zámkem publikace")
    s.add_argument("--remote", default="origin")
    s.add_argument("--branch", default="main")
    s.add_argument("--wait", type=float, default=600.0, help="jak dlouho čekat na běžící publikaci (s)")
    s.add_argument("--restart-prefix", action="append", default=[],
                   help="změna souboru pod touhle cestou vrátí kód 10 (restart služby)")
    args = ap.parse_args(argv)
    git = Git(args.repo)
    try:
        r = sync(git, args.remote, args.branch, args.wait)
    except GitError as e:
        print("sync: %s" % e)
        return 1
    if r["blocked"]:
        print("sync: checkout zůstal na %s. Neodeslané úpravy se překrývají se změnami v %s/%s: %s"
              % (r["from"][:12], args.remote, args.branch, ", ".join(r["blocked"][:10])))
        return EXIT_BLOCKED
    if not r["moved"]:
        print("sync: checkout je aktuální (%s)" % r["to"][:12])
        return 0
    print("sync: %s..%s, změněno %d souborů" % (r["from"][:12], r["to"][:12], len(r["changed"])))
    hit = [p for p in r["changed"] if any(p.startswith(x) for x in args.restart_prefix)]
    return EXIT_RESTART if hit else 0


if __name__ == "__main__":
    sys.exit(main())
