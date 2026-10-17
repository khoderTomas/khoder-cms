# Změny

Verze se řídí [sémantickým verzováním](https://semver.org/lang/cs/). Do verze 1.0 může nová vedlejší
verze (0.x.0) změnit rozhraní adaptéru nebo `cms.toml`. Co pak v adaptéru upravit, píše `UPGRADING.md`.
Opravná verze (0.x.y) rozhraní nemění.

## 0.1.0 (2026-10-17)

První verze, rozhraní adaptéru 1.

- Editor textů statického webu v gitu: úprava klikem ve stránce, v šablonách hlavičky a patičky
  a v hodnotách datových souborů JSON. Dále panel SEO (title, description, JSON-LD, údaje o firmě),
  panel Další texty, popisky obrázků, Najít a nahradit, Vrátit a Zahodit.
- Owner stránky jsou zamčené, dokud je admin neodemkne. Sdílená změna menu, patičky nebo hodnoty
  z dat, která owner stránku mění, se potvrzuje zaškrtnutím.
- Publikovat: úpravy se sloučí s `main`, build a commit proběhnou v dočasném worktree, pak push
  a deploy.
- Editor běží lokálně bez přihlášení, nebo jako služba za reverse proxy s přihlášením ze souboru
  uživatelů (`editor.py`, `login.py`, návod `INSTALACE.md`).
- Deploy z gitu (`deploy.py`) do složky, přes rsync nebo sftp: zapisuje jen změněné soubory, odkazům
  na CSS a JS dává `?v=` podle obsahu a před zápisem kontroluje stránky. Umí poslat IndexNow.
- Sladění checkoutu editoru s větví (`gitsync.py`) a report pokrytí textů (`coverage.py`).
- Ukázkový web v `example/` s návodem pro Claude Code (`CLAUDE.md`, skilly `stranky-webu`
  a `nastaveni-webu`).
- Kontrola rozhraní adaptéru při startu (`contract` ve třídě `Site`, `UPGRADING.md`).
