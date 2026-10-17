# Instalace editoru

Editor webu Khoder CMS běží jako služba na vašem serveru (VPS). Má vlastní adresu s HTTPS, přihlášení
ze souboru uživatelů a tlačítko Publikovat: úpravy sloučí s větví `main`, udělá build, commit a push do
gitu a commit nasadí na web. Bez serveru jde editor pustit i na vlastním počítači (§ Lokální režim).

Návod počítá s webem založeným podle ukázky (`cms/` v repu webu, `cms.toml` v kořeni, skill
`nastaveni-webu`) a s repem na GitHubu; jinde se liší jen nastavení klíčů v repu. Příklady používají
doménu `mojedilna.cz`, repo `git@github.com:vas-ucet/mojedilna.git` a soubory z `cms/install/`:

```
cms/install/cms-editor.service   systemd služba editoru
cms/install/editor.env           proměnné služby
cms/install/Caddyfile            web a editor za HTTPS
cms/install/github-deploy.yml    deploy po pushi do main (GitHub Actions)
```

## Jak to do sebe zapadá

```
prohlížeč ──HTTPS──> Caddy ──> 127.0.0.1:8765 editor (uživatel cms, checkout /home/cms/web)
                       │              │ push do main            │ ssh web@127.0.0.1 deploy <sha>
                       │              v                         v
                       │            GitHub <──── fetch ──── cms-deploy (uživatel web)
                       │                                        │
                       └── www.mojedilna.cz <── /var/www/mojedilna
```

- **Uživatel `cms`** spouští editor. Smí pushnout do repa (deploy key se zápisem), do složky webu
  zapisovat nesmí.
- **Uživatel `web`** vlastní složku webu a nasazuje přes `cms-deploy` vždy commit z `origin/main`.
  Editor ho volá přes ssh klíčem s vynuceným příkazem `cms-deploy cms`: ten nasadí jen HTML a sitemapy
  a podle pravidel z posledního plného deploye. Commit z editoru tak nezmění, co a kam se nasazuje.
- **Caddy** vydá certifikáty, servíruje web ze složky deploye a pouští ven editor, který sám poslouchá
  jen na `127.0.0.1`.

Web může ležet i jinde, třeba na hostingu bez shellu. Pak ho `cms-deploy` nasazuje přes `rsync` nebo
`sftp` (§ Web na jiném serveru).

## Požadavky

- VPS s Debianem 13 nebo Ubuntu 24.04 a přístupem přes `sudo`. Editor potřebuje Python 3.12 nebo
  novější (`python3 --version`), jen standardní knihovnu. Debian 12 má Python 3.11 a nestačí.
- Balíčky `git` a `caddy` (`sudo apt install git caddy`), pro web na jiném serveru i `rsync`.
- DNS: `www.mojedilna.cz`, `mojedilna.cz` a `editor.mojedilna.cz` míří na IP serveru, porty 80 a 443
  jsou otevřené. Přes ně Caddy získá certifikáty.
- Repo webu podle ukázky: `python build/build.py` končí `BUILD OK` a `cms.toml` má v `[deploy] site`
  adresu webu.

## 1. Uživatelé a složka webu

```
sudo useradd --create-home --shell /bin/bash web
sudo useradd --create-home --shell /bin/bash cms
sudo install -d -o web -g web -m 755 /var/www/mojedilna
```

Účty nemají heslo. Pracuje se v nich přes `sudo -iu web` a `sudo -iu cms`, ssh jen klíčem.

## 2. Deploy webu (uživatel web)

Všechny příkazy kroku jako `web` (`sudo -iu web`).

1. **Klíč pro čtení repa.**
   ```
   install -d -m 700 ~/.ssh
   ssh-keygen -t ed25519 -N "" -C "web cms-deploy" -f ~/.ssh/github_read
   ssh-keyscan github.com >> ~/.ssh/known_hosts
   cat ~/.ssh/github_read.pub
   ```
   Na GitHubu v repu webu: Settings › Deploy keys › Add deploy key, vložte veřejný klíč a **Allow write
   access nezaškrtávejte**.
2. **Repo jen ke čtení.**
   ```
   install -d -m 700 ~/cms
   git -c core.sshCommand="ssh -i ~/.ssh/github_read -o IdentitiesOnly=yes" clone --bare git@github.com:vas-ucet/mojedilna.git ~/cms/repo.git
   git --git-dir ~/cms/repo.git config core.sshCommand "ssh -i ~/.ssh/github_read -o IdentitiesOnly=yes"
   ```
3. **Spouštěč.**
   ```
   install -d ~/bin
   git --git-dir ~/cms/repo.git show main:cms/bin/cms-deploy > ~/bin/cms-deploy
   chmod 755 ~/bin/cms-deploy
   ```
4. **Cíl v `cms.toml`.** U sebe v repu webu nastavte složku webu na serveru, pak commit a push do `main`:
   ```toml
   [deploy.target]
   kind = "local"
   path = "/var/www/mojedilna"
   ```
5. **První deploy.**
   ```
   ~/bin/cms-deploy dry-run
   ~/bin/cms-deploy deploy
   ```
   `dry-run` vypíše, co by se nasadilo, `deploy` končí řádkem `OK: Deployed version …`. Druhý `deploy`
   hlásí `Změněno: 0 souborů`.

Plný deploy (`cms-deploy deploy`) bere `cms/deploy.py` a `cms.toml` z nasazovaného commitu a po úspěchu
je připne do `~/cms/pin/`. Režim `cms`, který volá editor, pouští jen připnutou verzi. Změnu cíle nebo
pravidel v `cms.toml` proto nasaďte plným deployem, ručně nebo po pushi (§ Deploy po pushi). Stav ukáže
`~/bin/cms-deploy status`, starší commit z `main` nasadí `~/bin/cms-deploy rollback <sha>`.

## 3. Editor (uživatel cms)

Příkazy jako `cms` (`sudo -iu cms`), kromě řádků se `sudo`, ty pouští správce serveru.

1. **Deploy key se zápisem.** Editor pushuje publikace do `main`.
   ```
   install -d -m 700 ~/.ssh
   ssh-keygen -t ed25519 -N "" -C "editor webu" -f ~/.ssh/github_write
   ssh-keyscan github.com >> ~/.ssh/known_hosts
   printf 'Host github.com\n  IdentityFile ~/.ssh/github_write\n  IdentitiesOnly yes\n' >> ~/.ssh/config
   cat ~/.ssh/github_write.pub
   ```
   Na GitHubu: Settings › Deploy keys › Add deploy key a **zaškrtněte Allow write access**. Když má
   `main` pravidla, která vyžadují pull request, přidejte v rulesetu do Bypass list položku Deploy keys.
   Jinak publikace skončí hláškou „Push selhal, nic se nepublikovalo“.
2. **Checkout a data.**
   ```
   git clone git@github.com:vas-ucet/mojedilna.git ~/web
   install -d -m 700 ~/data ~/data/journal ~/data/work
   ```
3. **Klíč pro deploy.** Editor volá `cms-deploy` jako `web` přes ssh na `127.0.0.1`.
   ```
   ssh-keygen -t ed25519 -N "" -C "editor cms-deploy cms" -f ~/.ssh/cms_deploy
   printf 'Host 127.0.0.1\n  User web\n  IdentityFile ~/.ssh/cms_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
   ssh-keyscan -t ed25519 127.0.0.1 >> ~/.ssh/known_hosts
   ```
   Správce přidá klíč uživateli `web` s vynuceným příkazem:
   ```
   sudo install -d -o web -g web -m 700 /home/web/.ssh
   echo "command=\"/home/web/bin/cms-deploy cms\",restrict,from=\"127.0.0.1\" $(sudo cat /home/cms/.ssh/cms_deploy.pub)" | sudo tee -a /home/web/.ssh/authorized_keys
   sudo chown web:web /home/web/.ssh/authorized_keys
   sudo chmod 600 /home/web/.ssh/authorized_keys
   ```
   Zkouška jako `cms`: `ssh -n web@127.0.0.1 status` vypíše stav deploye a
   `ssh -n web@127.0.0.1 "sh -c id"` skončí `CHYBA: režim cms umí jen deploy, dry-run a status`.
4. **Publikace v `cms.toml`.** U sebe v repu webu doplňte do sekcí, commit a push do `main`:
   ```toml
   [editor]
   local_publish = false                          # lokální editor nepublikuje, odkáže na server
   publish_url = "https://editor.mojedilna.cz"

   [publish]
   deploy_ssh = "web@127.0.0.1"                   # ssh na cms-deploy s vynuceným příkazem
   ```
   Na serveru pak `git -C ~/web pull`. Cíl ssh jde zadat i proměnnou `CMS_DEPLOY_SSH` v `editor.env`,
   ta má přednost.
5. **Uživatelé editoru.**
   ```
   python3 ~/web/cms/editor.py user jana --users ~/data/users.json --role admin --name "Jana Nováková" --email jana@mojedilna.cz
   ```
   Heslo se zadává dvakrát a skrytě, musí mít aspoň 12 znaků. Jméno a e-mail jsou autor commitu
   publikace. Role `admin` smí navíc upravit celý zdroj stránky a odemknout owner stránku, role
   `editor` všechno ostatní včetně Publikovat. Heslo změní `--password`, uživatele smaže `--delete`.
   Editor soubor uživatelů načte znovu sám. Změna hesla nebo smazání uživatele jeho přihlášení ukončí.
6. **Proměnné služby.**
   ```
   install -m 600 ~/web/cms/install/editor.env ~/editor.env
   ```
   V `~/editor.env` nastavte `CMS_URL` na adresu editoru (`https://` a bez cesty). `CMS_USERS` je
   soubor uživatelů, `CMS_SESSIONS` databáze přihlášení (díky ní restart nikoho neodhlásí),
   `CMS_JOURNAL` neodeslané úpravy mimo checkout, `CMS_WORK` dočasné worktree publikace a `CMS_SYNC_S`
   interval, po kterém editor stáhne `main`. Popis všech proměnných je v hlavičce `cms/editor.py`.
7. **Služba.**
   ```
   sudo install -m 644 /home/cms/web/cms/install/cms-editor.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now cms-editor
   sudo journalctl -u cms-editor -n 20
   ```
   V logu má být `Editor webu -> 127.0.0.1:8765 za https://editor.mojedilna.cz`. Bez platného souboru
   uživatelů, nebo když `CMS_URL` nezačíná `https://`, služba nenastartuje a důvod je v logu.

## 4. HTTPS a reverse proxy (Caddy)

```
sudo install -m 644 /home/cms/web/cms/install/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

V `Caddyfile` přepište domény a složku webu. Caddy sám získá certifikáty Let's Encrypt a obnovuje je.
Web servíruje ze složky deploye s odkazy bez `.html` a se stránkou `404.html`, `mojedilna.cz`
přesměruje na `www`.

Editoru Caddy posílá hlavičku `X-Real-IP` se skutečnou adresou klienta. Podle ní editor brzdí hádání
hesel: po 5 špatných pokusech z jedné adresy ji na 15 minut zablokuje. Bez té hlavičky by editor viděl
všechny klienty jako `127.0.0.1` a jeden útočník by přihlášení zablokoval všem.

**nginx místo Caddy** (třeba s CloudPanelem): vhost editoru s certifikátem (certbot) a

```
location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    client_max_body_size 5m;
}
```

Web pak `root /var/www/mojedilna;`, `try_files $uri $uri.html =404;` a `error_page 404 /404.html;`.

## 5. Zkouška

- `curl -s https://editor.mojedilna.cz/health` vrátí `ok`.
- `https://editor.mojedilna.cz` v prohlížeči přesměruje na přihlášení a po přihlášení ukáže seznam stránek.
- Malá úprava textu a Publikovat: kroky Sloučení s main, Build a verify, Sitemap, Commit a push, Deploy
  a Kontrola na webu skončí zeleně a commit je v `main` na GitHubu.

## Web na jiném serveru

Když web běží na hostingu, nasazuje ho `cms-deploy` ze serveru editoru přes `rsync` nebo `sftp`. Cíle
a jejich pole popisuje skill `nastaveni-webu` (§ 5 Deploy), cílová složka musí na hostingu existovat.
Klíč k hostingu má uživatel `web`:

```
ssh-keygen -t ed25519 -N "" -C "web hosting" -f ~/.ssh/hosting
ssh-keyscan server-hostingu.cz >> ~/.ssh/known_hosts
printf 'Host server-hostingu.cz\n  User ucet\n  IdentityFile ~/.ssh/hosting\n  IdentitiesOnly yes\n' >> ~/.ssh/config
```

Veřejný klíč `~/.ssh/hosting.pub` vložte do správy hostingu a změnu `[deploy.target]` nasaďte plným
deployem. Z `Caddyfile` pak smažte bloky webu, Caddy pouští jen editor.

## Deploy po pushi

Publikovat v editoru nasadí jen HTML a sitemapy. Změny kódu webu z vašeho počítače (styly, skripty,
nové stránky) nasadí plný deploy: ručně `sudo -iu web ~/bin/cms-deploy deploy`, nebo po každém pushi do
`main` workflow `cms/install/github-deploy.yml`:

1. U sebe vyrobte klíč jen pro GitHub Actions:
   `ssh-keygen -t ed25519 -N "" -C "github actions deploy" -f deploy_key`.
2. Na serveru přidejte do `/home/web/.ssh/authorized_keys` řádek s vynuceným příkazem, klíč pak smí jen
   nasadit špičku `main`:
   ```
   command="/home/web/bin/cms-deploy deploy",restrict ssh-ed25519 AAAA… github actions deploy
   ```
3. V repu na GitHubu (Settings › Secrets and variables › Actions) založte secret `DEPLOY_KEY` s obsahem
   souboru `deploy_key`, secret `DEPLOY_KNOWN_HOSTS` s výstupem `ssh-keyscan -t ed25519 <IP serveru>`
   a variable `DEPLOY_SSH` s hodnotou `web@<IP serveru>`. Soubory `deploy_key` a `deploy_key.pub` pak
   u sebe smažte.
4. `cms/install/github-deploy.yml` zkopírujte do `.github/workflows/deploy.yml`, commit a push. Běh
   workflow končí řádkem `OK: Deployed version …`.

Push z editoru workflow spustí taky. Plný deploy pak nic nového nenajde, editor commit už nasadil.

## Lokální režim

Editor jde pustit na vlastním počítači, z kořene repa webu:

```
python cms/editor.py
```

Poslouchá na `http://127.0.0.1:8765`, přihlášení nemá (jste admin) a končí Ctrl+C. Neodeslané úpravy
drží v `.cms/journal`, kterou git ignoruje.

Publikovat sloučí úpravy s `main`, udělá build, commit a push s vaším gitem. Commit pak nasadí podle
`cms.toml`:

- **Web bez editoru na serveru:** `[publish] deploy = "direct"` pouští `cms/deploy.py` z vašeho počítače
  rovnou na `[deploy.target]` (`rsync` nebo `sftp` s vaším ssh klíčem, na Windows jen `sftp`). Stav
  posledního deploye je v `~/.local/state/cms/`.
- **Web s editorem na serveru:** `[editor] local_publish = false` (§ 3, krok 4). Lokální editor Publikovat
  nenabídne a odkáže na `publish_url`. Úpravy z něj zůstanou v pracovní složce, commit a push uděláte
  sami.

## Provoz

- **Aktualizace balíčku.** Novou verzi `cms/` dostane server pushem do `main`. Editor do `CMS_SYNC_S`
  sekund (výchozí 120) posune checkout a po změně mimo složku stránek se sám restartuje. Novou verzi
  `cms/bin/cms-deploy` zkopírujte do `/home/web/bin/` ručně (§ 2, krok 3).
- **Neodeslané úpravy a změny v main.** Sync posouvá checkout jen dopředu a soubor s neodeslanou úpravou
  nepřepíše. Editor to ukáže v hlášce a checkout posune až publikace.
- **Odhlásit všechny:** `sudo systemctl stop cms-editor`, smazat `/home/cms/data/sessions.sqlite`,
  `sudo systemctl start cms-editor`.
- **Log:** `sudo journalctl -u cms-editor` ukáže start, přihlášení s adresou, restart po syncu a chyby.
  Kdo co v editoru udělal, je v `audit.jsonl` ve složce `CMS_JOURNAL`.
- **Záloha:** `/home/cms/data` (neodeslané úpravy, audit, uživatelé). Web sám je v gitu.
- **Rollback:** `sudo -iu web ~/bin/cms-deploy rollback <sha>` nasadí starší commit z `main`. Soubory
  přidané později zůstanou, deploy nemaže. Oprava pak jde revertem v gitu a běžným deployem.
