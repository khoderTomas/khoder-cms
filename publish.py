# -*- coding: utf-8 -*-
"""Publikace úprav z editoru a kontrola stránek proti zdrojům při startu serveru.

Publikovat (vlákno s krokovým stavem pro polling, pod build lockem checkoutu). Checkout, ve
kterém editor běží, se během publikace nemění: sloučení, build i commit vznikají v dočasném
worktree (ADR 0014, cms/gitsync.py). B = HEAD checkoutu, T = origin/main.
  1. git: checkout na main, žádný merge ani rebase, soubory z journalu nezměnil nikdo jiný.
     Pod zámkem publikace (čeká na něj i sync hostitele) fetch a sloučení zdrojů z journalu
     git merge-file (úpravy / B / T): ruční a owner stránky a zdroje generátoru.
     Generované stránky a sitemap se neslučují, vzniknou buildem z T. Konflikt, stránka
     smazaná v T nebo se změněným druhem = seznam dotčených úprav a nic se nezmění.
  2. worktree z T se sloučenými zdroji, build a kontrola webu (cms.toml [publish] build)
  3. sitemap (cms.toml [publish] sitemap)
  4. změnit se smí jen soubory z journalu, sitemap, generované stránky dotčených skriptů
     a stránky, které přebírají obsah ze změněných stránek; commit (autor = uživatel adminu,
     bez trailera Claude, jsou to ruční úpravy) a push. Když main mezitím ujel, kroky 1 až 4
     se jednou zopakují. Pak mark_published s commitem a posun checkoutu (merge --ff-only).
  5. deploy toho commitu na serveru (cms-deploy v režimu cms: nasadí jen HTML a sitemapu).
     Když selže, commit zůstává v main a Nasadit znovu (redeploy) ho zkusí znovu.
  6. smoke na produkci: stránka vrací 200 a obsahuje nový text
Deploy, build, remote, adresa webu, stažení stránky i složka worktree jdou injektovat kvůli testům.

Panel Změny (changes) porovnává stránky s HEAD: bloky textu spárované přes
SequenceMatcher a rozdíl po slovech, pole SEO, zbytek jednou větou. Zahodit
(discard) vrací stránku do stavu HEAD: ruční bajty z gitu, generovaná přes
literály, které její úpravy v journalu změnily.

Najít a nahradit (replace.py) je jeden záznam journalu kind=replace přes mnoho
souborů: v panelu Změny má vlastní kartu s Vrátit, stránky, které mění jen on,
se nerozepisují. Zahodit stránku, na kterou sáhl, nejde, dokud se nevrátí.
"""
import collections
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import adapter as AD
import apply as A
import blocks as B
import buildlock
import classify as C
import gitsync as G
import htmltext as H
import pysource as PS
import seo as S
from journal import sha

# Deploy: ssh klíčem s vynuceným příkazem (cms-deploy v režimu cms) na cíl z cms.toml [publish]
# deploy_ssh, proměnná CMS_DEPLOY_SSH ho přebíjí.
STEPS = [("git", "Sloučení s main"), ("build", "Build a verify"), ("sitemap", "Sitemap"),
         ("commit", "Commit a push"), ("deploy", "Deploy"), ("smoke", "Kontrola na webu")]
REDEPLOY = ("deploy", "smoke")
DEPLOYED = re.compile(r"OK: Deployed version (\S+)")
AUTHOR = re.compile(r"\s*([^<>]*\S)\s*<([^<>\s]+@[^<>\s]+)>\s*")
CONFLICT = "Úpravy z editoru se střetly se změnami v main, nic se nezměnilo."
CONFLICT_HOW = "Zahoď je v panelu Změny a po stažení nového main je udělej znovu."


class StepFail(Exception):
    pass


def fetch_url(url):
    """(status, text) stránky z produkce."""
    req = urllib.request.Request(url, headers={"User-Agent": "cms-editor-smoke/1", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def plural(n, one, few, many):
    return "%d %s" % (n, one if n == 1 else few if 2 <= n <= 4 else many)


def _author_name(author):
    m = AUTHOR.fullmatch(author or "")
    return m.group(1) if m else None


def _page_counts(entries):
    """[{slug, count}] stránek s úpravami v pořadí první úpravy; nahrazení se počítá každé stránce."""
    out = {}
    for e in entries:
        for s in (e.get("pages") or []) if e.get("kind") == "replace" else [e.get("slug")]:
            if s:
                out[s] = out.get(s, 0) + 1
    return [{"slug": s, "count": n} for s, n in out.items()]


def _pages(entries):
    return [x["slug"] for x in _page_counts(entries)]


def _tail(text, n=12):
    lines = [x for x in (text or "").strip().splitlines() if x.strip()]
    return "\n".join(lines[-n:])


def _label(e):
    if e.get("kind") == "replace":
        return "Najít a nahradit „%s“" % _cut(e.get("before") or "", 40)
    return e.get("label") or e["id"]


def conflict_text(conflicts):
    """Seznam konfliktů pro chybu kroku: stránka nebo soubor, úprava a co ho změnilo v main."""
    lines = [CONFLICT]
    for c in conflicts:
        where = "/" + c["page"] if c["page"] else c["path"]
        if c["why"]:
            lines.append("%s: %s" % (where, c["why"]))
        for e in c["edits"]:
            lines.append("%s: „%s“" % (e["label"], e["text"]) if e["text"] else e["label"])
        if c["commits"]:
            lines.append("  v main: " + "; ".join(c["commits"]))
    lines.append(CONFLICT_HOW)
    return "\n".join(lines)


class Publisher:
    """site = adaptér webu (výchozí adapter.load(root)), url = adresa webu pro kontrolu na webu
    (výchozí cms.toml [deploy] site). Příkazy buildu a sitemapy z cms.toml [publish]."""

    def __init__(self, root, journal, remote="origin", branch="main", url=None, site=None,
                 deploy_cmd=None, build_cmd=None, sitemap_cmd=None, fetch=fetch_url, work_dir=None):
        self.root = Path(root)
        self.journal = journal
        self.site = site or AD.load(self.root)
        self.repo = G.Git(self.root)
        self.remote, self.branch = remote, branch
        self.url = (url or self.site.url).rstrip("/")
        self.work_dir = work_dir                # dočasné worktree (None = systémový temp)
        py = sys.executable
        # build a sitemap běží ve worktree publikace (cwd), cesty jsou relativní k němu
        self.build_cmd = build_cmd or (AD.NICE + [py] + self.site.build_cmd if self.site.build_cmd else None)
        self.sitemap_cmd = sitemap_cmd or (AD.NICE + [py] + self.site.sitemap_cmd if self.site.sitemap_cmd else None)
        # {sha} = celý SHA publikovaného commitu. Klíč má vynucený příkaz `cms-deploy cms`, ten bere
        # argumenty ze SSH_ORIGINAL_COMMAND, takže se posílá jen „deploy <sha>“.
        self.deploy_ssh = os.environ.get("CMS_DEPLOY_SSH") or self.site.deploy_ssh
        self.deploy_cmd = deploy_cmd or (["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", self.deploy_ssh,
                                          "deploy {sha}"] if self.deploy_ssh else None)
        self.fetch = fetch
        self._lock = threading.Lock()
        self._thread = None
        self._current = None
        self.state = self._fresh("idle")

    # ── stav ──
    def _fresh(self, state, keys=None):
        return {"state": state, "error": None, "version": None, "commit": None, "pages": [],
                "started": time.time(),
                "steps": [{"key": k, "label": lbl, "state": "pending", "detail": "", "t": None}
                          for k, lbl in STEPS if keys is None or k in keys]}

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def redeploy_info(self):
        """Publikovaný commit, který se nenasadil (Nasadit znovu), nebo None."""
        pending = self.journal.undeployed()
        if not pending:
            return None
        return {"commit": pending[-1]["commit"][:12], "count": len(pending)}

    def status(self):
        with self._lock:
            s = dict(self.state, steps=[dict(x) for x in self.state["steps"]])
        s["count"] = len(self.journal.unpublished())
        s["redeploy"] = self.redeploy_info()
        return s

    def _step(self, key, state, detail=""):
        with self._lock:
            for st in self.state["steps"]:
                if st["key"] == key:
                    st["state"], st["detail"] = state, detail
                    st["t"] = round(time.time() - self.state["started"])
            self._current = key

    # ── pomocníci ──
    def _run(self, cmd, timeout=300, env=None, cwd=None):
        try:
            p = subprocess.run(cmd, cwd=cwd or self.root, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return 124, "Časový limit %d s vypršel." % timeout
        except OSError as e:
            return 127, str(e)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def _git(self, *args, timeout=120):
        return self._run(["git", *args], timeout=timeout)

    def _registry(self, rev):
        key = "%s:%s" % (rev, self.site.pages_rel)
        raw = self.repo.blobs([key])[key]
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _ahead_text(self, base, target):
        n, lines = self.repo.log(target, base, limit=3)
        what = ("1 commit, který v %s/%s není" if n == 1
                else "%d %s, které v %%s/%%s nejsou" % (n, "commity" if n <= 4 else "commitů"))
        return ("Checkout má %s (%s). Publikovat jde jen nad %s: nejdřív je pushni nebo zahoď."
                % (what % (self.remote, self.branch), "; ".join(lines), self.branch))

    # ── náhled před publikací ──
    def default_message(self, entries):
        slugs, reps = [], []
        for e in entries:
            if e.get("kind") == "replace":
                reps.append("nahrazení „%s“" % _cut(e.get("before") or "", 40))
            elif e.get("slug") and e["slug"] not in slugs:
                slugs.append(e["slug"])
        def join(xs):
            return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " a " + xs[-1]
        parts = []
        if slugs:
            parts.append("úpravy textů na " + join(["homepage" if s == "index" else "/" + s for s in slugs[:4]])
                         + (" a dalších stránkách" if len(slugs) > 4 else ""))
        if reps:
            parts.append(join(reps[:2]) + (" a další nahrazení" if len(reps) > 2 else ""))
        return "content: " + (" a ".join(parts) if parts else "úpravy textů")

    def problems(self, files):
        """Rychlé kontroly bez sítě: větev, merge nebo rebase, soubory z journalu změněné mimo editor."""
        out = []
        rc, branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        branch = branch.strip()
        if rc:
            return ["Tohle není git repozitář, publikovat nejde."]
        if branch != self.branch:
            out.append("Publikovat jde jen z větve %s, teď je checkoutnutá %s." % (self.branch, branch))
        for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD"):
            rc, p = self._git("rev-parse", "--git-path", name)
            if not rc and (self.root / p.strip()).exists():
                out.append("V gitu právě běží merge nebo rebase, nejdřív ho dokonči.")
                break
        stale = self.journal.stale_files()
        if stale:
            out.append("Soubory s úpravami z editoru změnil i někdo jiný (%s). Zkontroluj git diff, publikovat teď nejde."
                       % (", ".join(stale[:6]) + (" …" if len(stale) > 6 else "")))
        if not files:
            out.append("Není co publikovat.")
        return out

    def preview(self):
        """Dialog Publikovat: soubory, zpráva, problémy a se stavem main i konflikty a nové commity."""
        entries = self.journal.unpublished()
        files = self.journal.unpublished_files()
        out = {"files": files + ([self.site.sitemap_rel] if files else []), "entries": len(entries),
               "message": self.default_message(entries), "problems": self.problems(files),
               "running": self.running(), "conflicts": [], "incoming": 0, "commits": [], "commit_list": [],
               "pages": _page_counts(entries), "redeploy": self.redeploy_info()}
        if not files or out["problems"] or out["running"]:
            return out
        try:
            base, target = self.repo.rev("HEAD"), self.repo.fetch(self.remote, self.branch)
            if not self.repo.is_ancestor(base, target):
                out["problems"].append(self._ahead_text(base, target))
                return out
            out["incoming"], out["commits"] = self.repo.log(base, target, limit=5)
            out["commit_list"] = self.repo.log_detail(base, target, limit=8)
            _, out["conflicts"] = self._merge(entries, files, base, target)
        except G.GitError as e:
            out["problems"].append("Stav %s/%s se nepodařilo zjistit: %s" % (self.remote, self.branch, e))
            return out
        if out["conflicts"]:
            out["problems"].append(CONFLICT + " " + CONFLICT_HOW)
        return out

    # ── sloučení s main ──
    def _merge(self, entries, files, base, target):
        """Zdroje z journalu sloučené s T: ({cesta: bajty}, [konflikty]). Generované stránky a sitemap
        se neslučují (vzniknou buildem). Ve zdrojích je obsah checkoutu tak, jak by ho uložil git."""
        reg_b, reg_t = self._registry(base), self._registry(target)
        derived = {self.site.sitemap_rel} | {v["output"] for v in reg_b.values() if v.get("kind") == "generated"}
        conflicts = []
        for p in files:
            slug = self.site.page_slug(p)
            if not slug:
                continue
            kb, kt = (reg_b.get(slug) or {}).get("kind"), (reg_t.get(slug) or {}).get("kind")
            if kt is None:
                conflicts.append(self._conflict(p, entries, base, target, "v main stránka zmizela"))
            elif kt != kb:
                conflicts.append(self._conflict(p, entries, base, target, "v main se změnil druh stránky (%s → %s)"
                                                % (kb, kt)))
        bad = {c["path"] for c in conflicts}
        sources = [p for p in files if p not in derived and p not in bad]
        blobs = self.repo.blobs(["%s:%s" % (r, p) for r in (base, target) for p in sources])
        ours = self.repo.clean(sources)
        merged = {}
        for p in sources:
            b, t = blobs["%s:%s" % (base, p)], blobs["%s:%s" % (target, p)]
            if t is None:
                conflicts.append(self._conflict(p, entries, base, target, "v main soubor zmizel"))
                continue
            data, n = G.merge_file(ours[p], b, t)
            if n:
                conflicts.append(self._conflict(p, entries, base, target, None, b, t))
            else:
                merged[p] = data
        return merged, conflicts

    def _conflict(self, path, entries, base, target, why, b=None, t=None):
        """Konflikt v souboru: úpravy, kterých se týká (stránka a text), a commity z main."""
        touching = [e for e in entries if any(f["path"] == path for f in e["files"])]
        hit = touching
        if why is None and len(touching) > 1:
            every = self.journal.entries()
            hit = [e for e in touching if self._clashes(e, path, b, t, every)] or touching
        _, commits = self.repo.log(base, target, [path], limit=3)
        page = self.site.page_slug(path)
        newest = {}                                 # Vrátit jde jen nejnovější úpravu každého souboru
        for e in entries:
            for f in e["files"]:
                newest[f["path"]] = e["id"]
        return {"path": path, "page": page, "why": why, "commits": commits,
                "commit_list": self.repo.log_detail(base, target, [path], limit=3),
                "edits": [{"id": e["id"], "label": _label(e), "text": _cut(e.get("after") or "", 80),
                           "user": e.get("user"), "t": e.get("t"),
                           "undo": all(newest.get(f["path"]) == e["id"] for f in e["files"])} for e in hit]}

    def _clashes(self, e, path, b, t, every):
        """Střetne se se změnou v main i úprava e sama? Její změna se přenese na B a sloučí s T."""
        before, after = self.journal.before_bytes(e, path), self.journal.after_bytes(e, path, every)
        if before is None or after is None or b is None:
            return True
        before, after = self.repo.clean_bytes(path, before), self.repo.clean_bytes(path, after)
        alone, n = G.merge_file(after, before, b)
        return bool(n) or bool(G.merge_file(alone, b, t)[1])

    # ── publikace ──
    def start(self, message, author=None):
        with self._lock:
            if self.running():
                raise buildlock.Busy("Publikace už běží.")
            self.state = self._fresh("running")
            self._current = None
            self._thread = threading.Thread(target=self.run, args=(message, author), daemon=True)
            self._thread.start()

    def run(self, message, author=None):
        """Celá publikace synchronně (vlákno ze start(), testy volají přímo).
        author = "Jméno <e-mail>" uživatele adminu, None = identita gitu v checkoutu."""
        with self._lock:
            if self.state["state"] != "running":
                self.state = self._fresh("running")
            self._current = None
        try:
            with buildlock.hold("publikace z editoru", wait=30):
                self._publish(message.strip() or self.default_message(self.journal.unpublished()), author)
            with self._lock:
                self.state["state"] = "done"
        except (StepFail, buildlock.Busy, G.GitError) as e:
            self._failed(str(e))
        except Exception as e:                               # noqa: BLE001 — stav musí skončit
            self._failed("Neočekávaná chyba: %r" % e)
        return self.status()

    def _failed(self, msg):
        with self._lock:
            self.state["state"] = "failed"
            self.state["error"] = msg
            first = self.state["steps"][0]["key"]
            for st in self.state["steps"]:
                if st["key"] == self._current or (self._current is None and st["key"] == first):
                    st["state"], st["detail"] = "failed", msg
                    st["t"] = round(time.time() - self.state["started"])

    def _publish(self, message, author):
        entries = self.journal.unpublished()
        files = self.journal.unpublished_files()

        # 1. git: kontroly, pak pod zámkem publikace fetch a sloučení s main
        self._step("git", "running")
        probs = self.problems(files)
        if probs:
            raise StepFail(probs[0])
        base = self.repo.rev("HEAD")
        with G.Lock(self.repo.lock_path(), wait=120):
            for attempt in (1, 2):
                target = self.repo.fetch(self.remote, self.branch)
                if not self.repo.is_ancestor(base, target):
                    raise StepFail(self._ahead_text(base, target))
                merged, conflicts = self._merge(entries, files, base, target)
                if conflicts:
                    raise StepFail(conflict_text(conflicts))
                n, _ = self.repo.log(base, target, limit=0)
                self._step("git", "done", "sloučeno s %s z main" % plural(n, "novým commitem", "novými commity",
                                                                       "novými commity") if n else "main beze změn")
                # 2.–4. build, sitemap a commit ve worktree z T, push
                with G.Worktree(self.repo, target, self.work_dir) as wt:
                    commit, count = self._build_commit(wt, merged, files, message, author)
                    try:
                        pushed = commit == target or wt.git.push(self.remote, self.branch) == "ok"
                    except G.GitError as e:
                        raise StepFail("Push selhal, nic se nepublikovalo. Úpravy zůstávají v editoru.\n%s" % e)
                if pushed:
                    break
                self._step("git", "running", "main se mezitím změnil, slučuji znovu")
            else:
                raise StepFail("main se během publikace změnil dvakrát po sobě, nic se nepublikovalo. Zkus to znovu.")
            self.journal.mark_published([e["id"] for e in entries], commit, by=_author_name(author))
            with self._lock:
                self.state["commit"] = commit[:12]
                self.state["pages"] = _pages(entries)
            done = ("%s · %s" % (commit[:12], plural(count, "soubor", "soubory", "souborů")) if count
                    else "%s · main už úpravy obsahuje" % commit[:12])
            note = self._advance(commit, files)
            self._step("commit", "done", done + ("\n" + note if note else ""))

        # 5.–6. deploy a smoke
        self._deploy(commit, entries)

    def _build_commit(self, wt, merged, files, message, author):
        """Sloučené zdroje do worktree, build, sitemap a commit. Vrací (commit, počet souborů);
        bez změny proti T je commit T."""
        for p, data in merged.items():
            f = wt.path / p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        self._step("build", "running")
        env = self.site.clean_env(dict(buildlock.child_env(), PYTHONIOENCODING="utf-8"))
        if self.build_cmd:
            rc, out = self._run(self.build_cmd, timeout=900, env=env, cwd=wt.path)
            if rc:
                raise StepFail("Build nebo verify selhal, nic se nepublikovalo.\n%s" % _tail(out))
            m = re.search(r"BUILD OK[^\n]*", out)
            self._step("build", "done", m.group(0) if m else "build a verify prošly")
        else:
            self._step("build", "done", "web build nemá")

        self._step("sitemap", "running")
        if self.sitemap_cmd:
            rc, out = self._run(self.sitemap_cmd, timeout=120, env=env, cwd=wt.path)
            if rc:
                raise StepFail("Sitemap selhala: %s" % _tail(out, 6))
            self._step("sitemap", "done", "lastmod podle gitu")
        else:
            self._step("sitemap", "done", "web sitemapu negeneruje")

        self._step("commit", "running")
        changed = wt.git.dirty()
        extra = sorted(changed - self._allowed(wt.path, merged, files))
        if extra:
            raise StepFail("Build změnil soubory mimo úpravy z editoru (%s). Zdroje v main a %s/ spolu nesedí, "
                           "nic se nepublikovalo." % (", ".join(extra[:6]) + (" …" if len(extra) > 6 else ""),
                                                      self.site.source))
        if not changed:
            return wt.git.rev("HEAD"), 0
        ident = []
        if author:                                          # uživatel adminu je autor i committer
            m = AUTHOR.fullmatch(author)
            if not m:
                raise StepFail("Autor commitu musí být ve tvaru „Jméno <e-mail>“, ne %r." % author)
            ident = ["-c", "user.name=" + m.group(1), "-c", "user.email=" + m.group(2)]
        wt.git.run("add", "-A", "--", *sorted(changed))
        wt.git.run(*ident, "commit", "-q", "-m", message, *(["--author", author] if author else []))
        return wt.git.rev("HEAD"), len(changed)

    def _allowed(self, work, merged, files):
        """Co smí build ve worktree změnit: soubory z journalu, sitemap, generované stránky skriptů
        se sloučeným zdrojem (všech, když se mění sdílený soubor generátoru) a stránky, které z nich
        přebírají obsah."""
        site = self.site
        allowed = set(files) | {site.sitemap_rel}
        reg = site.registry(work)
        gen = [v for v in reg.values() if v.get("kind") == "generated"]
        shared = bool(set(site.shared_files()) & set(merged))
        allowed |= {v["output"] for v in gen if shared or v.get("source") in merged}
        for p in list(allowed):
            slug = site.page_slug(p)
            if slug:
                allowed |= {site.page_rel(s) for s in site.followers(slug, work / site.source)}
        return allowed

    def _advance(self, commit, files):
        """Posun checkoutu na publikovaný commit. Vrací poznámku, když se neposunul."""
        try:
            r = G.advance(self.repo, commit, owned=files)
        except G.GitError as e:
            return "Checkout se neposunul (%s), úpravy jsou v main." % e
        if r["blocked"]:
            return ("Checkout se neposunul, rozpracované soubory mimo editor (%s) mění i main. Posune ho další sync."
                    % ", ".join(r["blocked"][:6]))
        return ""

    # ── deploy a smoke ──
    def start_redeploy(self):
        """Nasadit znovu: deploy a smoke posledního publikovaného commitu, který se nenasadil."""
        with self._lock:
            if self.running():
                raise buildlock.Busy("Publikace právě běží.")
            if not self.journal.undeployed():
                raise A.Fail("Není co nasadit znovu, publikované úpravy jsou nasazené.", code="empty", status=409)
            self.state = self._fresh("running", REDEPLOY)
            self._current = None
            self._thread = threading.Thread(target=self.redeploy, daemon=True)
            self._thread.start()

    def redeploy(self):
        """Nasadit znovu synchronně (vlákno ze start_redeploy(), testy volají přímo)."""
        with self._lock:
            if self.state["state"] != "running":
                self.state = self._fresh("running", REDEPLOY)
            self._current = None
        try:
            pending = self.journal.undeployed()
            if not pending:
                raise StepFail("Není co nasadit znovu, publikované úpravy jsou nasazené.")
            commit = pending[-1]["commit"]                  # nejnovější publikace, starší commity jsou v ní
            with self._lock:
                self.state["commit"] = commit[:12]
                self.state["pages"] = _pages(pending)
            self._deploy(commit, pending)
            with self._lock:
                self.state["state"] = "done"
        except StepFail as e:
            self._failed(str(e))
        except Exception as e:                               # noqa: BLE001 — stav musí skončit
            self._failed("Neočekávaná chyba: %r" % e)
        return self.status()

    def _deploy(self, commit, entries):
        self._step("deploy", "running")
        if not self.deploy_cmd:
            raise StepFail("Úpravy jsou v main (commit %s), deploy není nastavený (cms.toml [publish] deploy_ssh)."
                           % commit[:12])
        rc, out = self._run([c.replace("{sha}", commit) for c in self.deploy_cmd], timeout=600)
        m = DEPLOYED.search(out)
        if rc or not m:
            raise StepFail("Úpravy jsou v main (commit %s), ale deploy selhal. Nasadit znovu ho zkusí znovu.\n%s"
                           % (commit[:12], _tail(out)))
        version = m.group(1)
        with self._lock:
            self.state["version"] = version
        self.journal.mark_deployed([e["id"] for e in self.journal.undeployed()], version)
        self._step("deploy", "done", "OK: Deployed version %s" % version)
        self._smoke(entries, version)

    def _smoke(self, entries, version):
        """Poslední úprava každého bloku musí být na produkci."""
        self._step("smoke", "running")
        latest = {}
        for e in entries:
            latest[(e.get("slug"), e.get("block"))] = e
        want = {}
        for (slug, _), e in latest.items():
            if slug:
                want.setdefault(slug, [])
                if e.get("snippet"):
                    want[slug].append(H.norm(e["snippet"]))
        # Najít a nahradit: poslední, na pár stránkách, na které potom už nic nesáhlo
        reps = [i for i, e in enumerate(entries) if e.get("kind") == "replace" and e.get("snippet")]
        if reps:
            e = entries[reps[-1]]
            later = {f["path"] for x in entries[reps[-1] + 1:] for f in x["files"]}
            for slug in [p for p in e.get("pages") or [] if self.site.page_rel(p) not in later][:5]:
                want.setdefault(slug, []).append(H.norm(e["snippet"]))
        missing = []
        for slug, snippets in want.items():
            url = "%s/%s?v=%s" % (self.url, "" if slug == "index" else slug, version)
            code, text = self.fetch(url)
            if code != 200:
                missing.append("/%s vrací %s" % (slug, code))
                continue
            page = H.norm(text)
            # úprava z panelu SEO nebo nahrazení může být jen v atributu (description, og, alt), H.norm ho zahodí
            meta = " | ".join(H.norm(v) for v in re.findall(r'\b(?:content|alt|title|aria-label|placeholder)="([^"]*)"',
                                                               text))
            missing += ["/%s nemá text „%s“" % (slug, s) for s in snippets if s not in page and s not in meta]
        if missing:
            raise StepFail("Nasazeno (verze %s), ale kontrola na webu neprošla: %s" % (version, "; ".join(missing)))
        self._step("smoke", "done", "%s vrací 200 a obsahuje nový text" % plural(len(want), "stránka", "stránky", "stránek"))


# ── Změny proti HEAD ──
_WS = re.compile(r"\s+")
_FAQ = re.compile(r"faq:(\d+):([qa])$")


class NoHead(Exception):
    pass


def head_blobs(root, rels):
    """{cesta: bajty v HEAD, None = v HEAD není}. Bajty jsou přesně z gitu, bez převodu
    konců řádků (checkout by z nich udělal CRLF, P20); jeden git cat-file pro všechny cesty."""
    rels = list(dict.fromkeys(rels))
    if not rels:
        return {}
    try:
        p = subprocess.run(["git", "cat-file", "--batch"], cwd=root, capture_output=True, timeout=60,
                           input="".join("HEAD:%s\n" % r for r in rels).encode("utf-8"))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise NoHead(str(e))
    if p.returncode:
        raise NoHead(_tail(p.stderr.decode("utf-8", "replace"), 2))
    out, i, data = {}, 0, p.stdout
    for r in rels:
        nl = data.index(b"\n", i)
        head = data[i:nl].split()
        i = nl + 1
        if len(head) == 3 and head[1] == b"blob":
            size = int(head[2])
            out[r] = data[i:i + size]
            i += size + 1
        else:
            out[r] = None                     # „missing“: v HEAD není (nebo HEAD ještě neexistuje)
    return out


def word_ops(a, b, context=12):
    """Rozdíl dvou textů po slovech: [[op, text]], op je = (beze změny), - (pryč), + (nové).
    Dlouhé úseky beze změny se zkrátí na okolí změny."""
    ta, tb = re.split(r"(\s+)", a), re.split(r"(\s+)", b)
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if tag == "equal":
            ops.append(["=", "".join(ta[i1:i2])])
            continue
        ops.append(["-", "".join(ta[i1:i2])])
        ops.append(["+", "".join(tb[j1:j2])])
    ops = [o for o in ops if o[1]]
    if len(ops) > 1:
        for k, op in enumerate(ops):
            if op[0] != "=":
                continue
            w = op[1].split(" ")
            first, last = k == 0, k == len(ops) - 1
            if first and len(w) > context:
                op[1] = "… " + " ".join(w[-context:])
            elif last and len(w) > context:
                op[1] = " ".join(w[:context]) + " …"
            elif not first and not last and len(w) > 2 * context + 1:
                op[1] = " ".join(w[:context]) + " … " + " ".join(w[-context:])
    return ops


def _block_rows(src):
    """Bloky stránky, jejich porovnávací klíč (markup bez textů v atributech, bílé znaky sloučené) a text."""
    bl = B.blocks(src)
    return bl, [_no_attr_text(_WS.sub(" ", b.inner(src)).strip()) for b in bl], [H.norm(A.block_text(src, b)) for b in bl]


# texty mimo bloky (panel Další texty) ukazuje page_diff zvlášť, v kódu stránky a klíči bloku se nepočítají
_ATTR_TEXT = re.compile(r'(\s(?:%s)\s*=\s*")[^"]*(")' % "|".join(re.escape(a) for a in C.TEXT_ATTRS), re.I)
_SVG_TEXT = re.compile(r"(<(text|tspan)\b[^>]*>)[^<]*(</\2>)", re.I)


_TAG_NAMES = {"img": "obrázek", "input": "pole", "textarea": "pole", "a": "odkaz", "button": "tlačítko",
              "nav": "navigace", "section": "sekce", "svg": "graf", "video": "video", "iframe": "vložené okno",
              "form": "formulář", "dialog": "okno"}


def _no_attr_text(s):
    return _SVG_TEXT.sub(r"\1\3", _ATTR_TEXT.sub(r"\1\2", s))


def _skeleton(src, bl):
    """Stránka bez textů bloků, hodnot hlavičky, JSON-LD a textů mimo bloky: co zbyde, je kód mimo texty."""
    cuts = sorted([(b.node.se, b.node.ie) for b in bl] + list(S.head_spans(src).values()))
    parts, last = [], 0
    for a, z in cuts:
        if a < last:
            continue
        parts.append(src[last:a])
        last = z
    parts.append(src[last:])
    return _WS.sub(" ", _no_attr_text(S.LD_RE.sub(r"\1\3", "".join(parts))))


def _attr_rows(src):
    """Texty mimo bloky v pořadí stránky: [((značka, atribut), text)]."""
    out = []
    for x in C.attr_nodes(src, "handbuilt"):
        for a in x.names:
            try:
                s, z = A._attr_span(src, x.node, a)
                text = C.norm(src[s:z])
            except A.Fail:                           # atribut bez dvojitých uvozovek: hodnota z parseru
                text = C.norm(x.node.attrs.get(a, "").replace("&", "&amp;"))
            out.append(((x.node.tag, a), text))
    return out


def _attr_diffs(head, cur):
    """Rozdíl textů mimo bloky po slovech: [{label, ops}], popisek podle atributu a značky."""
    ah, ac = _attr_rows(head), _attr_rows(cur)
    out = []

    def add(key, old, new):
        tag, a = key
        implied = a in (C.ATTR_TEXT, "alt", "placeholder")      # obrázek, pole a graf řekne už popisek
        label = C.ATTR_LABELS[a] if implied else "%s · %s" % (C.ATTR_LABELS[a], _TAG_NAMES.get(tag, "prvek"))
        out.append({"label": label, "ops": word_ops(old, new)})

    sm = difflib.SequenceMatcher(None, [k for k, _ in ah], [k for k, _ in ac], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                if ah[i][1] != ac[j][1]:
                    add(ac[j][0], ah[i][1], ac[j][1])
            continue
        for i in range(i1, i2):
            add(ah[i][0], ah[i][1], "")
        for j in range(j1, j2):
            add(ac[j][0], "", ac[j][1])
    return out


def _ld_rest(src):
    """JSON-LD bez textů, které ukazují pole SEO (FAQ, texty JSON-LD včetně údajů o vás) a bez poslední
    položky drobečkové navigace (kopie h1): zbydou adresy, ceny, data a struktura."""
    texts = S.ld_texts(src)
    out = []
    for bi, m in enumerate(S.LD_RE.finditer(src)):
        try:
            tree = json.loads(m.group(2))
        except ValueError:
            out.append(m.group(2))
            continue
        for (b, path) in texts:
            if b == bi:
                S._get(tree, path[:-1])[path[-1]] = None
        for _, obj in S._objects(tree):
            if not isinstance(obj, dict):
                continue
            if obj.get("@type") == "FAQPage":
                obj.pop("mainEntity", None)
            elif obj.get("@type") == "BreadcrumbList" and isinstance(obj.get("itemListElement"), list) \
                    and obj["itemListElement"] and isinstance(obj["itemListElement"][-1], dict):
                obj["itemListElement"][-1].pop("name", None)
        out.append(json.dumps(tree, sort_keys=True, ensure_ascii=False))
    return out


def _seo_order(k):
    m = _FAQ.match(k)
    if m:
        return (1, int(m.group(1)), m.group(2))
    keys = [x for x, _, _ in S.HEAD]
    return (0, keys.index(k) if k in keys else len(keys), k)


def page_diff(head, cur):
    """Co se na stránce změnilo proti HEAD: bloky textu (spárované přes SequenceMatcher,
    rozdíl po slovech), pole SEO, texty mimo bloky (panel Další texty) a jednou větou změny
    mimo texty. Konce řádků se nepočítají."""
    head, cur = _lf(head), _lf(cur)
    if head == cur:
        return []
    bh, kh, th = _block_rows(head)
    bc, kc, tc = _block_rows(cur)
    items = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, kh, kc, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        k = min(i2 - i1, j2 - j1)
        pairs = ([(i1 + x, j1 + x) for x in range(k)] + [(i, None) for i in range(i1 + k, i2)]
                 + [(None, j) for j in range(j1 + k, j2)])
        for i, j in pairs:
            old, new = th[i] if i is not None else "", tc[j] if j is not None else ""
            note = ("nový text" if i is None else "smazaný text" if j is None
                    else "změna formátování, odkazu nebo pevných mezer" if old == new else None)
            items.append({"kind": "block", "tag": (bc[j] if j is not None else bh[i]).node.tag,
                          "n": None if j is None else bc[j].n, "note": note,
                          "diffs": [{"label": None, "ops": word_ops(old, new)}]})
    vh, vc = S.values(head), S.values(cur)
    seo = [{"label": S.label(k, cur if k in vc else head), "ops": word_ops(vh.get(k, ""), vc.get(k, ""))}
           for k in sorted(set(vh) | set(vc), key=_seo_order) if vh.get(k) != vc.get(k)]
    if seo:
        items.append({"kind": "seo", "tag": "SEO", "n": None, "note": None, "diffs": seo})
    attrs = _attr_diffs(head, cur)
    if attrs:
        items.append({"kind": "attrs", "tag": "Další texty", "n": None, "note": None, "diffs": attrs})
    other = []
    if _ld_rest(head) != _ld_rest(cur):
        other.append("Mění se i strukturovaná data (JSON-LD) mimo FAQ.")
    if _skeleton(head, bh) != _skeleton(cur, bc):
        other.append("Mění se i kód stránky mimo texty (struktura, odkazy, skripty nebo styly).")
    if other or not items:
        items.append({"kind": "other", "tag": "", "n": None, "diffs": [],
                      "note": " ".join(other) or "Liší se jen bílými znaky."})
    return items


def _entry(e):
    return {k: e.get(k) for k in ("id", "slug", "label", "before", "after", "t", "block")}


def changes(editor):
    """Data panelu Změny: stránky s nepublikovanými úpravami (naposledy upravená první)
    a co se na nich změnilo proti HEAD. Blok a SEO nesou Vrátit poslední úpravy, která na
    ně sáhla; ostatní úpravy stránky (celý zdroj, blok bez rozdílu proti HEAD) jdou zvlášť."""
    j = editor.journal
    entries = j.unpublished()
    reg = editor.registry()
    reps = [e for e in entries if e.get("kind") == "replace"]
    order = []
    for e in reversed(entries):
        if e.get("kind") == "replace":
            continue                        # stránky jen s nahrazením ukáže karta nahrazení
        for f in e["files"]:
            s = editor.site.page_slug(f["path"])
            if s and s in reg and s not in order:
                order.append(s)
    error = None
    try:
        heads = head_blobs(editor.root, [editor.page_rel(s) for s in order])
    except NoHead as e:
        heads, error = {}, "Bez gitu se úpravy nedají porovnat s poslední publikací (%s)." % e
    pages = []
    for slug in order:
        rel = editor.page_rel(slug)
        head = heads.get(rel)
        items = [] if error else page_diff(head.decode("utf-8") if head is not None else "", editor.read_page(slug))
        own = [e for e in entries if e.get("slug") == slug]
        by_key = collections.defaultdict(list)
        for e in own:
            if e.get("block") is not None:
                by_key[e["block"]].append(e)
        taken = set()
        for it in items:
            key = it["kind"] if it["kind"] in ("seo", "attrs") else it["n"] if it["kind"] == "block" else None
            es = by_key.get(key, []) if key is not None else []
            it.update(undo=es[-1]["id"] if es else None, edits=len(es), t=es[-1]["t"] if es else None)
            taken.update(x["id"] for x in es)
        via = sorted({e["slug"] for e in entries if e.get("slug") not in (slug, None)
                      and any(f["path"] == rel for f in e["files"])})
        pages.append({"slug": slug, "kind": reg[slug]["kind"], "new": head is None and not error,
                      "items": items, "edits": [_entry(e) for e in reversed(own) if e["id"] not in taken],
                      "via": via, "count": len(own),
                      "replace": [_entry(e) for e in reps if any(f["path"] == rel for f in e["files"])]})
    replaces = [dict(_entry(e), pages=e.get("pages") or [], files=[f["path"] for f in e["files"]])
                for e in reversed(reps)]
    return {"pages": pages, "replaces": replaces, "count": len(entries), "files": j.unpublished_files(),
            "error": error}


# ── Zahodit ──
def _skel_lits(src, lits):
    """Zdroj bez obsahu literálů: když se shoduje, liší se dvě verze skriptu jen texty."""
    out, last = [], 0
    for lit in lits:
        out.append(src[last:lit.a])
        last = lit.z
    out.append(src[last:])
    return out


class _Lits:
    """Které literály skriptů která úprava změnila, z bajtů „před“ a „po“ v journalu.
    scripts = zdroje generátoru (cesty v repu), jiné soubory úprav jsou stránky."""

    def __init__(self, journal, entries, scripts):
        self.j, self.entries, self.memo, self.parsed = journal, entries, {}, {}
        self.scripts = set(scripts)

    def parse(self, data):
        key = sha(data)
        if key not in self.parsed:
            src = data.decode("utf-8")
            lits = PS.literals(src)
            self.parsed[key] = (src, lits, _skel_lits(src, lits))
        return self.parsed[key]

    def of(self, e):
        """{(skript, index literálu): surový obsah literálu před úpravou} pro literály, které úprava změnila."""
        if e["id"] not in self.memo:
            out = {}
            for f in e["files"]:
                path = f["path"]
                if path not in self.scripts:
                    continue
                b, a = self.j.before_bytes(e, path), self.j.after_bytes(e, path, self.entries)
                if b is None or a is None:
                    raise A.Fail("Skript %s se od úpravy %s změnil i mimo editor, zahodit ji jde jen ručně přes git."
                                 % (path, e.get("label", e["id"])), code="conflict", status=409)
                sb, lb, kb = self.parse(b)
                sa, la, ka = self.parse(a)
                if kb != ka:
                    raise A.Fail("Úprava %s změnila v %s i kód mimo texty, zahodit ji jde jen ručně přes git."
                                 % (e.get("label", e["id"]), path), code="conflict", status=409)
                out.update({(path, i): sb[x.a:x.z] for i, (x, y) in enumerate(zip(lb, la))
                            if sb[x.a:x.z] != sa[y.a:y.z]})
            self.memo[e["id"]] = out
        return self.memo[e["id"]]


def _file_sha(root, rel):
    p = Path(root) / rel
    return sha(p.read_bytes() if p.exists() else None)


def discard(editor, slug, confirm=None):
    """Zahodí nepublikované úpravy stránky (Zahodit v panelu Změny).

    Ruční a owner stránka: zapíšou se bajty z HEAD (git, ne checkout: P20).
    Generovaná: literály skriptů, které úpravy stránky změnily, dostanou zpět surový obsah
    z doby před úpravou. Sdílí-li literál úprava jiné stránky, zahodí se celá i ta (úpravy
    se nedělí) a potvrzení dotčené stránky vypíše. Pak zkušební build, injektory a verify
    jako při uložení. Journal: záznam discard, zahozené úpravy stav discarded.
    Bez platného confirm vrací potvrzení s tokenem, s ním zapisuje."""
    j = editor.journal
    kind = editor.kind(slug)
    rel = editor.page_rel(slug)
    entries = j.entries()
    done = [e for e in entries if e["state"] == "done"]
    seed = [e for e in done if any(f["path"] == rel for f in e["files"])]
    if not seed:
        raise A.Fail("Stránka /%s nemá nepublikované úpravy z editoru, není co zahodit." % slug, code="empty", status=409)
    _no_replace(seed)
    try:
        head = head_blobs(editor.root, [rel])[rel]
    except NoHead as e:
        raise A.Fail("Bez gitu nejde zjistit poslední publikovanou verzi (%s), zahodit nejde." % e, code="nogit", status=409)
    if head is None:
        raise A.Fail("Stránka v gitu (HEAD) ještě není, zahodit nejde.", code="nogit", status=409)
    if kind == "generated":
        covers, writes, state, notes = _discard_generated(editor, slug, entries, done, seed, head)
        _no_replace(covers)
    else:
        covers, writes, state, notes = _discard_file(editor, rel, entries, seed, head)

    pages = sorted({s for e in covers for f in e["files"] for s in [editor.site.page_slug(f["path"])] if s})
    others = sorted({e["slug"] for e in covers if e.get("slug") not in (slug, None)})
    details = ["Vrátí se %s." % plural(len(covers), "úprava", "úpravy", "úprav")]
    for e in covers[:5]:
        what = e.get("label", "")
        if e.get("slug") == slug:
            what = what.split(" · ", 1)[-1]
        details.append("%s: „%s“" % (what, _cut(e["after"])) if e.get("after") else what)
    if len(covers) > 5:
        details.append("… a %s" % plural(len(covers) - 5, "další", "další", "dalších"))
    if others:
        details.append("Úprava sdíleného textu se zahodí celá, takže zmizí i změny na %s."
                       % ", ".join("/" + s for s in others))
    rest = [p for p in pages if p != slug]
    if rest:
        details.append("Znovu se zapíše i %s: %s" % (plural(len(rest), "další stránka", "další stránky", "dalších stránek"),
                                                      ", ".join("/" + p for p in rest[:8]) + (" …" if len(rest) > 8 else "")))
    details += notes
    details.append("Zahození se nedá vrátit.")
    token = hashlib.sha1(json.dumps([slug, [e["id"] for e in covers], sorted(state.items()),
                                     sorted((k, sha(v)) for k, v in writes.items())]).encode("utf-8")).hexdigest()[:16]
    if confirm != token:
        return {"confirm": token, "message": "Zahodit změny stránky /%s?" % slug, "details": details}

    with A.write_lock(editor.root):
        if any(_file_sha(editor.root, p) != h for p, h in state.items()):
            raise A.Fail("Soubory se mezitím změnily. Otevři Změny znovu.", code="stale", status=409)
        e = j.discard(writes, [x["id"] for x in covers], slug=slug, pages=pages, label="/%s · zahození změn" % slug)
    return {"ok": True, "entry": e["id"], "discarded": len(covers), "pages": pages, "files": sorted(writes),
            "notes": notes}


def _no_replace(entries):
    """Nahrazení sahá do mnoha souborů: se stránkou ho zahodit nejde, jen celé přes Vrátit."""
    e = next((x for x in entries if x.get("kind") == "replace"), None)
    if e:
        raise A.Fail("Stránku mění i Najít a nahradit („%s“ → „%s“) na %s. Nejdřív ho vrať v panelu Změny, "
                     "pak stránku zahoď." % (_cut(e.get("before") or "", 40), _cut(e.get("after") or "", 40),
                                             plural(len(e.get("pages") or []), "stránce", "stránkách", "stránkách")),
                     code="conflict", status=409)


def _cut(text, n=60):
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


def _discard_file(editor, rel, entries, seed, head):
    """Ruční a owner stránka: celý soubor z HEAD."""
    j = editor.journal
    other = sorted({f["path"] for e in seed for f in e["files"]} - {rel})
    if other:
        raise A.Fail("Úpravy stránky zapsaly i jiné soubory (%s), zahodit je jde jen ručně přes git." % ", ".join(other),
                     code="conflict", status=409)
    notes = []
    first = j.before_bytes(seed[0], rel)
    if first is not None and _lf(first.decode("utf-8")) != _lf(head.decode("utf-8")):
        notes.append("V souboru byly už před první úpravou změny, které nezapsal editor. Zahodí se taky.")
    if j.after_bytes(seed[-1], rel, entries) is None:
        notes.append("Soubor se od poslední úpravy změnil i mimo editor. Zahodí se i to.")
    return seed, {rel: head}, {rel: _file_sha(editor.root, rel)}, notes


def _discard_generated(editor, slug, entries, done, seed, head):
    """Generovaná stránka: literály zpět, zkušební build dotčených skriptů, build v paměti a kontrola."""
    j, root, site = editor.journal, editor.root, editor.site
    lits = _Lits(j, entries, site.generator_files())
    closure = {e["id"] for e in seed}
    grow = True
    while grow:                              # úpravy, které sdílejí změněný literál, jdou celé
        grow = False
        keys = {k for e in done if e["id"] in closure for k in lits.of(e)}
        paths = {k[0] for k in keys}
        for e in done:
            if e["id"] not in closure and any(f["path"] in paths for f in e["files"]) and keys & set(lits.of(e)):
                closure.add(e["id"])
                grow = True
    covers = [e for e in done if e["id"] in closure]
    target = {}                              # surový obsah před první úpravou, která literál změnila
    for e in covers:
        for k, raw in lits.of(e).items():
            target.setdefault(k, raw)

    texts, state = {}, {}
    for path in sorted({k[0] for k in target}):
        data = (root / path).read_bytes()
        state[path] = sha(data)
        cur, cl, ck = lits.parse(data)
        first = next(e for e in covers if any(k[0] == path for k in lits.of(e)))
        if ck != lits.parse(j.before_bytes(first, path))[2]:
            raise A.Fail("Skript %s se od úprav změnil i mimo texty, zahodit je jde jen ručně přes git." % path,
                         code="conflict", status=409)
        s = cur
        for _, i in sorted((k for k in target if k[0] == path), key=lambda k: -cl[k[1]].a):
            s = s[:cl[i].a] + target[(path, i)] + s[cl[i].z:]
        if s != cur:
            texts[path] = s

    ok, err, outputs = site.generate(texts, site.scripts_for(texts, also=[editor.script_rel(slug)]))
    if not ok:
        raise A.Fail("Build po vrácení textů selhal, nic jsem nezapsal.", code="verify",
                     details=[x for x in err.strip().splitlines() if x.strip()][-6:])
    cover_pages = {s for e in covers for f in e["files"] for s in [site.page_slug(f["path"])] if s}
    writes, fails, foreign = {}, [], []
    for page in sorted(outputs):
        cand = editor.inject(outputs[page], page)
        cur = editor.read_page(page)
        if _lf(cand) == _lf(cur):
            continue
        if page not in cover_pages:
            foreign.append(page)
            continue
        fails += editor.check(page, cand)
        writes[editor.page_rel(page)] = cand.encode("utf-8")
        state[editor.page_rel(page)] = sha(cur.encode("utf-8"))
    if foreign:
        raise A.Fail("Zkušební build mění i stránky, kterých se zahazované úpravy netýkají (%s). %s/ neodpovídá "
                     "zdrojům: spusť %s a zkontroluj git diff."
                     % (", ".join("/" + p for p in foreign[:6]), site.source, site.build_hint()), code="drift", status=409)
    if fails:
        raise A.Fail("Po vrácení textů by stránky neprošly kontrolou buildu, nic jsem nezapsal.", code="verify", details=fails)
    writes.update({p: s.encode("utf-8") for p, s in texts.items()})
    rel = editor.page_rel(slug)
    state.setdefault(rel, _file_sha(root, rel))
    new = writes[rel].decode("utf-8") if rel in writes else editor.read_page(slug)
    notes = []
    if _lf(new) != _lf(head.decode("utf-8")):
        notes.append("Stránka se ani po zahození nevrátí přesně do stavu poslední publikace, zbytek nezapsal editor.")
    return covers, writes, state, notes


# ── kontrola při startu serveru ──
def _lf(s):
    return s.replace("\r\n", "\n")


def drift(editor):
    """Odpovídají stránky zdrojům? Plný zkušební build generátoru do tempu a build v paměti se
    porovnají se stránkami a každá stránka projde kontrolou. Vrací seznam problémů pro banner."""
    reg = editor.registry()
    gen, problems = {}, []
    if editor.site.scripts():
        ok, err, outputs = editor.site.generate()
        if ok:
            gen = outputs
        else:
            problems.append("Zkušební build generovaných stránek selhal: %s" % _tail(err, 2))
    differ, fails = [], []
    for slug, v in sorted(reg.items()):
        try:
            cur = editor.read_page(slug)
        except OSError:
            problems.append("Chybí %s." % editor.page_rel(slug))
            continue
        base = gen.get(slug) if v["kind"] == "generated" else cur
        if base is not None and _lf(editor.inject(base, slug)) != _lf(cur):
            differ.append(slug)
        fails += editor.check(slug, cur)
    if differ:
        problems.append("%s/ neodpovídá zdrojům u %d %s (%s). Spusť %s a zkontroluj git diff."
                        % (editor.site.source, len(differ), "stránky" if len(differ) == 1 else "stránek",
                           ", ".join("/" + s for s in differ[:5]) + (" …" if len(differ) > 5 else ""),
                           editor.site.build_hint()))
    if fails:
        problems.append("Verify hlásí %s, první: %s" % (plural(len(fails), "chybu", "chyby", "chyb"), fails[0]))
    return problems
