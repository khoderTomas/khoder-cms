---
name: nastaveni-webu
description: Vlastní web z ukázkového webu Khoder CMS a jeho nastavení – požadavky (Python 3.12+), adresa webu a cms.toml, údaje firmy v content/site.json, adaptér build/cms_adapter.py (TITLE_SUFFIX, panel SEO, menu) a deploy z gitu do složky, přes rsync nebo sftp. Použij při zakládání webu z ukázky, změně domény, názvu webu nebo struktury hlavičky a při nastavení či ladění deploye.
---

# Nastavení webu s Khoder CMS

Postup od ukázky k vlastnímu webu. Po každém kroku musí `python build/build.py` skončit `BUILD OK`.

## Požadavky

- **Python 3.12 nebo novější**, jen standardní knihovna, nic se neinstaluje. Ověření: `python --version`
  (na Linuxu a macOS `python3 --version`). Na 3.11 motor nepřečte části f-stringů (PEP 701), proto
  Debian 12 nestačí, Debian 13 nebo Ubuntu 24.04 ano. Samotný `cms/deploy.py` jede i na 3.11.
- git a repo webu na GitHubu, GitLabu nebo jiném gitovém serveru: deploy nasazuje commit z `origin`.
- Pro deploy na server klient `rsync` nebo `sftp` se ssh klíčem. Windows má `sftp` v OpenSSH, `rsync`
  ne (použijte sftp nebo WSL).

## 1. Repo z ukázky

1. Obsah složky ukázky zkopírujte do kořene repa webu i se skrytými soubory (`.claude/`,
   `.gitattributes`, `.gitignore`). Motor patří do složky `cms/` jako kopie vydané verze Khoder CMS.
2. `python build/build.py` a `python cms/coverage.py` musí projít ještě před první změnou.
3. Commit: od teď se každá změna dá vrátit.

## 2. Adresa webu

1. V `cms.toml` nastavte `[deploy] site` na adresu webu bez lomítka na konci, třeba
   `https://www.mojedilna.cz`.
2. Starou adresu `https://www.example.com` nahraďte novou ve `www/*.html` (canonical, `og:url`,
   `@id` a `url` v JSON-LD), v `content/site.json` a ve `www/robots.txt`.
3. `python build/build.py` a `python build/sitemap.py`. Kontrola ohlásí každý canonical, který na
   adresu nesedí.

## 3. Údaje firmy

`content/site.json`, objekt `business` (schema.org `LocalBusiness`):

- `name`, `description`, `url`, `address` (`streetAddress`, `addressLocality`, `postalCode`,
  `addressCountry`), `openingHours` ve formátu schema.org (`Mo-Fr 07:00-16:00`) a `knowsAbout` (témata).
- `telephone` bez mezer s předvolbou (`+420123456789`), na stránce ho build ukáže s mezerami.
- `email`.
- `pages` přepisuje údaje pro jednotlivé stránky: `{"kontakt": {"description": "…"}}`.

Build údaje doplní do JSON-LD a do prvků s `data-site`. Název, popis, ulici a město pak správce mění
v panelu SEO (oddíl O vás), kontakty, PSČ a otevírací dobu mění Claude Code tady.

Web pro jinou entitu než firmu (osobní web, `Person`): `ENTITY_KEY` a `ENTITY_TYPE` v `build/web.py`,
stejný klíč v `content/site.json` a pole v `Rules.entity` v adaptéru.

## 4. Adaptér: build/cms_adapter.py

Adaptér říká editoru, co o webu neví sám. Na novém webu se mění:

- **`TITLE_SUFFIX`**: regulární výraz konce title s názvem webu, třeba
  `r"\s+\|\s+Moje dílna\s*$"`. Editor podle něj odvozuje `og:title` (title bez názvu webu).
- **`LD_ALLOW` a `LD_TYPES`**: texty JSON-LD, které smí správce měnit v panelu SEO
  (`(@type, klíč): popisek`), a názvy skupin podle `@type`. Co v seznamu není, je v panelu zamčené.
- **`Rules.menu`**: selektory menu na telefonu (`toggle` tlačítko, `nav` navigace, `open` třída
  otevřeného menu, `narrow` šířka okna v px, do které je navigace za tlačítkem, stejná jako v
  `@media (max-width: …)` ve `www/assets/style.css`) a `frame`, prvky, podle kterých editor kreslí rám
  hlavičky a patičky. Při změně tříd v `content/partials/` nebo CSS změňte i tady.
- **`Rules.entity`**: které údaje firmy ukazuje panel SEO, `text` k úpravě, `contacts` jen ke čtení.
- **`Rules.lock_classes`, `Rules.lock_ids`**: prvky, které správce měnit nemá, s důvodem
  (`{"cenik": "Ceník plní build z content/cenik.json."}`). Výchozí ukázka žádné nemá.

Třída `Site` (build v paměti, kontrola, šablony) se na běžném webu nemění. Pouští `build/web.py`,
takže editor i `build/build.py` stavějí stejně.

## 5. Deploy

`cms/deploy.py` nasadí složku `www/` z commitu na `origin/main` do cíle z `[deploy.target]`
v `cms.toml`:

- nasazuje obsah commitu, ne pracovní složku: necommitnutá ani nepushnutá změna se nenasadí;
- zapisuje jen změněné soubory a nic nemaže (starý soubor na serveru smažte ručně);
- odmítne HTML mimo `content/pages.json`, PHP mimo `[deploy] php` a skryté soubory mimo
  `[deploy] dotfiles` (třeba `dotfiles = [".htaccess"]`);
- odkazům na CSS a JS přidá `?v=<otisk obsahu>`, prohlížeč tak po změně nevezme starou verzi.

Cíle:

```toml
[deploy.target]
kind = "local"                          # repo je na serveru webu
path = "/var/www/mojedilna"

[deploy.target]
kind = "rsync"
dest = "web@server.cz:/var/www/mojedilna"
ssh = "ssh -p 22"                       # volitelné: port, klíč

[deploy.target]
kind = "sftp"                           # hosting bez shellu
host = "web@server.cz"
path = "/www/mojedilna"
args = ["-P", "22"]                     # volitelné argumenty sftp
```

Postup:

```
git push origin main
python cms/deploy.py deploy --dry-run                 # co by se nasadilo, nic nezapíše
python cms/deploy.py deploy                           # nasazení, končí řádkem OK: Deployed version …
python cms/deploy.py status                           # co je nasazené a kolik commitů čeká
python cms/deploy.py deploy --target-path ./zkouska   # zkušební kopie do existující složky
```

Cílová složka musí existovat. Stav posledního deploye je v `~/.local/state/cms/deploy-<host>.json`
(jinde přes `--state`), podle něj sftp pozná změněné soubory a deploy kontroluje, že commit navazuje
na nasazený. Starší commit nasadí jen `--rollback`.

Volitelně `[deploy.indexnow]` pošle vyhledávačům změněné adresy ze sitemapy: `key_file` je soubor
s klíčem IndexNow ve `www/` (výchozí `indexnow-key.txt`), `sitemap` cesta sitemapy ve `www/`.

Návod k instalaci editoru jako služby na serveru (přihlášení, publikace do gitu) přibude s první verzí
balíčku.
