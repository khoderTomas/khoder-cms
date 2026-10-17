#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deploy statického webu z commitu v gitu (Khoder CMS). Jen stdlib, Python 3.11+.

  deploy.py [--repo R] [--config C] [--state S] deploy [--sha SHA] [--caller full|cms]
            [--dry-run] [--rollback] [--no-fetch] [--target-path DIR]
  deploy.py [--repo R] [--config C] [--state S] status [--no-fetch] [--json]

Nasadí složku `source` (výchozí dist/) z commitu na větvi `branch` do cíle:
kopie do adresáře (local), rsync, nebo sftp. Nastavení je v cms.toml, sekce [deploy].

Pravidla:
- Soubory jsou bajty blobů z gitu (git cat-file), ne pracovní strom ani checkout:
  ten by mohl převést konce řádků.
- Kontroly před zápisem: HTML jen stránky z manifestu (`pages`), PHP jen z allowlistu,
  dotfiles jen z allowlistu, žádné symlinky ani submoduly. Jedna chyba = nic se nenasadí.
- Odkazy stránek na CSS a JS z téhož exportu dostanou ?v=<hash obsahu>: dokud se soubor
  nezmění, razítko zůstává, takže druhý deploy téhož commitu nezapíše nic.
- Zapisuje jen změněné soubory: nejdřív ne-HTML, pak HTML, nakonec sitemapy. Nic nemaže,
  soubory mimo git (konfigurace s hesly, feedy, staré assety) zůstanou.
- Commit musí být na `remote/branch` a navazovat na naposledy nasazený. Starší commit
  nasadí jen režim full s --rollback.
- Režim cms (vynucený příkaz klíče editoru) nasadí jen změny HTML a sitemap. Když commit
  nese i jiné nenasazené změny, nenasadí nic. Nastavení nečte z commitu, jen z --config.
- IndexNow dostane jen URL ze sitemapy, jejichž obsah se změnil (razítko ?v= se nepočítá).
- Výstup končí řádkem `OK: Deployed version <verze> to <web>`, verze je zkrácený SHA.
"""
import argparse
import contextlib
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

DEPLOY_KEYS = {"source", "branch", "remote", "site", "pages", "pages_field", "php", "dotfiles",
               "file_mode", "state", "target", "indexnow"}
DEFAULTS = {"source": "dist", "branch": "main", "remote": "origin", "site": "", "pages": None,
            "pages_field": "output", "php": [], "dotfiles": [], "file_mode": "644", "state": None}
PHP_EXT = (".php", ".phtml", ".phar", ".pht", ".phps", ".php3", ".php4", ".php5", ".php7", ".php8")
SITEMAP = re.compile(r"sitemap[^/]*\.xml\Z")
STAMP = re.compile(rb'((?<![\w-])(?:href|src)=")([^"?#:]+?\.(?:css|js))(?:\?v=[^"]*)?"')
UNSTAMP = re.compile(rb'((?<![\w-])(?:href|src)="[^"?#:]+?\.(?:css|js))\?v=[^"]*"')
LIST_MAX = 80


class DeployError(Exception):
    pass


def _plural(n, one, few, many):
    return "%d %s" % (n, one if n == 1 else few if 2 <= n <= 4 else many)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _glob(pattern):
    """Glob nad celou cestou: * a ? nepřekročí /, ** ano."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _matches(rel, patterns):
    return any(_glob(p).match(rel) for p in patterns)


def phase(rel):
    """Pořadí zápisu: 0 ne-HTML, 1 HTML, 2 sitemapy (ukazují na stránky, jdou poslední)."""
    if rel.endswith(".html"):
        return 1
    if SITEMAP.match(rel):
        return 2
    return 0


# ── nastavení ──
def load_config(text, origin="cms.toml"):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise DeployError("%s nejde přečíst: %s" % (origin, e))
    d = data.get("deploy")
    if not isinstance(d, dict):
        raise DeployError("%s nemá sekci [deploy]." % origin)
    unknown = sorted(set(d) - DEPLOY_KEYS)
    if unknown:
        raise DeployError("%s: neznámé klíče v [deploy]: %s" % (origin, ", ".join(unknown)))
    cfg = dict(DEFAULTS)
    cfg.update(d)
    cfg["target"] = dict(d.get("target") or {})
    cfg["indexnow"] = d.get("indexnow")
    cfg["source"] = cfg["source"].strip("/")
    cfg["site"] = cfg["site"].rstrip("/")
    try:
        cfg["file_mode"] = int(str(cfg["file_mode"]), 8)
    except ValueError:
        raise DeployError("%s: file_mode musí být osmičkově, třeba \"644\"." % origin)
    return cfg


# ── git ──
class Repo:
    def __init__(self, path):
        self.path = str(path)

    def run(self, *args, input=None, check=True):
        try:
            p = subprocess.run(["git", "-C", self.path, *args], input=input, capture_output=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DeployError("git %s selhal: %s" % (args[0], e))
        if check and p.returncode:
            raise DeployError("git %s selhal: %s" % (args[0], p.stderr.decode("utf-8", "replace").strip()))
        return p

    def out(self, *args):
        return self.run(*args).stdout.decode("utf-8", "replace").strip()

    def fetch(self, remote, branch):
        self.run("fetch", "-q", remote, "+refs/heads/%s:refs/remotes/%s/%s" % (branch, remote, branch))

    def rev(self, ref):
        p = self.run("rev-parse", "--verify", "-q", ref + "^{commit}", check=False)
        if p.returncode:
            return None
        return p.stdout.decode().strip()

    def is_ancestor(self, a, b):
        p = self.run("merge-base", "--is-ancestor", a, b, check=False)
        if p.returncode not in (0, 1):
            raise DeployError("git merge-base selhal: %s" % p.stderr.decode("utf-8", "replace").strip())
        return p.returncode == 0

    def show(self, sha, path):
        p = self.run("show", "%s:%s" % (sha, path), check=False)
        return p.stdout if p.returncode == 0 else None

    def tree(self, sha, source):
        """[(mode, typ, objekt, cesta od kořene repa)] pod source/."""
        raw = self.run("ls-tree", "-r", "-z", "--full-tree", sha, "--", source + "/").stdout
        items = []
        for rec in raw.split(b"\0"):
            if not rec:
                continue
            meta, path = rec.split(b"\t", 1)
            mode, typ, obj = meta.decode().split()
            items.append((mode, typ, obj, path.decode("utf-8")))
        return items

    def blobs(self, ids):
        """{objekt: bajty} jedním git cat-file --batch, bez převodu konců řádků."""
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        data = self.run("cat-file", "--batch", input="".join(i + "\n" for i in ids).encode()).stdout
        out, pos = {}, 0
        for obj in ids:
            nl = data.index(b"\n", pos)
            head = data[pos:nl].split()
            pos = nl + 1
            if len(head) != 3 or head[1] != b"blob":
                raise DeployError("git cat-file nevrátil blob %s." % obj)
            size = int(head[2])
            out[obj] = data[pos:pos + size]
            pos += size + 1
        return out

    def log(self, a, b, limit=40):
        fmt = "%h %s (%an)"
        lines = self.out("log", "--format=" + fmt, "-n", str(limit + 1), "%s..%s" % (a, b)).splitlines()
        count = int(self.out("rev-list", "--count", "%s..%s" % (a, b)) or 0)
        return count, lines[:limit]


# ── export a kontroly ──
def export(repo, sha, cfg):
    """Soubory složky source v commitu: [(cesta v cíli, mode, typ, objekt)]."""
    prefix = cfg["source"] + "/"
    files = []
    for mode, typ, obj, path in repo.tree(sha, cfg["source"]):
        if not path.startswith(prefix):
            continue
        files.append((path[len(prefix):], mode, typ, obj))
    if not files:
        raise DeployError("Commit %s nemá ve složce %s/ žádné soubory." % (sha[:12], cfg["source"]))
    return files


def manifest_pages(repo, sha, cfg):
    """Cesty stránek z manifestu (vůči source/), None když manifest není nastavený."""
    if not cfg["pages"]:
        return None
    raw = repo.show(sha, cfg["pages"])
    if raw is None:
        raise DeployError("Manifest stránek %s v commitu %s chybí." % (cfg["pages"], sha[:12]))
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise DeployError("Manifest stránek %s nejde přečíst: %s" % (cfg["pages"], e))
    items = data.values() if isinstance(data, dict) else data
    prefix = cfg["source"] + "/"
    pages = set()
    for it in items:
        path = it.get(cfg["pages_field"]) if isinstance(it, dict) else it
        if not isinstance(path, str) or not path.startswith(prefix):
            raise DeployError("Manifest %s: položka %r nemá cestu pod %s." % (cfg["pages"], it, prefix))
        pages.add(path[len(prefix):])
    return pages


def check(files, cfg, pages):
    """Seznam důvodů, proč export nejde nasadit (prázdný = v pořádku)."""
    errors = []
    for rel, mode, typ, _ in files:
        parts = PurePosixPath(rel).parts
        if typ != "blob":
            errors.append("%s: submodul, nasazují se jen soubory" % rel)
            continue
        if mode == "120000":
            errors.append("%s: symbolický odkaz" % rel)
            continue
        if rel.startswith("/") or any(p in ("", ".", "..") for p in parts):
            errors.append("%s: neplatná cesta" % rel)
            continue
        if any(p.startswith(".") for p in parts) and not _matches(rel, cfg["dotfiles"]):
            errors.append("%s: skrytý soubor mimo povolené (%s)" % (rel, ", ".join(cfg["dotfiles"]) or "žádné"))
        if rel.lower().endswith(PHP_EXT) and not _matches(rel, cfg["php"]):
            errors.append("%s: PHP mimo povolené (%s)" % (rel, ", ".join(cfg["php"]) or "žádné"))
    if pages is not None:
        html = {rel for rel, *_ in files if rel.endswith(".html")}
        errors += ["%s: stránka není v %s" % (r, cfg["pages"]) for r in sorted(html - pages)]
        errors += ["%s: stránka z %s v commitu chybí" % (r, cfg["pages"]) for r in sorted(pages - html)]
    return errors


# ── razítka ?v= ──
def token(data):
    return _sha256(data)[:10]


def stamp(rel, data, tokens):
    """Odkazy na CSS a JS z exportu dostanou ?v=<hash obsahu>; cizí a neexistující zůstanou."""
    base = posixpath.dirname(rel)

    def sub(m):
        ref = m.group(2).decode("utf-8", "replace")
        if ref.startswith("//"):
            return m.group(0)
        target = ref.lstrip("/") if ref.startswith("/") else posixpath.normpath(posixpath.join(base, ref))
        tok = tokens.get(target)
        if not tok:
            return m.group(0)
        return m.group(1) + m.group(2) + b"?v=" + tok.encode() + b'"'

    return STAMP.sub(sub, data)


def unstamp(data):
    return UNSTAMP.sub(rb'\1"', data)


# ── cíle ──
class LocalTarget:
    """Kopie do adresáře na stejném stroji: atomický zápis, režim souboru zůstává."""
    kind = "local"

    def __init__(self, root, file_mode=0o644):
        self.root = Path(root)
        self.file_mode = file_mode
        if not self.root.is_dir():
            raise DeployError("Cílová složka %s neexistuje." % self.root)

    def describe(self):
        return str(self.root)

    def _path(self, rel):
        return self.root.joinpath(*PurePosixPath(rel).parts)

    def read(self, rel):
        try:
            return self._path(rel).read_bytes()
        except FileNotFoundError:
            return None
        except (IsADirectoryError, PermissionError, NotADirectoryError) as e:
            raise DeployError("%s v cíli nejde přečíst: %s" % (rel, e))

    def changed(self, out, state_files):
        return {rel for rel, data in out.items() if self.read(rel) != data}

    def content_changed(self, rel, src, state_files):
        cur = self.read(rel)
        return cur is None or unstamp(cur) != unstamp(src)

    def upload(self, rels, out):
        for rel in rels:
            self._write(rel, out[rel])

    def close(self):
        pass

    def _mkdirs(self, d):
        if d.is_dir():
            return
        self._mkdirs(d.parent)
        d.mkdir()
        os.chmod(d, stat.S_IMODE(d.parent.stat().st_mode))

    def _write(self, rel, data):
        p = self._path(rel)
        self._mkdirs(p.parent)
        try:
            mode = stat.S_IMODE(p.stat().st_mode)
        except FileNotFoundError:
            mode = self.file_mode
        fd, tmp = tempfile.mkstemp(prefix="." + p.name + ".", suffix=".cms-tmp", dir=p.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, p)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


class _Staged:
    """Společné pro vzdálené cíle: soubory se připraví do dočasné složky a odtud nahrají."""

    def __init__(self):
        self.stage = None

    def _stage(self, out):
        if self.stage is None:
            self.stage = Path(tempfile.mkdtemp(prefix="cms-deploy-"))
            for rel, data in out.items():
                p = self.stage.joinpath(*PurePosixPath(rel).parts)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                os.chmod(p, self.file_mode)
        return self.stage

    def close(self):
        if self.stage is not None:
            shutil.rmtree(self.stage, ignore_errors=True)
            self.stage = None

    def content_changed(self, rel, src, state_files):
        """Obsah vzdáleně nečteme: rozhoduje blob z posledního deploye (bez stavu nevíme nic)."""
        if state_files is None:
            return None
        return state_files.get(rel, {}).get("src") != _sha256(src)


class RsyncTarget(_Staged):
    """rsync bez mazání: porovnává obsahem (-c), práva existujících souborů nemění."""
    kind = "rsync"

    def __init__(self, dest, ssh=None, rsync="rsync", file_mode=0o644):
        super().__init__()
        self.dest = dest.rstrip("/") + "/"
        self.ssh, self.rsync, self.file_mode = ssh, rsync, file_mode

    def describe(self):
        return self.dest

    def _cmd(self, extra, listfile):
        cmd = [self.rsync, "-rc", "--files-from=" + str(listfile)] + extra
        if self.ssh:
            cmd += ["-e", self.ssh]
        return cmd + [str(self.stage) + "/", self.dest]

    def _run(self, cmd):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DeployError("rsync selhal: %s" % e)
        if p.returncode:
            raise DeployError("rsync skončil kódem %d: %s" % (p.returncode, (p.stderr or p.stdout).strip()[-800:]))
        return p.stdout

    def _list(self, rels, name):
        f = self.stage.parent / (self.stage.name + "-" + name)
        f.write_text("".join(r + "\n" for r in rels), encoding="utf-8")
        return f

    def changed(self, out, state_files):
        self._stage(out)
        lst = self._list(sorted(out), "all")
        try:
            listing = self._run(self._cmd(["-n", "--itemize-changes"], lst))
        finally:
            lst.unlink()
        changed = set()
        for line in listing.splitlines():
            if len(line) > 12 and line[0] in "<>" and line[1] == "f":
                changed.add(line[12:].strip())
        return changed & set(out)

    def upload(self, rels, out):
        self._stage(out)
        for ph in (0, 1, 2):
            part = [r for r in rels if phase(r) == ph]
            if not part:
                continue
            lst = self._list(part, "phase%d" % ph)
            try:
                self._run(self._cmd([], lst))
            finally:
                lst.unlink()


class SftpTarget(_Staged):
    """sftp pro hosting bez shellu: změny podle stavu posledního deploye, zápis není atomický."""
    kind = "sftp"

    def __init__(self, host, path, args=(), sftp="sftp", file_mode=0o644):
        super().__init__()
        self.host, self.path = host, path.rstrip("/")
        self.args, self.sftp, self.file_mode = list(args), sftp, file_mode

    def describe(self):
        return "%s:%s" % (self.host, self.path)

    def changed(self, out, state_files):
        state_files = state_files or {}
        return {rel for rel, data in out.items() if state_files.get(rel, {}).get("out") != _sha256(data)}

    def batch(self, rels):
        def q(s):
            return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')
        lines, dirs = [], set()
        for rel in rels:
            parent = posixpath.dirname(rel)
            chain = []
            while parent and parent not in dirs:
                chain.append(parent)
                dirs.add(parent)
                parent = posixpath.dirname(parent)
            lines += ["-mkdir %s" % q(self.path + "/" + d) for d in reversed(chain)]
            lines.append("put %s %s" % (q(str(self.stage.joinpath(*PurePosixPath(rel).parts))), q(self.path + "/" + rel)))
        return "".join(x + "\n" for x in lines)

    def upload(self, rels, out):
        self._stage(out)
        bf = self.stage.parent / (self.stage.name + "-batch")
        bf.write_text(self.batch(rels), encoding="utf-8")
        try:
            p = subprocess.run([self.sftp, "-b", str(bf), *self.args, self.host], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DeployError("sftp selhal: %s" % e)
        finally:
            bf.unlink()
        if p.returncode:
            raise DeployError("sftp skončil kódem %d: %s" % (p.returncode, (p.stderr or p.stdout).strip()[-800:]))


def make_target(cfg, override_path=None):
    if override_path:
        return LocalTarget(override_path, cfg["file_mode"])
    t = cfg["target"]
    kind = t.get("kind", "local")
    if kind == "local":
        if not t.get("path"):
            raise DeployError("Cíl local potřebuje [deploy.target] path.")
        return LocalTarget(t["path"], cfg["file_mode"])
    if kind == "rsync":
        if not t.get("dest"):
            raise DeployError("Cíl rsync potřebuje [deploy.target] dest.")
        return RsyncTarget(t["dest"], t.get("ssh"), t.get("rsync", "rsync"), cfg["file_mode"])
    if kind == "sftp":
        if not t.get("host") or not t.get("path"):
            raise DeployError("Cíl sftp potřebuje [deploy.target] host a path.")
        return SftpTarget(t["host"], t["path"], t.get("args", []), t.get("sftp", "sftp"), cfg["file_mode"])
    raise DeployError("Neznámý cíl %r (umím local, rsync, sftp)." % kind)


# ── stav a zámek ──
def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise DeployError("Stav deploye %s nejde přečíst: %s" % (path, e))


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


@contextlib.contextmanager
def locked(path, wait=300):
    """Jeden deploy naráz (admin a /merge můžou přijít současně)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+b")
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise DeployError("Jiný deploy drží zámek %s déle než %d s." % (path, wait))
                time.sleep(1)
        yield
    finally:
        f.close()


def default_state(cfg):
    host = urllib.parse.urlparse(cfg["site"]).hostname or "web"
    return Path.home() / ".local" / "state" / "cms" / ("deploy-%s.json" % host)


# ── IndexNow ──
def sitemap_urls(data, site):
    """[(URL, cesta souboru)] ze sitemapy; čisté URL (/stranka) míří na stranka.html."""
    out = []
    for loc in re.findall(rb"<loc>\s*([^<\s]+)\s*</loc>", data):
        url = loc.decode("utf-8", "replace")
        if site and not url.startswith(site + "/") and url != site:
            continue
        rel = urllib.parse.unquote(urllib.parse.urlparse(url).path).lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        elif not rel.endswith(".html") and "." not in posixpath.basename(rel):
            rel += ".html"
        out.append((url, rel))
    return out


def post_indexnow(endpoint, payload):
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8",
                                          "User-Agent": "cms-deploy/1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


# ── deploy ──
class Deployer:
    def __init__(self, repo, config=None, state=None, out=None, post=post_indexnow):
        """config: text cms.toml mimo commit (None = cms.toml z nasazovaného commitu)."""
        self.repo = Repo(repo)
        self.config_text = config
        self.state_path = state
        self.out = out or sys.stdout
        self.post = post

    def say(self, line=""):
        print(line, file=self.out, flush=True)

    def _config(self, sha):
        if self.config_text is not None:
            return load_config(self.config_text, "nastavení")
        raw = self.repo.show(sha, "cms.toml")
        if raw is None:
            raise DeployError("Commit %s nemá cms.toml a nastavení nebylo předáno (--config)." % sha[:12])
        return load_config(raw.decode("utf-8"), "cms.toml v %s" % sha[:12])

    def _resolve(self, sha, cfg_hint, fetch):
        remote, branch = cfg_hint
        if fetch:
            self.repo.fetch(remote, branch)
        tip = self.repo.rev("refs/remotes/%s/%s" % (remote, branch))
        if not tip:
            raise DeployError("Repo nezná %s/%s, nejdřív git fetch." % (remote, branch))
        want = self.repo.rev(sha) if sha else tip
        if not want:
            raise DeployError("Commit %s repo nezná (je pushnutý na %s/%s?)." % (sha, remote, branch))
        if not self.repo.is_ancestor(want, tip):
            raise DeployError("Commit %s není na %s/%s, nasazuje se jen z ní." % (want[:12], remote, branch))
        return want, tip

    def _hint(self):
        """remote a větev ještě před přečtením commitu: z --config, jinak výchozí."""
        if self.config_text is not None:
            cfg = load_config(self.config_text, "nastavení")
            return cfg["remote"], cfg["branch"]
        return DEFAULTS["remote"], DEFAULTS["branch"]

    def status(self, fetch=True):
        remote, branch = self._hint()
        if fetch:
            self.repo.fetch(remote, branch)
        tip = self.repo.rev("refs/remotes/%s/%s" % (remote, branch))
        cfg = self._config(tip) if tip else load_config(self.config_text or "[deploy]\n")
        path = self.state_path or cfg["state"] or default_state(cfg)
        dep = load_state(Path(path).expanduser()).get("deployed") or {}
        res = {"deployed": dep or None, "tip": tip, "pending": None, "commits": []}
        if dep.get("sha") and tip and self.repo.rev(dep["sha"]) and self.repo.is_ancestor(dep["sha"], tip):
            res["pending"], res["commits"] = self.repo.log(dep["sha"], tip)
        return res

    def deploy(self, sha=None, caller="full", dry_run=False, rollback=False, fetch=True, target_path=None):
        if caller not in ("full", "cms"):
            raise DeployError("Neznámý režim %r (full nebo cms)." % caller)
        if caller == "cms":
            if self.config_text is None:
                raise DeployError("Režim cms čte nastavení jen z --config, ne z nasazovaného commitu.")
            if rollback or target_path:
                raise DeployError("Režim cms nasazuje jen dopředu a jen do nastaveného cíle.")
        sha, tip = self._resolve(sha, self._hint(), fetch)
        cfg = self._config(sha)
        state_path = Path(self.state_path or cfg["state"] or default_state(cfg)).expanduser()
        lock = contextlib.nullcontext() if dry_run else locked(state_path.with_name(state_path.name + ".lock"))
        with lock:
            return self._deploy(sha, tip, cfg, caller, dry_run, rollback, target_path, state_path)

    def _deploy(self, sha, tip, cfg, caller, dry_run, rollback, target_path, state_path):
        state = load_state(state_path)
        prev = (state.get("deployed") or {}).get("sha")
        forward = True
        if prev and prev != sha:
            forward = bool(self.repo.rev(prev)) and self.repo.is_ancestor(prev, sha)
            if not forward and not rollback:
                raise DeployError("Commit %s nenavazuje na naposledy nasazený %s. Deploy jde jen dopředu, "
                                  "starší commit nasadí jen plný režim s --rollback." % (sha[:12], prev[:12]))

        subject = self.repo.out("log", "-1", "--format=%s", sha)
        self.say("Commit: %s %s (%s/%s, režim %s%s)" % (sha[:12], subject, cfg["remote"], cfg["branch"], caller,
                                                         ", rollback" if rollback and not forward else ""))
        files = export(self.repo, sha, cfg)
        pages = manifest_pages(self.repo, sha, cfg)
        errors = check(files, cfg, pages)
        if errors:
            raise DeployError("Kontroly neprošly, nic se nenasadilo:\n" + "\n".join("  " + e for e in errors[:LIST_MAX]))
        self.say("Kontroly: %s%s, PHP jen %s, skryté jen %s" % (
            _plural(len(files), "soubor", "soubory", "souborů"),
            ", %s podle %s" % (_plural(len(pages), "stránka", "stránky", "stránek"), cfg["pages"]) if pages is not None else "",
            ", ".join(cfg["php"]) or "žádné", ", ".join(cfg["dotfiles"]) or "žádné"))

        blobs = self.repo.blobs([obj for *_, obj in files])
        src = {rel: blobs[obj] for rel, _, _, obj in files}
        tokens = {rel: token(data) for rel, data in src.items() if rel.endswith((".css", ".js"))}
        out = {rel: stamp(rel, data, tokens) if rel.endswith(".html") else data for rel, data in src.items()}

        if prev and forward and prev != sha:
            count, lines = self.repo.log(prev, sha)
            self.say("Od posledního deploye (%s): %s" % (prev[:12], _plural(count, "commit", "commity", "commitů")))
            for line in lines:
                self.say("  " + line)
            if count > len(lines):
                self.say("  … a %d dalších" % (count - len(lines)))
        elif not prev:
            self.say("Od posledního deploye: stav chybí (%s), první deploy z gitu" % state_path)

        target = make_target(cfg, target_path)
        try:
            state_files = state.get("files") if state else None
            changed = target.changed(out, state_files)
            if caller == "cms":
                blocked = sorted(r for r in changed if phase(r) == 0)
                if blocked:
                    raise DeployError(
                        "Commit nese nenasazené změny mimo HTML a sitemapu, ty nasadí jen plný deploy "
                        "(/merge nebo cms-deploy deploy). Nic se nenasadilo:\n"
                        + "\n".join("  " + r for r in blocked[:LIST_MAX]))
            order = sorted(changed, key=lambda r: (phase(r), r))
            counts = [sum(1 for r in order if phase(r) == ph) for ph in (0, 1, 2)]
            self.say("Cíl: %s %s" % (target.kind, target.describe()))
            self.say("Změněno: %s (mimo HTML %d, HTML %d, sitemapy %d)" % (
                _plural(len(order), "soubor", "soubory", "souborů"), *counts))
            for r in order[:LIST_MAX]:
                self.say("  " + r)
            if len(order) > LIST_MAX:
                self.say("  … a %d dalších" % (len(order) - LIST_MAX))

            ping = self._indexnow_urls(cfg, src, order, target, state_files)

            if dry_run:
                self.say("DRY RUN: nic se nezapsalo, IndexNow by dostal %s." % _plural(len(ping), "URL", "URL", "URL"))
                return {"sha": sha, "changed": order, "indexnow": ping, "dry_run": True}

            target.upload(order, out)
        finally:
            target.close()

        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        entry = {"sha": sha, "at": now, "caller": caller, "changed": len(order)}
        state = {"version": 1, "deployed": entry,
                 "history": ([entry] + list(state.get("history") or []))[:50],
                 "files": {rel: {"src": _sha256(src[rel]), "out": _sha256(out[rel])} for rel in out}}
        save_state(state_path, state)

        if ping:
            self._send_indexnow(cfg, src, ping)
        version = sha[:12]
        self.say("OK: Deployed version %s to %s" % (version, cfg["site"] or target.describe()))
        return {"sha": sha, "version": version, "changed": order, "indexnow": ping, "dry_run": False}

    def _indexnow_urls(self, cfg, src, order, target, state_files):
        ix = cfg.get("indexnow")
        if not ix or not cfg["site"]:
            return []
        sm = src.get(ix.get("sitemap", "sitemap.xml"))
        if sm is None:
            return []
        changed = set(order)
        urls = []
        for url, rel in sitemap_urls(sm, cfg["site"]):
            if rel not in src or rel not in changed:
                continue
            if target.content_changed(rel, src[rel], state_files):
                urls.append(url)
        return urls

    def _send_indexnow(self, cfg, src, urls):
        ix = cfg["indexnow"]
        key_file = ix.get("key_file", "indexnow-key.txt")
        key = (src.get(key_file) or b"").decode("utf-8", "replace").strip()
        if not key:
            self.say("IndexNow: klíč %s v exportu chybí, nic se neposlalo." % key_file)
            return
        payload = {"host": urllib.parse.urlparse(cfg["site"]).hostname, "key": key,
                   "keyLocation": cfg["site"] + "/" + key_file, "urlList": urls}
        try:
            self.post(ix.get("endpoint", "https://api.indexnow.org/indexnow"), payload)
            self.say("IndexNow: %s" % _plural(len(urls), "URL", "URL", "URL"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.say("IndexNow selhal (deploy proběhl): %s" % e)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="deploy.py", description="Deploy webu z commitu v gitu (Khoder CMS).")
    ap.add_argument("--repo", default=".", help="git repo (může být bare)")
    ap.add_argument("--config", help="cms.toml mimo commit (režim cms ho vyžaduje)")
    ap.add_argument("--state", help="soubor se stavem posledního deploye")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deploy")
    d.add_argument("--sha", help="commit na větvi (výchozí její špička)")
    d.add_argument("--caller", default="full", choices=("full", "cms"))
    d.add_argument("--dry-run", action="store_true")
    d.add_argument("--rollback", action="store_true", help="dovolí starší commit (jen full)")
    d.add_argument("--no-fetch", action="store_true")
    d.add_argument("--target-path", help="místo nastaveného cíle kopie do této složky")
    s = sub.add_parser("status")
    s.add_argument("--no-fetch", action="store_true")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):           # výstup čte publikace a ssh, vždy UTF-8
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")

    config = None
    if a.config:
        try:
            config = Path(a.config).expanduser().read_text(encoding="utf-8")
        except OSError as e:
            print("CHYBA: nastavení %s nejde přečíst: %s" % (a.config, e), file=sys.stderr)
            return 1
    dep = Deployer(a.repo, config, a.state)
    try:
        if a.cmd == "deploy":
            dep.deploy(a.sha, a.caller, a.dry_run, a.rollback, not a.no_fetch, a.target_path)
        else:
            st = dep.status(not a.no_fetch)
            if a.json:
                print(json.dumps(st, ensure_ascii=False, indent=1))
            else:
                d0 = st["deployed"]
                print("Nasazeno: %s" % ("%s (%s, %s)" % (d0["sha"][:12], d0["at"], d0["caller"]) if d0 else "zatím nic"))
                if st["pending"] is not None:
                    print("Čeká na deploy: %s" % _plural(st["pending"], "commit", "commity", "commitů"))
                    for line in st["commits"]:
                        print("  " + line)
    except DeployError as e:
        print("CHYBA: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
