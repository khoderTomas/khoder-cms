# Ukázkový web

Malý web smyšleného truhlářství postavený tak, aby všechny jeho texty šly upravit v editoru Khoder CMS.
Slouží jako šablona pro vlastní web a zároveň jako testovací web motoru (`tests/test_example.py`).

## Co v něm je

```
cms.toml                  nastavení Khoder CMS: kde jsou stránky, adaptér, build, sitemapa, deploy
www/                      stránky webu (ruční HTML), obrázky, styly; nasazuje se celá složka
content/pages.json        manifest stránek: každé HTML ve www/ v něm musí být
content/site.json         údaje o firmě (business) a jejich přepisy po stránkách (pages)
content/partials/         hlavička a patička, společné pro všechny stránky
build/web.py              kroky buildu stránky a kontroly (jediné místo pro build i editor)
build/build.py            build a kontrola celého webu
build/sitemap.py          www/sitemap.xml ze stránek bez noindex
build/cms_adapter.py      adaptér: co editor o tomhle webu potřebuje vědět
```

Stránky: úvod, Co vyrábíme (s FAQ), O dílně, Kontakt, Ochrana osobních údajů (owner stránka, admin ji
musí před úpravou odemknout) a 404 (noindex, mimo sitemapu).

## Příkazy

Spouštějí se z této složky, stačí Python 3.12+ bez dalších balíčků.

```
python build/build.py            build + kontrola, chyba = exit 1
python build/build.py --verify   jen kontrola
python build/sitemap.py          sitemapa
python ../coverage.py --root .   kolik textu jde upravit klikem (v repu webu: python cms/coverage.py)
```

## Jak build pracuje

Stránky ve `www/` jsou zdroj i výstup: build je přepíše na místě a jen tam, kde nesedí na data. Druhý
běh nic nezmění. Kroky (`build/web.py`):

- **Hlavička a patička.** Stránka má značky `<!-- PARTIAL:header -->` a `<!-- /PARTIAL:header -->`
  (stejně `footer`) na vlastních řádcích. Build mezi ně vloží šablonu z `content/partials/` a odkazu na
  stránku samu přidá `aria-current="page"`. Text menu změněný v editoru se zapíše do šablony a propíše na
  všechny stránky.
- **Údaje o firmě v JSON-LD.** Blok `<script type="application/ld+json">` s `"@type": "LocalBusiness"`
  build naplní objektem `business` z `content/site.json`. Název, popis a adresu mění admin v panelu SEO
  (oddíl O vás), kontakty Claude Code v datech.
- **Údaje v textu.** Prvek s `data-site="telephone"`, `"email"` nebo `"address"` dostane hodnotu ze
  `content/site.json`, odkaz i `href`. V editoru je to počítaný text, ručně se nepřepisuje.

Kontrola hlídá, že stránka odpovídá datům a projde SEO: `lang`, title do 70 znaků a description do 170,
obojí na každé stránce jiné, jeden `h1`, canonical a `og:url` na adresu stránky (kromě noindex), `og:title`
a `og:description`, platné JSON-LD, FAQPage se stejným počtem otázek jako `<details class="faq-item">`,
obrázky s `alt`, `width` a `height`, interní odkazy na existující stránky a soubory a odkazy `tel:`
a `mailto:` podle `content/site.json`. Stejnou kontrolou projde každé uložení v editoru.

## Vlastní web z ukázky

1. Obsah složky zkopírujte do kořene repa webu a motor do něj jako `cms/`.
2. V `cms.toml` nastavte adresu webu (`[deploy] site`, z ní build počítá canonical a sitemapu),
   v `content/site.json` údaje firmy a v `build/cms_adapter.py` název webu v `TITLE_SUFFIX`.
3. Barvy, písmo a zaoblení jsou proměnné na začátku `www/assets/style.css`, každá barva má hodnotu pro
   světlý i tmavý režim. Písmo Archivo leží ve `www/assets/fonts/` (licence OFL), web nic nestahuje odjinud.
   Fotky ve `www/assets/img/` vytvořila AI: nahraďte je fotkami vlastních zakázek ve stejných dvou šířkách.
4. Texty pište do HTML, nikdy do JavaScriptu: editor je najde jen ve stránce, v šablonách a v datech.
5. Novou stránku přidejte do `www/` i do `content/pages.json`, se značkami hlavičky a patičky, a spusťte
   build.

Editor jako samostatný server s přihlášením a návod na nasazení přibudou s první verzí balíčku.
