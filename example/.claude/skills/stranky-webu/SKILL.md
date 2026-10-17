---
name: stranky-webu
description: Postupy pro změny stránek webu s Khoder CMS, po kterých zůstanou texty editovatelné v editoru – nová stránka, sekce, FAQ, obrázek, údaj z dat (data-site), JSON-LD, hlavička stránky, nový krok buildu a čtení reportu pokrytí. Použij při každé změně HTML ve www/, šablon v content/partials/, content/pages.json, content/site.json nebo build/web.py.
---

# Stránky webu s Khoder CMS

Pravidla jsou v `CLAUDE.md` v kořeni repa, tady jsou postupy. Každá změna končí dvěma příkazy:

```
python build/build.py      # musí skončit BUILD OK
python cms/coverage.py     # oddíl „Zamčený viditelný text podle důvodu“ prázdný
```

Stejnou kontrolu pouští editor při každém uložení a úpravu, která by neprošla, nezapíše.

## Nová stránka

1. Zkopírujte nejpodobnější stránku z `www/` (obyčejná: `o-dilne.html`, s FAQ: `sluzby.html`) jako
   `www/<slug>.html`. Slug tvoří malá písmena bez diakritiky, číslice a pomlčky, adresa stránky je
   `/<slug>`.
2. Přidejte položku do `content/pages.json`:
   ```json
   "<slug>": {"kind": "handbuilt", "output": "www/<slug>.html", "source": "www/<slug>.html"}
   ```
   `kind` je `handbuilt`, nebo `owner` pro stránku, kterou správce smí měnit až po odemčení adminem
   (právní texty, obchodní podmínky).
3. Přepište hlavičku stránky (oddíl Hlavička stránky níž) a obsah v `<main>`.
4. Odkaz do menu nebo patičky patří do `content/partials/header.html` nebo `footer.html`, ne do
   stránky. `aria-current="page"` nepište, build ho dá odkazu na stránku samu.
5. Spusťte `python build/build.py`, `python build/sitemap.py` a `python cms/coverage.py --pages`.

Stránku smažte ze `www/` i z `content/pages.json` najednou a spusťte build a sitemapu. Odkazy, které na
ni vedou, ohlásí kontrola. Z cíle deploye soubor nezmizí, deploy nic nemaže.

## Hlavička stránky

```html
<title>Co vyrábíme: kuchyně, skříně a renovace | Truhlářství Habr</title>
<meta name="description" content="Kuchyně na míru, vestavěné skříně a renovace starého nábytku.">
<link rel="canonical" href="https://www.example.com/sluzby">
<meta property="og:type" content="website">
<meta property="og:url" content="https://www.example.com/sluzby">
<meta property="og:title" content="Co vyrábíme: kuchyně, skříně a renovace">
<meta property="og:description" content="Kuchyně na míru, vestavěné skříně a renovace starého nábytku.">
```

- `title` nejvýš 70 znaků a končí názvem webu podle `TITLE_SUFFIX` v `build/cms_adapter.py`,
  `description` nejvýš 170 znaků. Obojí se na každé stránce liší.
- `canonical` a `og:url` jsou adresa webu z `cms.toml` (`[deploy] site`) a `/<slug>`, úvod jen `/`.
- `og:title` je title bez názvu webu, editor ho tak odvozuje.
- Stránka s `<meta name="robots" content="noindex">` (třeba 404) canonical ani og nepotřebuje
  a sitemapa ji vynechá.
- Meta vždy jako `<meta name|property="…" content="…">` v tomhle pořadí atributů.
- Na stránce je právě jeden `<h1>` a `<html lang="cs">`.

## Sekce a texty

V editoru jde klikem upravit každý nejmenší prvek s vlastním textem, ve kterém jsou jen inline prvky
(`a`, `strong`, `em`, `span`, `br`, `small`, `abbr`, `time`…). Prvek bez vlastního textu, ve kterém jsou
jen odkazy (`<p class="actions">` s tlačítky), se rozpadne na jednotlivé odkazy.

```html
<!-- dobře: každý text ve vlastním prvku -->
<div class="item">
  <h2>Dílna</h2>
  <p>Dílna je ve dvoře za domem, vjezd je z&nbsp;boční ulice.</p>
  <p class="actions"><a class="button" href="/kontakt#poptavka">Poptat zakázku</a></p>
</div>

<!-- zamčené: „Dílna“ stojí volně vedle bloku <p> -->
<div class="item">Dílna <p>Dílna je ve dvoře za domem.</p></div>
```

- Třídy pro rozvržení už má `www/assets/style.css` (`wrap`, `section`, `split`, `columns`, `item`,
  `ticks`, `photo`, `lead`, `button`). Nové přidejte tamtéž.
- Text v `alt`, `title`, `aria-label` a `placeholder` a text v SVG mění správce v panelu Další texty.
  Text v jiných atributech, třeba `data-*`, editor neukáže.
- Skript webu (`www/assets/*.js`) smí přepínat třídy a atributy, text nikdy. Tlačítko, které mění
  stav, má pořád stejný text a stav ukazuje atributem a stylem, jako `aria-expanded` v `menu.js`.
  Report pokrytí najde text v inline skriptech a v souborech `.js` přímo ve `www/`, v podsložkách ne:
  tam ho hlídejte sami.

## FAQ

Viditelné FAQ a blok `FAQPage` na téže stránce, otázky ve stejném pořadí. Kontrola hlídá stejný počet.

```html
<details class="faq-item">
  <summary>Jak dlouho výroba trvá?</summary>
  <div class="faq-body"><p>Obvykle čtyři až osm týdnů od schválení návrhu.</p></div>
</details>
```

```json
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "Jak dlouho výroba trvá?",
      "acceptedAnswer": {"@type": "Answer", "text": "Obvykle čtyři až osm týdnů od schválení návrhu."}
    }
  ]
}
```

Změnu otázky v editoru propíše motor i do JSON-LD. Odpověď v JSON-LD je prostý text bez HTML a správce
ji mění zvlášť v panelu SEO.

## Obrázek

```html
<figure class="photo">
  <img src="/assets/img/skrine.webp"
       srcset="/assets/img/skrine-600.webp 600w, /assets/img/skrine.webp 1200w"
       sizes="(max-width: 960px) 100vw, 55vw"
       alt="Vestavěná skříň v podkroví, dveře kopírují šikmou střechu"
       width="1200" height="900" loading="lazy">
</figure>
```

- `alt`, `width` a `height` vždy, `width` a `height` podle velké verze. `loading="lazy"` u obrázků
  pod prvním výřezem.
- Každá fotka je ve dvou šířkách: velká verze a malá s šířkou v názvu (`skrine-600.webp`).
- Fotky ve `www/assets/img/` ukázky vytvořila AI. Nahraďte je fotkami vlastních zakázek ve stejných dvou
  šířkách. Se stejným názvem a poměrem stran stačí vyměnit soubory, jinak upravte `srcset`, `width`
  a `height`. `alt` popište podle skutečné fotky.

## Údaj z dat (data-site)

Údaj firmy, který stránka ukazuje, se bere z objektu `business` v `content/site.json`:

```html
<p data-site="address">Ukázková 12, 100 00 Praha</p>
<a data-site="telephone" href="tel:+420123456789">+420 123 456 789</a>
<a data-site="email" href="mailto:dilna@example.com">dilna@example.com</a>
```

- Atribut nese `<a>`, `<span>` nebo `<p>` bez vnořených prvků. Obsah prvku i `href` přepíše build,
  při psaní stačí cokoli.
- Klíč je cesta v `business`: `telephone` (formát `+420 123 456 789`, odkaz `tel:`), `email` (odkaz
  `mailto:`), `address` (ulice, PSČ a město), jinak libovolný textový údaj, třeba `name` nebo
  `address.addressLocality`.
- Hodnota se ukáže tak, jak je v datech. Údaj v podobě pro lidi, třeba otevírací doba „Po–Pá 7–16
  hodin“ vedle `openingHours` ve formátu schema.org, je obyčejný text.
- Odkazy `tel:` a `mailto:` kdekoli na stránce kontrola porovná s `content/site.json`.
- Přepis pro jednu stránku: `content/site.json` → `pages` → `<slug>` → klíč a hodnota. Platí pro údaje
  v textu i pro JSON-LD té stránky.

## JSON-LD

- Blok vždy `<script type="application/ld+json">` s platným JSON, víc typů v jednom bloku přes `@graph`.
- Blok s `"@type": "LocalBusiness"` je objekt `business` z `content/site.json`. Na novou stránku stačí
  `{"@type": "LocalBusiness"}`, build ho doplní. Jiné bloky na firmu odkazují přes
  `{"@id": "https://www.example.com/#business"}`.
- Panel SEO ukazuje texty jen u dvojic (`@type`, klíč) z `LD_ALLOW` v `build/cms_adapter.py`, skupiny
  pojmenuje `LD_TYPES`. Pro nový typ s textem pro správce přidejte obojí, třeba
  `("Product", "description"): "Popis produktu"` a `"Product": "Produkt"`. Odkazy, kontakty, ceny,
  data a kódy panel jako text neukáže nikdy.
- Text mimo `LD_ALLOW` je zamčený, v reportu pokrytí v řádku JSON-LD pod „zamčený“. Viditelný text to
  není, oddíl zámků viditelného textu zůstane prázdný.

## Nový krok buildu

Hodnotu, kterou je potřeba spočítat (součet, počet, údaj z jiného souboru), dejte nejdřív do
`content/site.json` a ukažte přes `data-site`. Když to nestačí:

1. V `build/web.py` napište funkci `render_<jméno>(s, slug, data)`, která vrátí stránku s doplněnou
   hodnotou a nic jiného nemění. Přidejte ji do `STEPS` a popisek do `STEP_LABEL`. Kontrola pak hlásí
   stránku, která datům neodpovídá.
2. Prvek s hodnotou označte vlastním atributem (třeba `data-calc`) a v `build/cms_adapter.py` ho
   přidejte do `Rules.atom_attrs` a `Rules.computed` s důvodem pro správce. Úseky, které krok přepisuje,
   vraťte i z `Rules.locked_spans`, jinak by je Najít a nahradit změnilo a build vrátil.
3. Build musí jít pustit dvakrát se stejným výsledkem: druhý běh nic nezmění.

## Report pokrytí

`python cms/coverage.py` říká, kolik viditelného textu jde upravit klikem, co je sdílené (hlavička,
patička), co počítá build a co je zamčené a proč. `--pages` přidá výpis po stránkách, `--json out.json`
strojový výstup. Zámky viditelného textu a oprava:

| Důvod v reportu | Oprava |
|---|---|
| Text stojí vedle blokových prvků | text do vlastního `<p>` nebo `<span>` |
| Text ve skriptu editor neukazuje | text do HTML, skript ho jen ukáže nebo skryje |
| Text pro skript webu v atributu data-… | text do HTML |
| Atribut … editor neukazuje | text do obsahu prvku, nebo do `alt`, `title`, `aria-label` |
| Text v JSON-LD panel SEO neukazuje | dvojice do `LD_ALLOW`, pokud ho má správce měnit |

Zámek podle třídy nebo id (`Rules.lock_classes`, `Rules.lock_ids` v adaptéru) je záměrný: správce ten
text měnit nemá a report ukáže důvod z adaptéru.
