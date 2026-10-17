# Převzetí a aktualizace

V repu webu je motor složka `cms/`, převzatá z vydané verze jako `git subtree`. Ve webu se `cms/`
neupravuje, další aktualizace by takovou úpravu přepsala. Co je specifické pro web, patří do adaptéru
(`[editor] adapter` v `cms.toml`) a do buildu webu. Chybu motoru nahlaste v repu Khoder CMS.

Příkazy se pouštějí z kořene repa webu s čistým pracovním stromem. Tag se píše celým jménem.

## První převzetí

```
git subtree add --prefix=cms https://github.com/khoderTomas/khoder-cms.git v0.1.0 --squash
```

Web podle ukázky pak nastaví skill `nastaveni-webu` (`example/.claude/skills/`).

## Aktualizace na novou verzi

1. Vaše verze je první nadpis v `cms/CHANGELOG.md`. Přečtěte si v `CHANGELOG.md` nové verze a níž
   v tomhle souboru jejich oddíly.
2. Stáhněte novou verzi:
   ```
   git subtree pull --prefix=cms https://github.com/khoderTomas/khoder-cms.git v0.2.0 --squash
   ```
3. Upravte adaptér podle oddílů verzí. Pak musí projít `python build/build.py` (končí `BUILD OK`)
   a `python cms/coverage.py`.
4. Commit a push do `main`. Editor na serveru si novou verzi stáhne sám a restartuje se. Změnil-li se
   `cms/bin/cms-deploy`, zkopírujte ho na server ručně (`INSTALACE.md` § Provoz).

## Rozhraní adaptéru

Adaptér uvádí ve třídě `Site` číslo rozhraní motoru, pro které je napsaný:

```python
class Site(CA.Site):
    rules = Rules()
    contract = 1
```

Editor a `coverage.py` ho při startu porovnají s číslem motoru (`CONTRACT` v `cms/adapter.py`).
Když nesedí, skončí chybou „Adaptér … je napsaný pro rozhraní …“. Upravte adaptér podle oddílů
verzí níž a teprve potom číslo zvyšte. Číslo bez úpravy adaptéru nepřepisujte, kontrola by pak nic
nechránila.

## 0.1.0

Rozhraní 1. První verze, není z čeho aktualizovat. Adaptér napsaný před 0.1.0 podle starší kopie
motoru dostane do třídy `Site` řádek `contract = 1`.
