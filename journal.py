# -*- coding: utf-8 -*-
"""Journal úprav editoru: co editor zapsal do repa, s bajty „před“ pro Vrátit.

Každá úprava je záznam <složka journalu>/<id>.json (cms.toml [editor] journal, mimo git):
  state   pending → done → (undone | published | discarded), nebo aborted po pádu
  files   [{path, before, after}]  (sha1; before=None = soubor neexistoval)
Bajty „před“ leží v <id>/<i>.bin, takže Vrátit přežije restart serveru.

Zápis: nejdřív záznam pending s bajty „před“, pak atomické zápisy souborů,
nakonec done. Když server spadne mezi tím, recover() při startu soubory vrátí.

Zahodit (publish.discard) je záznam kind=discard: pending → discard. Nese bajty
„před“ jako každý zápis a v covers úpravy, které zahazuje; ty dostanou stav
discarded, takže se nepublikují a z panelu Změny zmizí.

Publikace: published s commit (úpravy jsou na větvi), po deployi i version.
Published bez version = commit čeká na Nasadit znovu.

Záznam nese user (kdo úpravu udělal, ze who()); audit všech akcí je audit.jsonl ve stejné složce.
"""
import hashlib
import json
import os
import time
from pathlib import Path


def sha(b):
    return None if b is None else hashlib.sha1(b).hexdigest()


def atomic_write(path, data):
    """Zápis přes dočasný soubor a os.replace; Windows občas soubor chvíli drží."""
    path = Path(path)
    tmp = path.with_name(path.name + ".ke-tmp")
    tmp.write_bytes(data)
    for i in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.1 * (i + 1))
    os.replace(tmp, path)


class Conflict(Exception):
    pass


class Journal:
    def __init__(self, root, directory=None, who=None):
        """who() = jméno uživatele, který právě zapisuje (None = lokální editor bez přihlášení)."""
        self.root = Path(root)
        self.dir = Path(directory) if directory else self.root / ".cms" / "journal"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.who = who or (lambda: None)

    # ── čtení ──
    def _read_file(self, rel):
        p = self.root / rel
        return p.read_bytes() if p.exists() else None

    def entries(self, states=None):
        out = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                e = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if states is None or e["state"] in states:
                out.append(e)
        # id má přesnost na sekundy a náhodnou příponu: pořadí zápisu drží až čas t
        out.sort(key=lambda e: (e.get("t", 0), e["id"]))
        return out

    def get(self, eid):
        f = self.dir / (eid + ".json")
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def _save(self, e):
        atomic_write(self.dir / (e["id"] + ".json"), json.dumps(e, ensure_ascii=False, indent=1).encode("utf-8"))

    def unpublished(self):
        return self.entries({"done"})

    def unpublished_files(self):
        files = []
        for e in self.unpublished():
            for f in e["files"]:
                if f["path"] not in files:
                    files.append(f["path"])
        return files

    def before_bytes(self, e, path):
        """Bajty souboru těsně před úpravou e (None = soubor neexistoval)."""
        f = next(f for f in e["files"] if f["path"] == path)
        return None if f["before"] is None else (self.dir / e["id"] / f["blob"]).read_bytes()

    def after_bytes(self, e, path, entries=None):
        """Bajty souboru těsně po úpravě e: „před“ nejbližšího dalšího záznamu se stejným
        souborem, nebo soubor teď. None, když otisk nesedí (soubor mezitím změnil někdo jiný)."""
        f = next(f for f in e["files"] if f["path"] == path)
        key = (e.get("t", 0), e["id"])
        later = [x for x in (self.entries() if entries is None else entries)
                 if (x.get("t", 0), x["id"]) > key and any(g["path"] == path for g in x["files"])]
        data = self.before_bytes(later[0], path) if later else self._read_file(path)
        return data if sha(data) == f["after"] else None

    # ── zápis ──
    def _begin(self, writes, meta):
        """Záznam pending s bajty „před“ všech souborů, které se budou psát."""
        eid = time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(3).hex()
        blobs = self.dir / eid
        blobs.mkdir()
        files = []
        for i, (rel, data) in enumerate(writes.items()):
            before = self._read_file(rel)
            if before is not None:
                (blobs / ("%d.bin" % i)).write_bytes(before)
            files.append({"path": rel, "before": sha(before), "after": sha(data), "blob": "%d.bin" % i})
        e = dict(meta, id=eid, t=time.time(), state="pending", files=files)
        user = self.who()
        if user:
            e.setdefault("user", user)
        self._save(e)
        return e

    def _write_files(self, writes):
        for rel, data in writes.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self.root / rel, data)

    def write(self, writes, **meta):
        """writes = {relativní cesta: nové bajty}. Vrací záznam (state done)."""
        e = self._begin(writes, meta)
        self._write_files(writes)
        e["state"] = "done"
        self._save(e)
        return e

    def discard(self, writes, covers, **meta):
        """Zahození: zapíše writes a úpravy covers (id) označí discarded. Vrací záznam (state discard)."""
        e = self._begin(writes, dict(meta, kind="discard", covers=list(covers)))
        self._write_files(writes)
        self._close_discard(e)
        return e

    def _close_discard(self, e):
        for eid in e["covers"]:
            c = self.get(eid)
            if c and c["state"] == "done":
                c["state"] = "discarded"
                c["discard"] = e["id"]
                self._save(c)
        e["state"] = "discard"
        self._save(e)

    def undo(self, eid):
        e = self.get(eid)
        if not e or e["state"] != "done":
            raise Conflict("Tuhle úpravu už vrátit nejde (je vrácená, zahozená nebo publikovaná).")
        changed = [f["path"] for f in e["files"] if sha(self._read_file(f["path"])) != f["after"]]
        if changed:
            later = [x for x in self.entries({"discard"}) if x.get("t", 0) > e.get("t", 0)
                     and any(f["path"] in changed for f in x["files"])]
            if later:
                raise Conflict("Soubor %s se od úpravy změnil zahozením změn stránky /%s. Tuhle úpravu už vrátit "
                               "nejde, zahodit ji jde na její stránce." % (", ".join(changed), later[-1].get("slug", "")))
            raise Conflict("Soubor se od úpravy změnil (%s). Nejdřív vrať pozdější úpravy."
                           % ", ".join(changed))
        self._restore(e)
        e["state"] = "undone"
        self._save(e)
        return e

    def _restore(self, e):
        for f in e["files"]:
            p = self.root / f["path"]
            if f["before"] is None:
                if p.exists():
                    p.unlink()
            else:
                atomic_write(p, (self.dir / e["id"] / f["blob"]).read_bytes())

    def recover(self):
        """Po pádu: záznamy pending dokončí nebo vrátí. Vrací zprávy pro banner."""
        msgs = []
        for e in self.entries({"pending"}):
            now = [sha(self._read_file(f["path"])) for f in e["files"]]
            if all(n == f["after"] for n, f in zip(now, e["files"])):
                if e.get("kind") == "discard":
                    self._close_discard(e)
                    msgs.append("Zahození %s se dokončilo před pádem editoru." % e.get("label", e["id"]))
                    continue
                e["state"] = "done"
                msgs.append("Úprava %s se dokončila před pádem editoru, zůstává." % e.get("label", e["id"]))
            elif all(n in (f["before"], f["after"]) for n, f in zip(now, e["files"])):
                self._restore(e)
                e["state"] = "aborted"
                msgs.append("Nedokončená úprava %s byla vrácena." % e.get("label", e["id"]))
            else:
                e["state"] = "aborted"
                msgs.append("Nedokončená úprava %s: soubory mezitím změnil někdo jiný, nechávám je být."
                            % e.get("label", e["id"]))
            self._save(e)
        return msgs

    def stale_files(self):
        """Soubory nepublikovaných úprav, které nemají obsah z posledního zápisu editoru
        (změnil je někdo mimo editor). Vrácená úprava nechala v souboru bajty „před“."""
        last = {}
        for e in self.entries():
            if e["state"] in ("pending", "aborted"):
                continue
            for f in e["files"]:
                last[f["path"]] = (e["state"], f)
        out = []
        for path in self.unpublished_files():
            state, f = last[path]
            if sha(self._read_file(path)) != (f["before"] if state == "undone" else f["after"]):
                out.append(path)
        return out

    def mark_published(self, ids, commit, by=None):
        """Úpravy jsou v commitu na větvi (publikace), deploy ještě nemusel proběhnout. by = kdo publikoval."""
        now = time.time()
        for eid in ids:
            e = self.get(eid)
            if e and e["state"] == "done":
                e["state"] = "published"
                e["commit"] = commit
                e["published_t"] = now
                if by:
                    e["published_by"] = by
                self._save(e)

    def last_published(self):
        """Poslední publikace podle journalu: {t, by, commit, version}, nebo None."""
        es = [e for e in self.entries({"published"}) if e.get("published_t")]
        if not es:
            return None
        e = max(es, key=lambda x: x["published_t"])
        return {"t": e["published_t"], "by": e.get("published_by"), "commit": e.get("commit"),
                "version": e.get("version")}

    def undeployed(self):
        """Publikované úpravy, jejichž commit se ještě nenasadil (deploy selhal)."""
        return [e for e in self.entries({"published"}) if e.get("commit") and not e.get("version")]

    def mark_deployed(self, ids, version):
        for eid in ids:
            e = self.get(eid)
            if e and e["state"] == "published" and not e.get("version"):
                e["version"] = version
                self._save(e)
