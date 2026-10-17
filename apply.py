# -*- coding: utf-8 -*-
"""Uložení úpravy jako transakce. Do repa se nezapíše nic, dokud výsledek
neprojde injektory a verify stejně jako při buildu.

  1. nový zdroj v paměti (splice bloku, svázané kopie v JSON-LD)
  2. build stránky v paměti a kontrola nad kandidátem (adaptér webu: inject, check)
  3. kontrola efektu: změnilo se jen to, co uživatel napsal; jinak potvrzení
  4. zápis pod build lockem přes journal (atomicky, s bajty „před“)

Generovaná stránka (skript stránky a sdílené soubory generátoru, adapter.Site.page_sources):
úprava se zapíše do literálů skriptu podle proveniance (provenance.py), zkušební build skriptu
(nebo celého generátoru, když se mění sdílený soubor) do tempu dá nové HTML všech dotčených
stránek a ty projdou stejným buildem v paměti a kontrolou. Journal pak nese skript i všechny
změněné stránky, takže Vrátit vrátí všechno najednou.

Panel SEO (seo.py) jde stejnou cestou: title, description, og/twitter a FAQ
v JSON-LD jako prostý text, u generované stránky do literálu skriptu, zkušební
build, build v paměti, kontrola a jeden záznam journalu.

Panel Další texty (save_attrs): alt, title, aria-label, placeholder a text v SVG
(classify.attr_nodes) jako prostý text do stránky, sdílené menu a patička do
šablony s přestavěním všech jejích stránek a potvrzením jako sdílený blok.
"""
import collections
import contextlib
import hashlib
import json
import re
import threading
import time
from pathlib import Path

import adapter as AD
import blocks as B
import buildlock
import classify as C
import gitsync as G
import htmltext as H
import provenance as PV
import pysource as P
import seo as S
from fail import Fail
from journal import Conflict, Journal

LD_RE = re.compile(r'(<script type="application/ld\+json">)(.*?)(</script>)', re.S)


LOCK_PART_ROLE = "Menu a patičku mění admin: změna se propíše na všechny stránky, i na owner stránky."
LOCK_ENTITY_ROLE = "Údaje o vás a výchozí témata mění admin: změna se propíše na všechny stránky, které je nesou."
_ARIA_CURRENT = re.compile(r'[ \t\r\n]+aria-current="[^"]*"')    # počítá build (adaptér), v šabloně není
CRLF, LF = chr(13) + chr(10), chr(10)
_PART_PLANS = collections.OrderedDict()   # token → plán sdílené změny (potvrzení a uložení bez druhého buildu)

UNLOCK_S = 30 * 60                       # odemčení owner stránky platí pro uživatele 30 minut
LOCAL = {"login": "local", "role": "admin", "name": None, "email": None}   # lokální editor bez přihlášení
_ACTOR = threading.local()


@contextlib.contextmanager
def acting(user, ip=None):
    """Kdo právě pracuje (požadavek běží ve vlastním vlákně): journal, odemčení, audit, autor."""
    prev = getattr(_ACTOR, "u", None)
    _ACTOR.u = dict(user or LOCAL, ip=ip)
    try:
        yield _ACTOR.u
    finally:
        _ACTOR.u = prev


def actor():
    return getattr(_ACTOR, "u", None) or dict(LOCAL, ip=None)


def _actor_name():
    a = actor()
    return None if a["login"] == LOCAL["login"] else (a.get("name") or a["login"])


_LOCKS = {}


def _publish_lock(root):
    """Zámek publikace v repu (gitsync), None mimo git (testy nad kopií bez .git)."""
    key = str(root)
    if key not in _LOCKS:
        try:
            _LOCKS[key] = G.Git(root).lock_path() if (Path(root) / ".git").exists() else None
        except G.GitError:
            _LOCKS[key] = None
    return _LOCKS[key]


@contextlib.contextmanager
def write_lock(root, who="editor textů"):
    """Zápis editoru: build lock a pod ním zámek publikace, stejné pořadí jako Publisher. sync
    (gitsync.py, sync.sh) tak nikdy neposune checkout mezi kontrolou zdroje a zápisem."""
    with buildlock.hold(who, wait=30):
        path = _publish_lock(root)
        lock = G.Lock(path, wait=30, who=who) if path else contextlib.nullcontext()
        try:
            lock.__enter__()
        except G.Busy as e:
            raise buildlock.Busy(str(e)) from None
        try:
            yield
        finally:
            lock.__exit__(None, None, None)


def author(user):
    """„Jméno <e-mail>“ uživatele pro git, nebo None, když nemá jméno a e-mail."""
    if user and user.get("name") and user.get("email"):
        return "%s <%s>" % (user["name"], user["email"])
    return None


class Editor:
    """root = repo webu, journal_dir = journal úprav (výchozí cms.toml [editor] journal),
    site = adaptér webu (výchozí adapter.load(root))."""

    def __init__(self, root, journal_dir=None, site=None):
        self.root = Path(root)
        self.site = site or AD.load(self.root)
        self.dist = self.site.dist
        self.journal = Journal(self.root, journal_dir or self.root / self.site.journal_rel, who=_actor_name)
        self._unlocks = {}              # login → {slug: konec odemčení (epoch)}
        self._audit = threading.Lock()
        self._prov = {}                 # slug → PageProv (platí, dokud sedí otisk zdrojů)
        self._seo = {}                  # slug → SeoProv (literály polí SEO, stejný otisk)

    # ── odemčení owner stránek (per uživatel, s vypršením) ──
    @property
    def unlocked(self):
        """Owner stránky odemčené pro aktuálního uživatele (jen ke čtení, mění se přes unlock a relock)."""
        now = time.time()
        return frozenset(s for s, t in self._unlocks.get(actor()["login"], {}).items() if t > now)

    def unlock(self, slug, seconds=UNLOCK_S):
        """Odemkne owner stránku jen aktuálnímu uživateli. Vrací konec odemčení (epoch)."""
        until = time.time() + seconds
        self._unlocks.setdefault(actor()["login"], {})[slug] = until
        return until

    def relock(self, slug):
        self._unlocks.get(actor()["login"], {}).pop(slug, None)

    def unlocked_until(self, slug):
        t = self._unlocks.get(actor()["login"], {}).get(slug)
        return t if t and t > time.time() else None

    # ── audit: kdo co v editoru udělal (soubor v journalu, mimo repo) ──
    def audit(self, action, **data):
        a = actor()
        rec = dict({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "user": a["login"], "ip": a.get("ip"),
                    "action": action}, **data)
        line = json.dumps(rec, ensure_ascii=False) + chr(10)
        with self._audit:
            with open(self.journal.dir / "audit.jsonl", "a", encoding="utf-8") as f:
                f.write(line)

    # ── stránky ──
    def registry(self):
        return self.site.registry()

    def kind(self, slug):
        reg = self.registry()
        if slug not in reg:
            raise Fail("Neznámá stránka /%s." % slug, status=404)
        return reg[slug]["kind"]

    def page_rel(self, slug):
        return self.site.page_rel(slug)

    def read_page(self, slug):
        with open(self.root / self.page_rel(slug), encoding="utf-8", newline="") as f:
            return f.read()

    def lock_reason(self, slug):
        """Proč je celá stránka zamčená, nebo None."""
        k = self.kind(slug)
        if k == "generated":
            pv = self.provenance(slug)
            if pv.error:
                return "Zkušební build generované stránky selhal, úpravy jsou zamčené: %s" % pv.error[-300:]
            return None
        if k == "owner" and slug not in self.unlocked:
            return "Owner stránka (ADR 0003): nejdřív ji odemkni (jen role admin, na 30 minut)."
        return None

    def source_lock(self, slug):
        """Proč nejde uložit celý zdroj stránky (generovaná stránka má zdroj ve skriptu)."""
        if self.kind(slug) == "generated":
            return ("Generovaná stránka: zdrojem je %s, %s by build přepsal. "
                    "Texty uprav v editoru, skript přes Clauda." % (self.script_rel(slug), self.page_rel(slug)))
        return self.lock_reason(slug)

    # ── generované stránky ──
    def script_rel(self, slug):
        """Skript generované stránky (cesta v repu)."""
        return self.registry()[slug]["source"]

    def provenance(self, slug, src=None):
        """Proveniance bloků generované stránky, cache podle otisku stránky a zdrojů."""
        src = self.read_page(slug) if src is None else src
        key = PV.cache_key(src, PV.sources(self.site, slug))
        pv = self._prov.get(slug)
        if pv is None or pv.key != key:
            pv = PV.probe(self.site, slug, src)
            self._prov[slug] = pv
        return pv

    # ── build v paměti ──
    def inject(self, s, slug, sources=None):
        """Stránka po buildu v paměti (adaptér webu). sources = {slug: obsah} stránek, ze kterých
        se přebírá obsah a které ještě nejsou na disku."""
        try:
            return self.site.inject(s, slug, sources)
        except AD.SiteError as e:
            raise Fail(str(e), code="verify", status=422)

    def faq_followers(self, cands, fails):
        """cands = {slug: nový obsah}. Stránky, které převzaly obsah (FAQ) ze změněné
        stránky (adapter.Site.followers), se musí zapsat s ní: publish spouští
        build a ten nesmí změnit soubor mimo journal. Vrací {slug: (teď, nově)}."""
        out = {}
        for src_slug, text in cands.items():
            for page in self.site.followers(src_slug):
                if page in cands or page in out:
                    continue
                cur = self.read_page(page)
                cand = self.inject(cur, page, sources={src_slug: text})
                if cand != cur:
                    fails += self.check(page, cand)
                    out[page] = (cur, cand)
        return out

    def _stale_followers(self, followers):
        return any(self.read_page(p) != cur for p, (cur, _) in followers.items())

    def check(self, slug, s):
        return self.site.check(slug, s)

    # ── uložení bloku ──
    def save_block(self, slug, n, bhash, phash, after_html, confirm=None, owners=False):
        """owners = uživatel v potvrzení sdílené změny zaškrtl, že se mění i owner stránky."""
        kind = self.kind(slug)
        src = self.read_page(slug)
        block = B.find_block(src, n, bhash, phash)
        if block is not None and block.part:
            return self._save_part(slug, src, block, after_html, confirm, owners)
        why = self.lock_reason(slug)
        if why:
            raise Fail(why, code="locked", status=403)
        if block is None:
            raise Fail("Stránka se mezitím změnila. Načti ji znovu.", code="stale", status=409)
        if block.lock:
            raise Fail(block.lock, code="locked", status=403)
        try:
            new_inner = H.reconstruct(src, block, after_html)
        except H.EditError as e:
            raise Fail(e.msg, code=e.code, status=422)
        old_inner = block.inner(src)
        if new_inner == old_inner:
            return {"ok": True, "unchanged": True}
        token = hashlib.sha1(("%s|%d|%s|%s" % (slug, n, bhash, after_html)).encode("utf-8")).hexdigest()[:16]
        if kind == "generated":
            return self._save_generated(slug, src, block, new_inner, confirm, token)

        cand = src[:block.node.se] + new_inner + src[block.node.ie:]
        cand, linked = link_jsonld(cand, H.norm(block_text(src, block)),
                                   H.plain(block_text(src, block, new_inner)))
        cand = self.inject(cand, slug)
        fails = self.check(slug, cand)
        followers = self.faq_followers({slug: cand}, fails)
        if fails:
            raise Fail("Úprava by neprošla kontrolou buildu, nic jsem nezapsal.", code="verify", details=fails)

        after_blocks = B.blocks(cand)
        target = next((b for b in after_blocks if b.n == block.n), None)
        if target is None or len(after_blocks) != len(B.blocks(src)):
            raise Fail("Úprava změnila strukturu stránky, nic jsem nezapsal.", code="structure")
        got = target.inner(cand)
        notes = []
        if got != new_inner and H.norm(got) != H.norm(new_inner):
            notes.append("Build text ještě upravil: „%s“" % H.norm(got))
        if notes and confirm != token:
            return {"confirm": token, "message": "Po buildu vypadá text jinak, než jsi napsal.", "details": notes}

        rel = self.page_rel(slug)
        with write_lock(self.root):
            if self.read_page(slug) != src or self._stale_followers(followers):
                raise Fail("Stránka se mezitím změnila. Načti ji znovu.", code="stale", status=409)
            new_text = H.norm(block_text(src, block, got))
            files = {rel: cand.encode("utf-8")}
            files.update({self.page_rel(p): c.encode("utf-8") for p, (_, c) in followers.items()})
            entry = self.journal.write(files, slug=slug, pages=[slug] + sorted(followers), block=block.n,
                                       label="/%s · %s" % (slug, block.node.tag),
                                       before=H.norm(block_text(src, block))[:300], after=new_text[:300],
                                       snippet=_snippet(new_text))
        ann = self._annotated(slug, cand)
        ablock = next(b for b in B.blocks(ann) if b.n == block.n)
        return {"ok": True, "entry": entry["id"], "inner": ablock.inner(ann), "hash": target.hash(cand),
                "page": B.page_hash(cand), "linked": linked, "pages": sorted(followers)}

    def _save_generated(self, slug, src, block, new_inner, confirm, token):
        pv = self.provenance(slug, src)
        bp = pv.blocks.get(block.n)
        if bp is None or bp.unmapped:
            raise Fail(PV.LOCK_UNMAPPED, code="locked", status=403)
        if bp.injected():
            raise Fail(PV.LOCK_INJECTED, code="locked", status=403)
        cand0 = src[:block.node.se] + new_inner + src[block.node.ie:]
        blocks0 = B.blocks(cand0)
        target0 = next((b for b in blocks0 if b.n == block.n), None)
        if target0 is None or len(blocks0) != len(B.blocks(src)):
            raise Fail("Úprava změnila strukturu stránky, nic jsem nezapsal.", code="structure")
        old_segs, new_segs = PV.segment_raws(src, block), PV.segment_raws(cand0, target0)
        if len(old_segs) != len(new_segs):
            raise Fail("Úprava změnila strukturu bloku, nic jsem nezapsal.", code="structure")

        # 1. změněné úseky do hodnot literálů (v jednom literálu odzadu, ať sedí pozice)
        edits = []
        for si, (o, nw) in enumerate(zip(old_segs, new_segs)):
            if o == nw:
                continue
            if bp.segs[si] is None:
                raise Fail("Tenhle úsek bloku editor ve zdroji nenašel, změnu nezapíšu.", code="locked", status=403)
            edits.append((bp.segs[si], o, nw))
        values = {}
        for seg, o, nw in sorted(edits, key=lambda x: -x[0].k):
            key = (seg.file, seg.li)
            val = values.get(key, pv.lits[seg.file][seg.li].value)
            try:
                values[key] = PV.splice(val, seg, o, nw, PV.needs_strip(pv, seg, o, bp))
            except PV.SpliceError as e:
                raise Fail(str(e), code="locked", status=422)
        changed = {}
        for fk, text in pv.srcs.items():
            s = text
            for (f2, li), val in sorted(values.items(), key=lambda x: -pv.lits[x[0][0]][x[0][1]].a):
                if f2 == fk:
                    try:
                        s = P.replace(s, pv.lits[fk][li], val)
                    except ValueError as e:
                        raise Fail("Do zdroje se text nepodařilo zapsat (%s), nic jsem nezapsal." % e,
                                   code="verify", status=422)
            if s != text:
                changed[fk] = s
        if not changed:
            return {"ok": True, "unchanged": True}

        # 2. zkušební build dotčených skriptů, build v paměti a kontrola každé změněné stránky
        ok, err, outputs = self.site.generate(changed, self.site.scripts_for(changed))
        if not ok:
            raise Fail("Build se změněným textem selhal, nic jsem nezapsal.", code="verify",
                       details=[x for x in err.strip().splitlines() if x.strip()][-6:])
        writes, fails, built = {}, [], {}
        for page in sorted(outputs):
            cand = self.inject(outputs[page], page)
            if cand == self.read_page(page):
                continue
            fails += self.check(page, cand)
            writes[self.page_rel(page)] = built[page] = cand
        followers = self.faq_followers(built, fails)
        writes.update({self.page_rel(p): c for p, (_, c) in followers.items()})
        if fails:
            raise Fail("Úprava by neprošla kontrolou buildu, nic jsem nezapsal.", code="verify", details=fails)
        cand = writes.get(self.page_rel(slug))
        if cand is None:
            raise Fail("Úprava se po buildu na stránce neprojevila, nic jsem nezapsal.", code="verify", status=422)
        after_blocks = B.blocks(cand)
        target = next((b for b in after_blocks if b.n == block.n), None)
        if target is None or len(after_blocks) != len(blocks0):
            raise Fail("Úprava změnila strukturu stránky, nic jsem nezapsal.", code="structure")

        # 3. co všechno se mění: jiný text než napsal, další bloky, hlavička, další stránky
        got = target.inner(cand)
        notes = []
        if got != new_inner and H.norm(got) != H.norm(new_inner):
            notes.append("Build text ještě upravil: „%s“" % H.norm(got))
        others = [H.norm(b2.inner(cand)) for b1, b2 in zip(B.blocks(src), after_blocks)
                  if b1.n != block.n and b1.inner(src) != b2.inner(cand)]
        if others:
            notes.append("Na téhle stránce se mění i další text (%d): „%s“" % (len(others), others[0][:120]))
        if src[:src.find("<body")] != cand[:cand.find("<body")]:
            notes.append("Mění se i hlavička stránky (title, meta nebo JSON-LD): text se tam ukládá prostý.")
        pages = sorted(p for p in set(outputs) | set(followers) if self.page_rel(p) in writes and p != slug)
        if pages:
            notes.append("Text je sdílený: propíše se i na %s — %s"
                         % (_plural(len(pages), "další stránku", "další stránky", "dalších stránek"),
                            ", ".join("/" + p for p in pages[:8]) + (" …" if len(pages) > 8 else "")))
        if notes and confirm != token:
            return {"confirm": token, "message": "Úprava změní víc než tenhle blok.", "details": notes}

        # 4. zápis: skript(y) i všechny změněné stránky v jednom záznamu journalu
        with write_lock(self.root):
            if (self.read_page(slug) != src or self._stale_followers(followers)
                    or PV.sources(self.site, slug) != pv.srcs):
                raise Fail("Stránka nebo skript se mezitím změnily. Načti stránku znovu.", code="stale", status=409)
            all_writes = {fk: text.encode("utf-8") for fk, text in changed.items()}
            all_writes.update({rel: text.encode("utf-8") for rel, text in writes.items()})
            new_text = H.norm(block_text(src, block, got))
            entry = self.journal.write(all_writes, slug=slug, pages=[slug] + pages, block=block.n,
                                       label="/%s · %s" % (slug, block.node.tag),
                                       before=H.norm(block_text(src, block))[:300], after=new_text[:300],
                                       snippet=_snippet(new_text))
        ann = self._annotated(slug, cand)
        ablock = next(b for b in B.blocks(ann) if b.n == block.n)
        return {"ok": True, "entry": entry["id"], "inner": ablock.inner(ann), "hash": target.hash(cand),
                "page": B.page_hash(cand), "linked": 0, "pages": pages, "files": sorted(changed)}

    # ── sdílený blok menu a patičky (Block.part, ADR 0003 dodatek) ──
    def _save_part(self, slug, src, block, after_html, confirm, owners):
        """Úprava bloku, který stránka nese ze sdíleného souboru: zapíše se do souboru (šablony)
        a přestaví všechny jeho stránky. Uložení vždy potvrdí uživatel (první volání vrací souhrn
        s tokenem); mění-li změna owner stránky, chce i owners (zaškrtnutí v dialogu), odemčení ne."""
        if actor()["role"] != "admin":
            raise Fail(LOCK_PART_ROLE, code="role", status=403)
        rel = block.part
        tpl = PV.read(self.root, rel)
        mine = [b.n for b in B.blocks(src) if b.part == rel]
        tblocks = B.blocks(tpl, part=rel)
        if len(mine) != len(tblocks) or block.n not in mine:
            raise Fail("Sdílená část stránky neodpovídá %s, nic jsem nezapsal. Spusť %s."
                       % (rel, self.site.build_hint()), code="drift", status=409)
        tb = tblocks[mine.index(block.n)]
        if _page_part(block.inner(src)) != tb.inner(tpl):
            raise Fail("Text na stránce neodpovídá %s, nic jsem nezapsal. Spusť %s."
                       % (rel, self.site.build_hint()), code="drift", status=409)
        try:
            new_inner = _page_part(H.reconstruct(src, block, after_html))
        except H.EditError as e:
            raise Fail(e.msg, code=e.code, status=422)
        if new_inner == tb.inner(tpl):
            return {"ok": True, "unchanged": True}
        new_tpl = tpl[:tb.node.se] + new_inner + tpl[tb.node.ie:]
        token = hashlib.sha1(("part|%s|%d|%s|%s" % (slug, block.n, block.hash(src), after_html))
                             .encode("utf-8")).hexdigest()[:16]
        key = (str(self.root), token)
        plan = self._cached_plan(key, {rel: new_tpl})
        cand = plan["writes"].get(self.page_rel(slug))
        target = next((b for b in B.blocks(cand) if b.n == block.n), None) if cand else None
        if target is None or target.part != rel:
            raise Fail("Úprava změnila strukturu stránky, nic jsem nezapsal.", code="structure")
        before = H.norm(block_text(src, block))
        after = H.norm(block_text(cand, target))
        reg = self.registry()
        pages = plan["pages"]
        own = [p for p in pages if reg[p]["kind"] == "owner"]
        if confirm != token:
            notes = []
            got = target.inner(cand)
            if _page_part(got) != new_inner and H.norm(got) != H.norm(new_inner):
                notes.append("Build text ještě upravil: „%s“" % after)
            return {"confirm": token, "part": rel, "message": "Uložit sdílenou změnu?", "before": before,
                    "after": after, "pages": [{"slug": p, "kind": reg[p]["kind"]} for p in pages],
                    "owners": own, "details": notes}
        if own and not owners:
            raise Fail("Změna mění i owner stránky (%s), potvrď je v dialogu." % ", ".join("/" + p for p in own),
                       code="owners", status=403)
        with write_lock(self.root):
            if not self._fresh(plan["state"]):
                _PART_PLANS.pop(key, None)
                raise Fail("Stránky nebo %s se mezitím změnily. Načti stránku znovu." % rel, code="stale", status=409)
            entry = self.journal.write({k: v.encode("utf-8") for k, v in plan["writes"].items()}, slug=None,
                                       kind="replace", pages=pages, block=None, part=rel,
                                       label="Sdílený text · %s" % rel, before=before[:300], after=after[:300],
                                       snippet=_snippet(after))
        _PART_PLANS.pop(key, None)
        ann = self._annotated(slug, cand)
        ablock = next(b for b in B.blocks(ann) if b.n == block.n)
        return {"ok": True, "entry": entry["id"], "inner": ablock.inner(ann), "hash": target.hash(cand),
                "page": B.page_hash(cand), "linked": 0, "pages": pages, "files": [rel], "part": rel}

    def _fresh(self, state):
        return all(_sha_file(self.root, p) == h for p, h in state.items())

    def _cached_plan(self, key, texts, own=None, reach=None):
        """Plán sdílené změny z cache (potvrzení a uložení bez druhého buildu), dokud sedí soubory."""
        plan = _PART_PLANS.get(key)
        if plan is None or not self._fresh(plan["state"]):
            plan = self._part_plan(texts, own, reach)
            _PART_PLANS[key] = plan
            while len(_PART_PLANS) > 4:
                _PART_PLANS.popitem(last=False)
        return plan

    def _annotated(self, slug, s):
        """Stránka s anotací bloků a textů mimo bloky jako v editačním pohledu (inner uloženého bloku
        pro editor, ať obrázek v bloku dál otevře popover)."""
        root = B.parse(s)
        part_lock = None if actor()["role"] == "admin" else LOCK_PART_ROLE
        marks = C.attr_marks(C.attr_nodes(s, self.kind(slug), self.lock_reason(slug), part_lock, root))
        return B.annotate(s, B.blocks(s, root), None, marks)

    def _part_plan(self, texts, own=None, reach=None):
        """Stránky sdílených souborů texts ({cesta v repu: nový obsah}) přestavěné s novým obsahem:
        ruční a owner buildem v paměti pod overlay, generované zkušebním buildem generátoru, k tomu
        stránky s převzatým FAQ. own = {slug: zdroj} stránek s vlastní změnou, která se sestaví
        s nimi (panel Další texty), reach = stránky, kterých se změna týká, místo stránek sdílených
        souborů (údaje o vás, adapter.Site.entity_reach). Nic nezapisuje. Vrací {writes: {cesta v repu: text}, pages: [slug],
        state: {cesta: sha souboru}}."""
        site = self.site
        reg = self.registry()
        own = own or {}
        if reach is None:
            reach = set().union(*(site.partial_pages(rel) for rel in texts))
        reached = sorted(set(reach) | set(own))
        state = {rel: _sha_file(self.root, rel) for rel in texts}
        fails, built = [], {}
        with site.overlay(texts):
            for p in reached:
                if reg[p]["kind"] == "generated":
                    continue
                prel = self.page_rel(p)
                state[prel] = _sha_file(self.root, prel)
                cur = self.read_page(p)
                cand = self.inject(own.get(p, cur), p)
                if cand != cur:
                    fails += self.check(p, cand)
                    built[p] = cand
            if any(reg[p]["kind"] == "generated" for p in reached):
                ok, err, outputs = site.generate(texts, None)
                if not ok:
                    raise Fail("Build se změněným textem selhal, nic jsem nezapsal.", code="verify",
                               details=[x for x in err.strip().splitlines() if x.strip()][-6:])
                foreign = []
                for p in sorted(outputs):
                    prel = self.page_rel(p)
                    cur = self.read_page(p)
                    cand = self.inject(outputs[p], p)
                    if cand == cur:
                        continue
                    if p not in reached:
                        foreign.append(p)
                        continue
                    state[prel] = _sha_file(self.root, prel)
                    fails += self.check(p, cand)
                    built[p] = cand
                if foreign:
                    raise Fail("Zkušební build mění i stránky, kterých se změna netýká (%s). %s/ neodpovídá "
                               "zdrojům: spusť %s a zkontroluj git diff."
                               % (", ".join("/" + p for p in foreign[:6]), site.source, site.build_hint()),
                               code="drift", status=409)
            followers = self.faq_followers(built, fails)
        if fails:
            raise Fail("Úprava by neprošla kontrolou buildu, nic jsem nezapsal.", code="verify", details=fails)
        writes = dict(texts)
        writes.update({self.page_rel(p): c for p, c in built.items()})
        for p, (_, c) in followers.items():
            writes[self.page_rel(p)] = c
            state[self.page_rel(p)] = _sha_file(self.root, self.page_rel(p))
        return {"writes": writes, "pages": sorted(set(built) | set(followers)), "state": state}

    # ── texty mimo bloky: atributy a text v SVG (panel Další texty, B7) ──
    def attr_nodes(self, slug, src, root=None):
        """classify.attr_nodes pro aktuálního uživatele: zámek stránky a role jako v editačním pohledu."""
        part_lock = None if actor()["role"] == "admin" else LOCK_PART_ROLE
        return C.attr_nodes(src, self.kind(slug), self.lock_reason(slug), part_lock, root)

    def save_attrs(self, slug, changes, confirm=None, owners=False):
        """changes = [{i, a, old, new}]: i = pořadí prvku (data-ke-a), a = atribut nebo C.ATTR_TEXT,
        old = hodnota, kterou editor ukázal (jinak 409). Vlastní texty stránky se zapíšou do ní,
        sdílené (menu, patička) do šablony a přestaví se všechny její stránky; to uživatel potvrdí
        (první volání vrací souhrn s tokenem), owner stránky zaškrtnutím (owners). Ukládá se prostý
        text, rovné uvozovky jako „…“."""
        src = self.read_page(slug)
        root = B.parse(src)
        nodes = self.attr_nodes(slug, src, root)
        rows, seen = [], set()
        for c in changes:
            i, a = int(c["i"]), str(c["a"])
            x = nodes[i] if 0 <= i < len(nodes) else None
            if x is None or a not in x.names or (i, a) in seen:
                raise Fail("Text na stránce už není tam, kde byl. Načti stránku znovu.", code="stale", status=409)
            seen.add((i, a))
            span = _attr_span(src, x.node, a)
            old = C.norm(src[span[0]:span[1]])
            if old != _squash(c["old"]):
                raise Fail("%s „%s“ se mezitím změnil. Načti stránku znovu." % (C.ATTR_LABELS[a], _cut(old)),
                           code="stale", status=409)
            if x.lock:
                raise Fail(x.lock, code="role" if x.lock == LOCK_PART_ROLE else "locked", status=403)
            new = _plain(c["new"])
            if not C.has_word(new):
                raise Fail("%s: text bez slova uložit nejde (prázdný popisek obrázku nastaví Claude Code ve zdroji)."
                           % C.ATTR_LABELS[a], code="empty", status=422)
            if "<" in new or ">" in new:
                raise Fail("Další texty se ukládají prostým textem, bez znaků < a >.", code="markup", status=422)
            if new != old:
                rows.append({"i": i, "a": a, "x": x, "span": span, "old": old, "new": new})
        if not rows:
            return {"ok": True, "unchanged": True}
        local = [r for r in rows if not r["x"].part]
        cand0 = _splice_attrs(src, [(r["span"], _attr_enc(r["a"], r["new"])) for r in local])
        texts = {rel: self._attr_template(rel, nodes, [r for r in rows if r["x"].part == rel])
                 for rel in sorted({r["x"].part for r in rows if r["x"].part})}
        token = hashlib.sha1(json.dumps(["attrs", slug, [(r["i"], r["a"], r["new"]) for r in rows]],
                                        ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        if texts:
            return self._save_attrs_part(slug, src, nodes, rows, cand0 if local else None, texts, token, confirm, owners)

        rel = self.page_rel(slug)
        cand = self.inject(cand0, slug)
        fails = self.check(slug, cand)
        followers = self.faq_followers({slug: cand}, fails)
        if fails:
            raise Fail("Úprava by neprošla kontrolou buildu, nic jsem nezapsal.", code="verify", details=fails)
        notes = _attr_effect(cand, nodes, rows)
        if notes and confirm != token:
            return {"confirm": token, "message": "Po buildu vypadá text jinak, než jsi napsal.", "details": notes}
        with write_lock(self.root):
            if self.read_page(slug) != src or self._stale_followers(followers):
                raise Fail("Stránka se mezitím změnila. Načti ji znovu.", code="stale", status=409)
            files = {rel: cand.encode("utf-8")}
            files.update({self.page_rel(p): c.encode("utf-8") for p, (_, c) in followers.items()})
            entry = self.journal.write(files, slug=slug, pages=[slug] + sorted(followers), block="attrs",
                                       label="/%s · další texty" % slug, before=_attr_sum(rows, "old"),
                                       after=_attr_sum(rows, "new"), snippet=_snippet(rows[-1]["new"]))
        return _attr_result(entry, src, cand, rows, files=[rel], pages=sorted(followers))

    def _attr_template(self, rel, nodes, rows):
        """Šablona rel se změnami sdílených textů rows (prvky šablony v pořadí odpovídají prvkům
        stránky z rel, díry stránky v šabloně prvky nemají)."""
        tpl = PV.read(self.root, rel)
        tnodes = C.attr_nodes(tpl, "handbuilt", part=rel)
        mine = [x for x in nodes if x.part == rel]
        if len(mine) != len(tnodes) or any(x.names != t.names for x, t in zip(mine, tnodes)):
            raise Fail("Sdílená část stránky neodpovídá %s, nic jsem nezapsal. Spusť %s."
                       % (rel, self.site.build_hint()), code="drift", status=409)
        edits = []
        for r in rows:
            t = tnodes[mine.index(r["x"])]
            span = _attr_span(tpl, t.node, r["a"])
            if C.norm(tpl[span[0]:span[1]]) != r["old"]:
                raise Fail("Text na stránce neodpovídá %s, nic jsem nezapsal. Spusť %s."
                           % (rel, self.site.build_hint()), code="drift", status=409)
            edits.append((span, _attr_enc(r["a"], r["new"])))
        return _splice_attrs(tpl, edits)

    def _save_attrs_part(self, slug, src, nodes, rows, cand0, texts, token, confirm, owners):
        """Další texty se sdíleným textem: šablony texts a s nimi stránka (cand0 = vlastní změny
        stránky, nebo None) přestavěné jako sdílený blok (_part_plan), jeden záznam journalu."""
        if actor()["role"] != "admin":
            raise Fail(LOCK_PART_ROLE, code="role", status=403)
        key = (str(self.root), token)
        plan = self._cached_plan(key, texts, {slug: cand0} if cand0 is not None else None)
        cand = plan["writes"].get(self.page_rel(slug))
        if cand is None:
            raise Fail("Úprava se po buildu na stránce neprojevila, nic jsem nezapsal.", code="verify", status=422)
        notes = _attr_effect(cand, nodes, rows)
        reg = self.registry()
        pages = plan["pages"]
        own = [p for p in pages if reg[p]["kind"] == "owner"]
        parts = ", ".join(sorted(texts))
        if confirm != token:
            return {"confirm": token, "parts": sorted(texts), "message": "Uložit sdílenou změnu?",
                    "pages": [{"slug": p, "kind": reg[p]["kind"]} for p in pages], "owners": own, "details": notes,
                    "changes": [{"i": r["i"], "a": r["a"], "old": r["old"], "new": r.get("got", r["new"]),
                                 "part": r["x"].part} for r in rows]}
        if own and not owners:
            raise Fail("Změna mění i owner stránky (%s), potvrď je v dialogu." % ", ".join("/" + p for p in own),
                       code="owners", status=403)
        shared = [r for r in rows if r["x"].part]
        with write_lock(self.root):
            if self.read_page(slug) != src or not self._fresh(plan["state"]):
                _PART_PLANS.pop(key, None)
                raise Fail("Stránky nebo %s se mezitím změnily. Načti stránku znovu." % parts, code="stale", status=409)
            entry = self.journal.write({k: v.encode("utf-8") for k, v in plan["writes"].items()}, slug=None,
                                       kind="replace", pages=pages, block=None, part=parts,
                                       label="Další texty · %s" % parts, before=_attr_sum(rows, "old"),
                                       after=_attr_sum(rows, "new"), snippet=_snippet(shared[-1]["new"]))
        _PART_PLANS.pop(key, None)
        files = sorted(texts) + ([self.page_rel(slug)] if cand0 is not None else [])
        return _attr_result(entry, src, cand, rows, files=files, pages=pages, part=parts)

    # ── celý zdroj (pokročilé) ──
    def save_source(self, slug, content, base):
        why = self.source_lock(slug)
        if why:
            raise Fail(why, code="locked", status=403)
        src = self.read_page(slug)
        if B.page_hash(src) != base:
            raise Fail("Soubor se mezitím změnil. Načti ho znovu.", code="stale", status=409)
        content = content.replace("\r\n", "\n")
        if "\r\n" in src:
            content = content.replace("\n", "\r\n")
        if content == src:
            return {"ok": True, "unchanged": True}
        cand = self.inject(content, slug)
        fails = self.check(slug, cand)
        followers = self.faq_followers({slug: cand}, fails)
        if fails:
            raise Fail("Úprava by neprošla kontrolou buildu, nic jsem nezapsal.", code="verify", details=fails)
        with write_lock(self.root):
            if self.read_page(slug) != src or self._stale_followers(followers):
                raise Fail("Soubor se mezitím změnil. Načti ho znovu.", code="stale", status=409)
            files = {self.page_rel(slug): cand.encode("utf-8")}
            files.update({self.page_rel(p): c.encode("utf-8") for p, (_, c) in followers.items()})
            entry = self.journal.write(files, slug=slug, pages=[slug] + sorted(followers),
                                       label="/%s · celý zdroj" % slug, before="", after="", snippet="")
        return {"ok": True, "entry": entry["id"], "page": B.page_hash(cand), "pages": sorted(followers)}

    # ── SEO panel: hlavička, texty JSON-LD, FAQ v JSON-LD (seo.py), údaje o vás (Rules.entity) ──
    def seo_prov(self, slug, src=None):
        """Literály polí SEO generované stránky, cache podle otisku stránky a zdrojů."""
        src = self.read_page(slug) if src is None else src
        key = PV.cache_key(src, PV.sources(self.site, slug))
        sp = self._seo.get(slug)
        if sp is None or sp.key != key:
            sp = S.probe(self.site, slug, src)
            self._seo[slug] = sp
        return sp

    def seo_lock(self, slug, sp=None):
        k = self.kind(slug)
        if k == "owner" and slug not in self.unlocked:
            return "Owner stránka (ADR 0003): nejdřív ji odemkni (jen role admin, na 30 minut)."
        if sp is not None and sp.error:
            return "Zkušební build generované stránky selhal, SEO je jen ke čtení: %s" % sp.error[-300:]
        return None

    def _seo_page(self, slug, src):
        """(druh, proveniance generované stránky, zámek, data panelu stránky)."""
        kind = self.kind(slug)
        sp = self.seo_prov(slug, src) if kind == "generated" else None
        lock = self.seo_lock(slug, sp)
        linked = frozenset(H.plain(B.block_text(src, b)) for b in B.blocks(src) if not b.lock)
        return kind, sp, lock, S.panel(src, kind, sp, linked, lock)

    def seo_panel(self, slug):
        src = self.read_page(slug)
        kind, sp, lock, out = self._seo_page(slug, src)
        out.update(slug=slug, kind=kind, lock=lock, role=actor()["role"],
                   source=self.script_rel(slug) if sp else self.page_rel(slug),
                   person=self.entity_panel(slug, src))
        return out

    def save_seo(self, slug, changes, confirm=None, owners=False):
        """changes = [{id, old, new}]: old je hodnota, kterou panel ukázal (jinak 409), u keywords
        a seznamů JSON-LD seznam. Pole údajů o vás (p:…, knows) jdou přes save_entity."""
        if changes and all(_entity_id(c["id"]) for c in changes):
            return self.save_entity(slug, changes, confirm, owners)
        if any(_entity_id(c["id"]) for c in changes):
            raise Fail("Údaje o vás a texty stránky se ukládají zvlášť.", code="mixed", status=422)
        src = self.read_page(slug)
        kind, sp, why, pan = self._seo_page(slug, src)
        if why:
            raise Fail(why, code="locked", status=403)
        rows = {f["id"]: f for f in pan["fields"]}
        rows.update({r["id"]: r for o in pan["ld"] for r in o["rows"]})
        rows.update({f[x]["id"]: f[x] for f in pan["faq"] for x in ("q", "a")})
        cur, lv = S.values(src), S.ld_values(src)
        new = {}
        for c in changes:
            fid = c["id"]
            r = rows.get(fid)
            if r is None:
                raise Fail("Pole %s na stránce není." % fid)
            if not r["edit"]:
                raise Fail("%s: %s" % (S.label(fid, src), r["why"] or "jen ke čtení"), code="locked", status=403)
            old = lv[fid] if fid in lv else cur.get(fid)
            if old != c["old"]:
                raise Fail("Pole „%s“ se mezitím změnilo. Panel SEO načtu znovu." % S.label(fid, src),
                           code="stale", status=409)
            v = S.clean_list(c["new"]) if isinstance(old, list) else S.clean(c["new"])
            if not v:
                raise Fail("Pole „%s“ nesmí být prázdné." % S.label(fid, src), code="empty", status=422)
            if v != old:
                new[fid] = v
        if not new:
            return {"ok": True, "unchanged": True}
        token = hashlib.sha1(json.dumps([slug, sorted(new.items())], ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        try:
            expected = S.apply(src, new, derived=sp is not None)
        except S.SeoError as e:
            raise Fail(str(e), code="verify", status=422)
        rel = self.page_rel(slug)
        if sp is None:
            cand = self.inject(expected, slug)
            fails = self.check(slug, cand)
            followers = self.faq_followers({slug: cand}, fails)
            if fails:
                raise Fail("Úprava by neprošla kontrolou buildu, nic jsem nezapsal.", code="verify", details=fails)
            writes, text, pages, body = {rel: cand}, None, sorted(followers), False
            writes.update({self.page_rel(p): c for p, (_, c) in followers.items()})
            notes = _seo_effect(cand, new)
        else:
            text = PV.read(self.root, sp.script)
            writes, pages, notes, body, followers = self._seo_generated(slug, src, sp, text, new, expected)
        if notes and confirm != token:
            return {"confirm": token, "message": "Úprava změní víc než pole SEO.", "details": notes}

        with write_lock(self.root):
            if self.read_page(slug) != src or self._stale_followers(followers) or (
                    text is not None and PV.read(self.root, sp.script) != text):
                raise Fail("Stránka nebo skript se mezitím změnily. Panel SEO načtu znovu.", code="stale", status=409)
            entry = self.journal.write({k: v.encode("utf-8") for k, v in writes.items()}, slug=slug,
                                       pages=[slug] + pages, block="seo", label="/%s · SEO" % slug,
                                       before=_seo_sum(src, {k: (lv[k] if k in lv else cur[k]) for k in new}),
                                       after=_seo_sum(src, new),
                                       snippet=_snippet(_seo_text(next(iter(new.values())))))
        cand = writes[rel]
        return {"ok": True, "entry": entry["id"], "page": B.page_hash(cand), "body": body, "pages": pages,
                "files": [sp.script if sp else rel], "title": S.values(cand).get("title")}

    def _seo_generated(self, slug, src, sp, text, new, expected):
        """Pole do literálů skriptu stránky (text = skript; seznam JSON-LD jako celý seznam ve skriptu),
        zkušební build skriptu, build v paměti a kontrola. Vrací (zápisy, další stránky, poznámky
        k potvrzení, mění se text na stránce, stránky s převzatým obsahem)."""
        fk = sp.script
        lits = P.literals(text)
        vals, edits = {}, []
        for fid, v in new.items():
            if fid in sp.lists:
                a, z, sep = sp.lists[fid]
                edits.append((a, z, "[" + sep.join(json.dumps(x, ensure_ascii=False) for x in v) + "]"))
                continue
            li = sp.targets[fid]
            enc = S.enc_html(S.kw_join(v)) if fid == S.KW else S.enc_html(v) if fid in S.LABEL else v
            if vals.setdefault(li, enc) != enc:
                raise Fail("Dvě pole mají ve zdroji společný text, různě je změnit nejde.", code="locked", status=422)
        for li, enc in vals.items():
            try:
                raw = P.encode(lits[li], enc)
            except ValueError as e:
                raise Fail("Do zdroje se text nepodařilo zapsat (%s), nic jsem nezapsal." % e, code="verify", status=422)
            if len(lits[li].q) == 3 and "\r\n" in text:
                raw = raw.replace("\r\n", "\n").replace("\n", "\r\n")
            edits.append((lits[li].a, lits[li].z, raw))
        edits.sort()
        if any(x[1] > y[0] for x, y in zip(edits, edits[1:])):
            raise Fail("Dvě pole mají ve zdroji společný text, různě je změnit nejde.", code="locked", status=422)
        s = text
        for a, z, raw in reversed(edits):
            s = s[:a] + raw + s[z:]
        ok, err, outputs = self.site.generate({fk: s}, [fk])
        if not ok:
            raise Fail("Build se změněným textem selhal, nic jsem nezapsal.", code="verify",
                       details=[x for x in err.strip().splitlines() if x.strip()][-6:])
        writes, fails, built = {fk: s}, [], {}
        for page in sorted(outputs):
            cand = self.inject(outputs[page], page)
            if cand == self.read_page(page):
                continue
            fails += self.check(page, cand)
            writes[self.page_rel(page)] = built[page] = cand
        followers = self.faq_followers(built, fails)
        writes.update({self.page_rel(p): c for p, (_, c) in followers.items()})
        if fails:
            raise Fail("Úprava by neprošla kontrolou buildu, nic jsem nezapsal.", code="verify", details=fails)
        cand = writes.get(self.page_rel(slug))
        if cand is None:
            raise Fail("Úprava se po buildu na stránce neprojevila, nic jsem nezapsal.", code="verify", status=422)

        notes, body = _seo_effect(cand, new), False
        if cand != expected:
            if src[src.find("<body"):] != cand[cand.find("<body"):]:
                body = True
                other = [H.norm(y.inner(cand)) for x, y in zip(B.blocks(src), B.blocks(cand)) if x.inner(src) != y.inner(cand)]
                notes.append("Stejný text je i na stránce a změní se tam taky (%d): „%s“"
                             % (len(other), other[0][:120] if other else "…"))
            if expected[:expected.find("<body")] != cand[:cand.find("<body")]:
                notes.append("Mění se i další místo v hlavičce nebo JSON-LD stránky.")
        pages = sorted(p for p in set(outputs) | set(followers) if self.page_rel(p) in writes and p != slug)
        if pages:
            notes.append("Text je sdílený: propíše se i na %s — %s"
                         % (_plural(len(pages), "další stránku", "další stránky", "dalších stránek"),
                            ", ".join("/" + p for p in pages[:8]) + (" …" if len(pages) > 8 else "")))
        return writes, pages, notes, body, followers

    # ── údaje o vás: sdílený objekt JSON-LD v datech webu (adapter.Rules.entity) ──
    def entity_pages(self):
        """Stránky, které nesou blok entity (podle stránek v repu)."""
        out = []
        for slug in sorted(self.registry()):
            try:
                if self.site.entity_block(self.read_page(slug)) is not None:
                    out.append(slug)
            except FileNotFoundError:
                continue
        return out

    def entity_panel(self, slug, src=None):
        """Oddíl O vás panelu SEO, None = web entitu nemá nebo ji stránka nenese."""
        ent = AD.RULES.entity
        if not ent:
            return None
        src = self.read_page(slug) if src is None else src
        obj = self.site.entity_block(src)
        if obj is None:
            return None
        shared = None if actor()["role"] == "admin" else LOCK_ENTITY_ROLE
        return S.entity_panel(self.site.entity_data(), slug, self.entity_pages(), obj, self.seo_lock(slug), shared)

    def save_entity(self, slug, changes, confirm=None, owners=False):
        """Údaje o vás (p:<klíč>, knows) do souboru dat entity a přestavění stránek, kterým se mění
        (adapter.Site.entity_reach), jako sdílený blok (_part_plan). Společné údaje a výchozí témata
        jen role admin a s potvrzením (první volání vrací souhrn s tokenem), owner stránky zaškrtnutím
        (owners); přepis jen téhle stránky bez potvrzení, na owner stránce po odemčení."""
        ent = AD.RULES.entity
        if not ent:
            raise Fail("Web nemá údaje o vás.", status=404)
        src = self.read_page(slug)
        if self.site.entity_block(src) is None:
            raise Fail("Stránka údaje o vás nenese.", status=404)
        rel = ent["file"]
        text = PV.read(self.root, rel)
        data = self.site.entity_data(text)
        try:
            new, shared, local = S.entity_apply(data, slug, changes)
        except S.SeoError as e:
            msg = str(e)
            if msg.startswith("stale:"):
                raise Fail("Pole „%s“ se mezitím změnilo. Panel SEO načtu znovu." % msg[6:], code="stale", status=409)
            if msg.startswith("empty:"):
                raise Fail("Pole „%s“ nesmí být prázdné." % msg[6:], code="empty", status=422)
            raise Fail(msg, status=422)
        if not shared and not local:
            return {"ok": True, "unchanged": True}
        if shared and actor()["role"] != "admin":
            raise Fail(LOCK_ENTITY_ROLE, code="role", status=403)
        why = self.seo_lock(slug) if local else None
        if why:
            raise Fail(why, code="locked", status=403)
        new_text = self.site.entity_text(new)
        reach = self.site.entity_reach(data, new)
        token = hashlib.sha1(json.dumps(["entity", slug, new_text], ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        key = (str(self.root), token)
        plan = self._cached_plan(key, {rel: new_text}, reach=reach)
        reg = self.registry()
        pages = plan["pages"]
        own = [p for p in pages if reg[p]["kind"] == "owner" and (p != slug or bool(shared))]
        summary = [{"id": fid, "label": S.label(fid), "old": _entity_value(data, slug, fid),
                    "new": _entity_value(new, slug, fid), "shared": fid in shared}
                   for fid in shared + [x for x in local if x not in shared]]
        if shared and confirm != token:
            return {"confirm": token, "file": rel, "message": "Uložit údaje o vás?", "changes": summary,
                    "pages": [{"slug": p, "kind": reg[p]["kind"]} for p in pages], "owners": own}
        if shared and own and not owners:
            raise Fail("Změna mění i owner stránky (%s), potvrď je v dialogu." % ", ".join("/" + p for p in own),
                       code="owners", status=403)
        before = " · ".join("%s: %s" % (x["label"], _seo_text(x["old"])) for x in summary)[:300]
        after = " · ".join("%s: %s" % (x["label"], _seo_text(x["new"])) for x in summary)[:300]
        with write_lock(self.root):
            if self.read_page(slug) != src or not self._fresh(plan["state"]):
                _PART_PLANS.pop(key, None)
                raise Fail("Stránky nebo %s se mezitím změnily. Panel SEO načtu znovu." % rel, code="stale", status=409)
            writes = {k: v.encode("utf-8") for k, v in plan["writes"].items()}
            snippet = _snippet(_seo_text(summary[-1]["new"]))
            if shared:
                entry = self.journal.write(writes, slug=None, kind="replace", pages=pages, block=None, part=rel,
                                           label="Údaje o vás · %s" % rel, before=before, after=after, snippet=snippet)
            else:
                entry = self.journal.write(writes, slug=slug, pages=[slug] + [p for p in pages if p != slug],
                                           block="seo", label="/%s · SEO" % slug, before=before, after=after,
                                           snippet=snippet)
        _PART_PLANS.pop(key, None)
        cand = plan["writes"].get(self.page_rel(slug), src)
        return {"ok": True, "entry": entry["id"], "page": B.page_hash(cand), "pages": pages, "files": [rel],
                "shared": bool(shared), "part": rel if shared else None}

    def undo(self, eid):
        with write_lock(self.root):
            try:
                return self.journal.undo(eid)
            except Conflict as e:
                raise Fail(str(e), code="conflict", status=409)


block_text = B.block_text


def _entity_id(fid):
    """Je pole z oddílu O vás (údaje o vás v datech webu, ne text stránky)?"""
    return fid == "knows" or fid.startswith("p:")


def _entity_value(data, slug, fid):
    exp = S.entity_expected(data, slug)
    if fid == "knows":
        return exp.get(AD.RULES.entity["topics"])
    return S._dig(exp, fid[2:])


def _seo_text(v):
    return ", ".join(v) if isinstance(v, list) else (v or "")


def _seo_sum(src, vals):
    """Souhrn úpravy panelu SEO pro journal a Změny: „Title: text · Klíčová slova: a, b“."""
    return " · ".join("%s: %s" % (S.label(k, src), _seo_text(v)) for k, v in vals.items())[:300]


def _seo_effect(cand, new):
    """Poznámky, když build pole SEO ještě upravil (hodnota na stránce po buildu je jiná)."""
    got = dict(S.values(cand), **S.ld_values(cand))
    return ["Build %s ještě upravil: „%s“" % (S.label(k, cand), _seo_text(got.get(k)))
            for k, v in new.items() if got.get(k) != v]


def _page_part(inner):
    """Inner sdíleného bloku ze stránky, jak ho nese šablona: LF a bez aria-current."""
    return _ARIA_CURRENT.sub("", inner.replace(CRLF, LF))


def _sha_file(root, rel):
    try:
        return hashlib.sha1((Path(root) / rel).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _plural(n, one, few, many):
    return "%d %s" % (n, one if n == 1 else few if 2 <= n <= 4 else many)


def _snippet(text):
    """Kus nového textu pro kontrolu na produkci: nejdelší slova, ne celé věty."""
    words = text.split()
    return " ".join(words[:6]) if len(words) >= 3 else text


# ── texty mimo bloky (save_attrs) ──
_WJ = chr(0x2060)


def _attr_span(src, node, a):
    """(začátek, konec) hodnoty atributu a v začátečním tagu prvku, u C.ATTR_TEXT obsah prvku."""
    if a == C.ATTR_TEXT:
        return node.se, node.ie
    m = re.search(r'\s%s\s*=\s*"([^"]*)"' % re.escape(a), src[node.s:node.se], re.I)
    if m is None:
        raise Fail("Atribut %s je ve zdroji zapsaný bez dvojitých uvozovek, změní ho Claude Code." % a,
                   code="locked", status=422)
    return node.s + m.start(1), node.s + m.end(1)


def _squash(s):
    """Text, jak ho ukázal editor: bez word joinerů, bílé znaky (i pevná mezera) jako jedna mezera."""
    return re.sub(r"\s+", " ", str(s or "").replace(_WJ, "")).strip()


def _plain(s):
    """Nový text mimo blok: _squash a rovné uvozovky jako „…“ (P02)."""
    out, prev = [], ""
    for c in _squash(s):
        if c == '"':
            c = "„" if (not prev or prev in " ([/") else "“"
        out.append(c)
        prev = c
    return "".join(out)


def _attr_enc(a, v):
    """Hodnota do zdroje: & jako entita, v atributu i uvozovka."""
    v = v.replace("&", "&amp;")
    return v if a == C.ATTR_TEXT else v.replace('"', "&quot;")


def _splice_attrs(s, edits):
    for (a, z), v in sorted(edits, key=lambda e: -e[0][0]):
        s = s[:a] + v + s[z:]
    return s


def _attr_sum(rows, key):
    """Souhrn úpravy pro journal a Změny: „alt: text · title: text“."""
    return " · ".join("%s: %s" % ("text v SVG" if r["a"] == C.ATTR_TEXT else r["a"], r[key]) for r in rows)[:300]


def _attr_effect(cand, nodes, rows):
    """Texty rows na stránce po buildu (r["got"]); poznámky, když je build změnil."""
    after = C.attr_nodes(cand, "handbuilt")
    if len(after) != len(nodes) or any(x.names != y.names for x, y in zip(nodes, after)):
        raise Fail("Úprava změnila strukturu stránky, nic jsem nezapsal.", code="structure")
    notes = []
    for r in rows:
        a, z = _attr_span(cand, after[r["i"]].node, r["a"])
        r["got"] = C.norm(cand[a:z])
        if r["got"] != r["new"]:
            notes.append("Build text ještě upravil: „%s“" % r["got"])
    return notes


def _attr_result(entry, src, cand, rows, **kw):
    """Odpověď uložení: hodnoty po buildu pro editor a nové otisky bloků, ve kterých se text změnil
    (obrázek v bloku), ať další úprava bloku nehlásí změněnou stránku."""
    blocks = [{"n": b.n, "h": b.hash(cand)} for a, b in zip(B.blocks(src), B.blocks(cand)) if a.inner(src) != b.inner(cand)]
    return dict({"ok": True, "entry": entry["id"], "page": B.page_hash(cand), "blocks": blocks,
                 "values": [{"i": r["i"], "a": r["a"], "v": r.get("got", r["new"])} for r in rows]}, **kw)


def _cut(text, n=60):
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


# ── svázané kopie v JSON-LD ──
def _walk(v, parent=None, key=None, holder=None):
    """(hodnota, rodičovský objekt, klíč, seznam, ve kterém rodič leží) pro všechny řetězce."""
    if isinstance(v, dict):
        for k, x in v.items():
            yield from _walk(x, v, k, holder)
    elif isinstance(v, list):
        for x in v:
            yield from _walk(x, parent, key, v) if not isinstance(x, (dict, list)) else _walk(x, None, None, v)
    elif isinstance(v, str):
        yield v, parent, key, holder


def _allowed(parent, key, holder):
    """Allowlist: otázka FAQ a poslední položka drobečkové navigace."""
    if key != "name" or not isinstance(parent, dict):
        return False
    t = parent.get("@type")
    if t == "Question":
        return True
    return t == "ListItem" and isinstance(holder, list) and holder and holder[-1] is parent


def link_jsonld(src, old_plain, new_plain):
    """Přepíše v JSON-LD hodnoty z allowlistu, které se rovnaly starému textu bloku.
    Mění jen dotčený řetězec, formátování bloku zůstane. Vrací (zdroj, počet)."""
    if not old_plain or old_plain == new_plain:
        return src, 0
    enc_old = H.ld_enc(old_plain)[1:-1]
    enc_new = H.ld_enc(new_plain)[1:-1]
    pat = re.compile(r'("name"\s*:\s*")' + re.escape(enc_old) + '"')
    count = 0

    def fix(m):
        nonlocal count
        raw = m.group(2)
        try:
            tree = json.loads(raw)
        except ValueError:
            return m.group(0)
        for mm in reversed(list(pat.finditer(raw))):
            trial = raw[:mm.start()] + mm.group(1) + enc_new + '"' + raw[mm.end():]
            try:
                t2 = json.loads(trial)
            except ValueError:
                continue
            a = list(_walk(tree))
            b = list(_walk(t2))
            diffs = [(p, k, h) for (x, p, k, h), (y, _, _, _) in zip(a, b) if x != y]
            if len(a) == len(b) and len(diffs) == 1 and _allowed(*diffs[0]):
                raw, tree = trial, t2
                count += 1
        return m.group(1) + raw + m.group(3)

    return LD_RE.sub(fix, src), count
