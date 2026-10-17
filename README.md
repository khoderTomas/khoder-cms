# Khoder CMS

Obecný balíček pro statický web v gitu: editor textů, deploy a sloučení s `main`. Nic v něm
není o konkrétním webu: nastavení webu je v `cms.toml` v kořeni repa a vše ostatní dodá adaptér
webu (§ Adaptér webu). Do repa webu patří jako složka `cms/`, příkazy v tomhle README se pouštějí
z kořene repa webu. Šev hlídá `tests/test_seam.py`: moduly importují jen standardní knihovnu a sebe
navzájem, kód nemá cesty webu a v balíčku není jméno konkrétního webu.

## Stav

Verze 0.1.0 (`CHANGELOG.md`), rozhraní adaptéru 1. Do repa webu se motor převezme a aktualizuje jako
`git subtree` z tagu, postup a co při nové verzi upravit v adaptéru je v `UPGRADING.md`:

```
git subtree add --prefix=cms https://github.com/khoderTomas/khoder-cms.git v0.1.0 --squash
```

Ukázkový web je v `example/`: šablona
pro vlastní web a zároveň testovací web motoru (`example/README.md`). Nese i návod pro Claude Code
zájemce: `example/CLAUDE.md` (pravidla, aby texty zůstaly editovatelné) a skilly
`example/.claude/skills/` (stránky a texty, nastavení adaptéru a deploye). Editor běží lokálně, nebo
jako služba na vlastním serveru s přihlášením (§ Editor jako server, návod `INSTALACE.md`). Do verze
1.0 může vedlejší verze (0.x.0) změnit rozhraní adaptéru i `cms.toml`, opravná verze ne.

Testy v tomhle repu: `python -m unittest discover -s tests -t tests` (Python 3.12+, jen standardní
knihovna; samotný `deploy.py` stačí s 3.11). Část testů běží jen na Linuxu (práva souborů, rsync, procesy jiného uživatele).
`tests/test_example.py` pouští nad kopií ukázkového webu build, kontroly, deploy, pokrytí a úpravy v editoru.

Licence MIT, viz `LICENSE`.

## Adaptér webu

Motor zná tři druhy stránek z manifestu stránek (`[deploy] pages`): ruční (zdrojem je soubor
stránky), owner (ruční, zamčená do odemčení adminem) a generované (zdrojem jsou textové literály
skriptů generátoru, pole `source`). Ostatní dodá modul z `[editor] adapter` (rozhraní
`adapter.py`):

- `Rules`: oblasti, které plní build (zamčené s důvodem), zámky podle id a třídy, pevné prvky
  podle atributu, počítané texty a citace (report pokrytí), typografie, odvození og:title
  generátorem, úseky stránky ze sdílené části (`partial_spans`), panel SEO: texty JSON-LD, které
  admin mění (`ld_allow`, `ld_label`), co plní build (`ld_fixed`), proč je robots jen ke čtení
  a sdílený objekt JSON-LD v datech webu, třeba autor webu (`entity`: soubor, typ, texty, kontakty,
  témata), texty z dat: značkovací vzory v prostém textu hodnot (`value_markup`: zástupný symbol
  jako pevný prvek, párové značky kolem textu odkazu) a rodiny oblastí, jejichž bloky jdou upravit
  v datech (`unlocked`, `region_family`). V procesu jsou jedny, `adapter.load()` je nainstaluje.
- `Site`: manifest stránek, build stránky v paměti (`inject`) a kontrola (`check`), stránky, které
  přebírají obsah jiné stránky (`followers`), sdílené části stránek ve vlastních souborech, třeba
  hlavička a patička (`partial_files`, `partial_pages`, `overlay`: Najít a nahradit v nich hledá
  a přestaví jejich stránky), zkušební build generátoru do tempu (`generate`, `scripts`,
  `shared_files`), datové soubory s texty stránek (`data_files`), zkušební build celého webu
  (`build_site`: generátor a všechny stránky pod overlay, výchozí implementace stačí), data
  sdíleného objektu JSON-LD (`entity_data`, `entity_text`, `entity_block`) a stránky, kterým jeho
  změna změní výstup (`entity_reach`), a úklid prostředí pro build při publikaci. Třída uvádí
  `contract`, číslo rozhraní motoru, pro které je adaptér napsaný. `adapter.load()` s jiným číslem
  než `CONTRACT` skončí chybou `ContractError` s odkazem na `UPGRADING.md`.

Hodnotu v JSON (blok JSON-LD na stránce, data webu) mění `jsonsource.py` jen na cestě, zbytek textu
zůstává v původním formátu; řetězec z dat (`write_string`) i v původním escapování tokenu.

## Proveniance

Odkud je text bloku, který na stránce nestojí ve zdrojovém HTML, zjistí `provenance.py` jednou
sondou celého webu (`probe_site`). Kandidáti: editovatelné bloky generovaných stránek (hledají se
v literálech skriptu stránky a sdílených souborů generátoru) a bloky oblastí odemčené rodiny
(`Block.region`, hledají se v řetězcích `data_files`). Na konec každého výskytu textu úseku se vloží
značka jen ze znaků pro soukromé použití (U+E000, číslice z U+E040–U+E07F, U+E001), výskyt uprostřed
slova se nepočítá. Zkušební build (`build_site`) ukáže, kam značky dopadly; výstup bez značek se musí
rovnat stránce, jinak jde stránka do druhého kola jen se svými značkami a pak je neprověřená (bloky
zamčené). Build webu musí značky z délek textů vynechat (zalamování titulků, šířka štítků).

`apply.Editor` drží poslední sondu (`site_prov`) a výsledek stránky použije, dokud sedí ona a soubory,
ze kterých má texty (`PageProv.key`); po zápisu sondu dohřeje na pozadí (`warm`, i při startu
serveru). Uložení (`_save_mapped`) zapíše změněné úseky do literálů nebo hodnot, přestaví dotčené
stránky (u dat všechny) a zapíše zdroje i stránky jedním záznamem journalu. Hodnota z dat bez tagů
a entit je prostý text: vložený text se do ní dekóduje z HTML, nové formátování editor odmítne
a pevné mezery do ní jdou, jen když je měla celá hodnota (`needs_strip`). Zahodit (`publish.discard`)
vrací literály i hodnoty z dat, které úpravy stránky změnily (`_discard_sources`).

Publikace slučuje soubory z journalu přes `git merge-file`; u `.json`, kde po řádcích vyjde konflikt
(dvě hodnoty na jednom řádku), ještě po cestách (`gitsync.merge`, `jsonsource.merge3`).

Bez adaptéru je web čisté ruční HTML bez buildu a generátoru.

```toml
[editor]
adapter = "web/cms_adapter.py"     # modul adaptéru (cesta v repu)
journal = ".cms/journal"           # journal úprav (na serveru mimo checkout, CMS_JOURNAL)
css = "editor-site.css"            # styly editačního pohledu pro prvky webu (/_admin/site.css)

[publish]
build = ["build.py"]               # Python skript buildu a kontroly ve worktree publikace
sitemap = ["sitemap.py"]           # Python skript sitemapy
sitemap_file = "www/sitemap.xml"
deploy_ssh = "web@127.0.0.1"       # ssh cíl s vynuceným příkazem cms-deploy cms (CMS_DEPLOY_SSH přebíjí)
# deploy = "direct"                # bez ssh: deploy.py z checkoutu editoru rovnou na [deploy.target]
```

## Editor jako server

`editor.py` (jen stdlib) hostí editor `app.py` sám. Bez `CMS_URL` běží lokálně na `127.0.0.1` bez
přihlášení. S `CMS_URL` je to služba za reverse proxy s HTTPS: přihlášení formulářem (`login.py`: soubor
uživatelů s hashem hesla a rolí, brzda hádání hesel, sezení v SQLite), zápis jen s `X-KE` a Origin
editoru, sync checkoutu na `origin/main` a restart kódem 10 po změně mimo složku stránek. Šasi a sada
prvků editoru jsou ve `static/`: `tokens.css`, `components.css`, ikony Lucide `icons.svg` a vrstva
`host.css`. Proměnné prostředí popisuje hlavička `editor.py`, instalaci na server `INSTALACE.md`
a soubory `install/`.

```
python3 cms/editor.py                                    # lokálně, http://127.0.0.1:8765
python3 cms/editor.py user jana --users users.json --role admin --name "Jana Nováková" --email jana@example.cz
```

## Deploy z gitu

`deploy.py` (jen stdlib, Python 3.11+) nasadí složku `source` z commitu na větvi do cíle `local`,
`rsync` nebo `sftp`. Nic nemaže, zapisuje jen změněné soubory, odkazům na CSS a JS dává
`?v=<hash obsahu>` a před zápisem kontroluje stránky, PHP a skryté soubory. Pravidla jsou
v hlavičce `deploy.py`.

```
python3 cms/deploy.py deploy --dry-run                 # cms.toml z commitu, cíl z [deploy.target]
python3 cms/deploy.py deploy --target-path /tmp/www    # kopie do složky místo cíle
python3 cms/deploy.py status
```

Na serveru se spouští přes `bin/cms-deploy`: plný režim pouští `deploy.py` z nasazovaného commitu
a připne ho, režim `cms` (editor, vynucený příkaz klíče) pouští jen připnutou verzi a nasadí jen
HTML a sitemapy.

`cms.toml`:

```toml
[deploy]
source = "dist"                    # co se nasazuje
branch = "main"
remote = "origin"
site = "https://www.example.cz"    # IndexNow a výpis
pages = "content/pages.json"       # manifest stránek: každé HTML v něm, každá položka v exportu
pages_field = "output"             # pole s cestou stránky v položce manifestu
php = ["send.php"]                 # povolené PHP, glob přes celou cestu (* nepřekročí /)
dotfiles = [".htaccess"]           # povolené skryté soubory
file_mode = "644"                  # režim nových souborů

[deploy.target]
kind = "local"                     # local: path · rsync: dest, ssh · sftp: host, path, args
path = "/var/www/example"

[deploy.indexnow]                  # bez sekce se IndexNow neposílá
key_file = "indexnow-key.txt"
sitemap = "sitemap.xml"
```

## Sladění checkoutu s větví

`gitsync.py` (jen stdlib) drží pracovní checkout editoru v souladu s větví bez tvrdého resetu,
protože v něm leží neodeslané úpravy:

- **Zámek publikace** `<git-common-dir>/cms-publish.lock` (flock, na Windows msvcrt). Publikace
  z editoru i sync ho najdou z repa samy, kdo přijde druhý, počká.
- **`merge_file`, `Worktree`, `Git.push`, `Git.clean`**: stavební kameny publikace. Editor sloučí
  své zdroje s větví (`git merge-file`), build a commit udělá v dočasném worktree mimo checkout,
  pushne a při ujeté větvi to jednou zopakuje.
- **`advance`**: posun checkoutu jen dopředu (`merge --ff-only`). Rozpracovaný soubor, který nový
  commit mění, posun zastaví, pokud nejde o právě publikované soubory nebo už nemá obsah z commitu.

```
python3 cms/gitsync.py --repo ~/app sync --restart-prefix build/ --restart-prefix cms/
```

Kódy: 0 beze změny nebo bez restartu, 10 posunuto a změnil se soubor pod `--restart-prefix`,
3 neodeslané úpravy se překrývají se změnami ve větvi (nic se nezměnilo), 1 chyba.

## Pokrytí textů

`coverage.py` projde všechny stránky z registru a skripty webu a pro každý text řekne, jak ho jde
v editoru upravit: klikem, sdíleně s dalšími stránkami, v panelu (SEO, Další texty), nebo vůbec
(počítá ho build, nebo je zamčený s důvodem). Počítá nad `classify(page)`, ze které bere zámky
i editační pohled (bloky a texty mimo bloky, `attr_nodes`), takže report a editor mluví stejně. Výsledek: odkud viditelný text pochází (podíl
unikátních znaků), jak jde upravit, důvody zámků a tabulka ostatních textů (atributy, title
a meta, JSON-LD, JSON pro skripty, texty ve skriptech).

```
python cms/coverage.py                   # report, owner stránky jako po odemčení adminem
python cms/coverage.py --pages           # i po stránkách
python cms/coverage.py --json out.json   # i strojově
```

Report i editor stojí na `classify`, `blocks` a pravidlech adaptéru.

Testy: `python -m unittest discover -s cms/tests -t cms/tests`.
