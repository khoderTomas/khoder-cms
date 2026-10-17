# Web s Khoder CMS: pravidla pro Claude Code

Šablona z ukázkového webu Khoder CMS. Oddíl „O webu“ přepište na svůj web, ostatní platí pro každý web
postavený z ukázky.

## O webu

Ukázkový web smyšleného truhlářství: úvod, Co vyrábíme (s FAQ), O dílně, Kontakt, Ochrana osobních
údajů a 404. Ruční HTML česky, údaje firmy v `content/site.json`. Tady popište svůj web: komu je
určený, jakým tónem píše a co se na něm mění nejčastěji.

## Jak web funguje

Stránky ve `www/` jsou ruční HTML. Texty v nich mění správce webu v editoru Khoder CMS (klik do textu,
panel SEO) a vy v kódu. Editor najde jen text, který stojí v HTML stránky, v šabloně hlavičky nebo
patičky (`content/partials/`) nebo v datech webu (`content/site.json`). Text jinde (JavaScript, CSS,
obrázek) správce sám nezmění, musí za vámi.

```
cms.toml               nastavení: složka stránek, manifest, adresa webu, adaptér, build, deploy
www/                   stránky (zdroj i výstup buildu), obrázky, styly; nasazuje se celá složka
content/pages.json     manifest stránek: každé HTML ve www/ v něm musí být
content/site.json      údaje firmy (business) a jejich přepisy po stránkách (pages)
content/partials/      hlavička a patička, společné pro všechny stránky
build/web.py           kroky buildu a kontroly (jediné místo, editor je pouští při každém uložení)
build/build.py         build a kontrola celého webu
build/sitemap.py       www/sitemap.xml ze stránek bez noindex
build/cms_adapter.py   adaptér: co editor o tomhle webu potřebuje vědět
cms/                   motor Khoder CMS (needitovat, aktualizuje se celý z nové verze)
```

## Příkazy

Python 3.12 nebo novější, jen standardní knihovna (`python --version`, na Linuxu a macOS `python3`).
Na 3.11 motor nepřečte části f-stringů (PEP 701): Debian 12 nestačí, Debian 13 nebo Ubuntu 24.04 ano.

```
python build/build.py                    build a kontrola, chyba = exit 1
python build/build.py --verify           jen kontrola
python build/sitemap.py                  sitemapa
python cms/coverage.py                   kolik textu jde upravit klikem a co je zamčené
python cms/deploy.py deploy --dry-run    co by se nasadilo (z commitu na origin/main)
```

## Před každým commitem

1. `python build/build.py` končí `BUILD OK`. Chyby píše po stránkách. Co plní build (obsah mezi
   značkami PARTIAL, blok `LocalBusiness`, údaje `data-site`), opravte v šabloně nebo v datech, ne ve
   stránce.
2. `python build/sitemap.py`, když přibyla nebo ubyla stránka nebo se změnil `noindex`.
3. `python cms/coverage.py`: oddíl „Zamčený viditelný text podle důvodu“ je prázdný. Každý zámek
   v něm je text, který správce nezmění (postup opravy ve skillu `stranky-webu`).

## Pravidla, aby texty zůstaly editovatelné

- **Text jen v HTML, šablonách a datech.** Nikdy v JavaScriptu (`textContent`, `innerHTML`,
  `document.title`, řetězce ve skriptu), v CSS (`content:`), v atributech `data-*` ani v obrázku.
  Skript webu smí přepínat třídy a atributy, text bere z HTML.
- **Text nestojí volně vedle bloků.** `<div>Úvod <p>Odstavec</p></div>` editor zamkne. Každý text
  patří do vlastního prvku (`p`, `li`, nadpis, `a`, `span`).
- **Údaj z dat se nepíše ručně.** Telefon, e-mail, adresu a další údaje firmy nese prvek
  `<a>`, `<span>` nebo `<p>` s atributem `data-site`, třeba `<a data-site="telephone" href="tel:">`.
  Build doplní text i `href` z `content/site.json`, v editoru je to počítaný text.
- **Hlavička a patička jen v `content/partials/`.** Stránka má značky `<!-- PARTIAL:header -->`
  a `<!-- /PARTIAL:header -->` (stejně `footer`) na vlastních řádcích bez odsazení a build mezi ně
  vloží šablonu. Obsah mezi značkami ve stránce neměňte, build ho přepíše.
- **Meta v pevném tvaru:** nejdřív `name` nebo `property`, pak `content`, uvozovky dvojité, třeba
  `<meta name="description" content="…">` nebo `<meta property="og:title" content="…">`. V jiném
  tvaru je nenajde panel SEO ani kontrola.
- **JSON-LD** jen jako `<script type="application/ld+json">`. Blok s `"@type": "LocalBusiness"` plní
  build z `content/site.json`, ručně ho neměňte. Texty jiných typů ukazuje panel SEO, jen když jsou
  v `LD_ALLOW` v `build/cms_adapter.py`.
- **FAQ** jako `<details class="faq-item">` se `<summary>` (otázka) a `<div class="faq-body">`
  (odpověď) a na téže stránce blok `FAQPage` v JSON-LD se stejnými otázkami ve stejném pořadí.
- **Obrázek** vždy s `alt`, `width` a `height`.
- **Nová stránka** = soubor ve `www/` a položka v `content/pages.json`, se značkami hlavičky a patičky
  a s vlastním title a description.
- Stránky a šablony v UTF-8 s konci řádků LF, kontrola CRLF odmítne.
- Pevná mezera po jednopísmenné předložce nebo spojce a mezi číslem a jednotkou (`v&nbsp;Praze`,
  `80&nbsp;km`) jako ve stávajících stránkách. Editor ji při úpravě doplní sám, build ne.

## Kam sahat opatrně

- `build/cms_adapter.py` zná třídy menu (`.nav-toggle`, `.site-nav`, `is-open`, šířka 760 px) a rámy
  `.site-header__inner` a `.site-footer__inner`. Kdo je přejmenuje v šabloně nebo CSS, změní je i tady.
- `build/web.py` pouští build i editor. Nový krok buildu znamená i úpravu adaptéru (skill `stranky-webu`).
- `www/assets/img/`: fotky ukázky vytvořila AI. Nahraďte je fotkami vlastních zakázek ve stejných dvou
  šířkách.
- Stránka s `"kind": "owner"` v `content/pages.json` (tady ochrana osobních údajů) je v editoru zamčená,
  dokud ji admin neodemkne. V kódu se upravuje normálně.

## Podrobné postupy

- Skill `stranky-webu`: nová stránka, sekce, FAQ, obrázek, údaj z dat, JSON-LD, čtení reportu pokrytí.
- Skill `nastaveni-webu`: vlastní web z ukázky, `cms.toml`, údaje firmy, adaptér a deploy.

V repu motoru Khoder CMS je tenhle web ukázkou ve složce `example/` a motor leží o složku výš: místo
`cms/…` tam platí `../…` a pokrytí se pouští jako `python ../coverage.py --root .`.
