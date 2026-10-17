/* Editor webu (Khoder CMS, cms/app.py) a šasi stránek hostitele.
   Editor běží pod cestou BASE (<meta name="ke-base">): v adminu /web, ve vývojovém editoru
   prázdná. Režimy podle <meta name="ke-mode">: edit (úprava stránky <base>/<slug>?e=1,
   skripty webu tam neběží), list (seznam stránek Web), source (celý zdroj stránky, jen role
   admin); jiné režimy jsou stránky hostitele, jejich skripty používají window.KE.
   Seznam, zdroj a stránky hostitele stojí na šasi design language (.k-shell od hostitele):
   lišta editoru se tam nekreslí (hostitel plní .k-top__tools, KE.tools), toast je .k-toast
   vpravo dole, hlášky .k-notice v toku nad obsahem, dialog KE.dialog (nativní <dialog>),
   zásuvka KE.sheet, ikony KE.ic ze spritu. Úprava stránky je táž sada nad stránkou webu:
   prvky editoru mají třídu .ke-ui (tokeny sady jen v nich, web má proměnné stejných jmen)
   a ikony ze spritu s předponou ke-i-. Publikovat žije jen na seznamu (dialog, z úpravy
   stránky odkaz <base>/#publikovat). Menu a patička ze sdíleného souboru (data-ke-part) se
   přepisují přímo na stránce, uložení potvrdí dialog s rozsahem změny a owner stránkami (B6).
   Texty mimo bloky (alt, title, aria-label, placeholder, text v SVG) a skryté bloky má panel
   Další texty a popover u obrázku, pole a textu v SVG (B7).
   Podoba podle schválených prototypů (A4: prototype/cms-a4, B6: prototype/cms-b6, B7: prototype/cms-b7);
   ukládá se klikem mimo blok nebo Ctrl+Enter, přepnutí okna neukládá a rozepsaný text drží localStorage. */
(function () {
  'use strict';

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  function meta(n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }
  function setMeta(n, v) { var m = document.querySelector('meta[name="' + n + '"]'); if (m) m.content = v; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function keep(area, k, v) {
    try {
      var s = window[area];
      if (v === undefined) return s.getItem(k);
      if (v === null) s.removeItem(k); else s.setItem(k, v);
    } catch (e) { return null; }
    return null;
  }
  function store(k, v) { return keep('localStorage', k, v); }
  function sess(k, v) { return keep('sessionStorage', k, v); }
  function plural(n, one, few, many) { return n + ' ' + (n === 1 ? one : n >= 2 && n <= 4 ? few : many); }

  var MODE = meta('ke-mode') || 'edit';
  var SLUG = meta('ke-slug'), KIND = meta('ke-kind'), LOCK = meta('ke-lock');
  var PATH = meta('ke-path') || SLUG;   // soubor stránky v repu (generovaná: výstup buildu)
  var HOSTED = ['edit', 'list', 'source'].indexOf(MODE) < 0;   // stránka hostitele, ne editoru webu
  var SHELL = document.querySelector('.k-shell');   // šasi design language (všechno kromě úpravy stránky)
  var BASE = meta('ke-base');             // cesta editoru webu: /web v adminu, '' ve vývojovém editoru
  var ROLE = meta('ke-role') || 'admin';  // editor nesmí celý zdroj ani odemknout owner stránku
  var PUBLISH = meta('ke-publish') !== '0';       // vývojový editor nepublikuje (cms.toml [editor])
  var PUBLISH_URL = meta('ke-publish-url');
  // konec title s názvem webu (adaptér webu, Rules.title_suffix): Sjednotit v panelu SEO ho utne
  var SUFFIX = new RegExp(meta('ke-title-suffix') || '(?!)');
  // ikona ze spritu (lucide, admin_page ho vkládá do stránky); jméno je vždy konstanta z kódu,
  // test_nabidky_vzhled hlídá, že je ve spritu. Python protějšek: server.ic. Na stránce webu mají
  // symboly předponu ke-i- (web má vlastní #i-…, view.edit_view vkládá sprite hostitele).
  var ICON = MODE === 'edit' ? '#ke-i-' : '#i-';
  function kic(name, size) {
    return '<svg class="k-ic' + (size ? ' k-ic--' + size : '') + '" aria-hidden="true"><use href="' + ICON + esc(name) + '"/></svg>';
  }
  var UI = MODE === 'edit' ? ' ke-ui' : '';   // prvky nad stránkou webu nesou tokeny sady

  /* ── API: /api/… editoru webu jde pod BASE, nabídky (/api/nabidky/…) mají BASE prázdnou ── */
  function api(method, url, body) {
    var o = {method: method, headers: {'X-KE': '1'}, cache: 'no-store'};
    if (body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    return fetch(url.indexOf('/api/') === 0 ? BASE + url : url, o).then(function (r) {
      return r.json().catch(function () { return {error: 'Server vrátil ' + r.status + '.'}; })
        .then(function (b) { return {status: r.status, ok: r.ok, body: b || {}}; });
    });
  }
  function offline() {
    toast(meta('ke-offline') || 'Server editoru neodpovídá. Zkus to za chvíli znovu.', 'err');
  }

  /* ── lišta úpravy stránky (ostatní stránky mají šasi .k-shell a lištu .k-top) ── */
  var bar = null, banners = document.createElement('div');
  if (SHELL) {
    var wn = $('#wNotices');          // seznam stránek Web: hlášky mají místo pod hlavičkou
    if (wn) banners = wn;
    else {
      banners.className = 'nb-notices';
      var kwrap = $('.k-wrap', SHELL);
      kwrap.insertBefore(banners, kwrap.firstChild);
    }
  } else {
    // Lišta v k-* (prototyp B6): na telefonu nástroje pod ⋯, Publikovat jediné primární
    var tool = function (id, icon, label) {
      return '<button class="k-btn k-btn--ghost k-btn--sm ke-tool" type="button" id="' + id + '">' + kic(icon) + '<span class="ke-lbl">' + label + '</span>'
        + (id === 'keChanges' ? '<span class="k-count" id="keCount" data-ke-count>0</span>' : '') + '</button>';
    };
    var opt = function (t, icon, label) {
      return '<button class="k-opt" type="button" role="menuitem" data-tool="' + t + '">' + kic(icon) + label
        + (t === 'keChanges' ? '<span class="k-count k-opt__count" data-ke-count>0</span>' : '') + '</button>';
    };
    bar = document.createElement('div');
    bar.className = 'ke-bar ke-ui';
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', 'Editor textů');
    bar.innerHTML = '<a class="k-btn k-btn--ghost k-btn--sm ke-back" href="' + esc(BASE) + '/" aria-label="Seznam stránek Web">' + kic('arrow-left') + '<span class="ke-lbl">Web</span></a>'
      + '<span class="ke-sep"></span>'
      + '<span class="k-select ke-pagesel"><select class="k-input k-input--sm" id="kePage" aria-label="Stránka"><option value="' + esc(SLUG) + '">/' + esc(SLUG === 'index' ? '' : SLUG) + '</option></select></span>'
      + '<div class="k-seg k-seg--sm" role="radiogroup" aria-label="Režim">'
      + '<button class="k-seg__opt" type="button" role="radio" aria-checked="true" id="keModeEdit">Upravit</button>'
      + '<button class="k-seg__opt" type="button" role="radio" aria-checked="false" id="keModeView">Náhled</button></div>'
      + '<span class="ke-spacer"></span>'
      + tool('keSeo', 'search', 'SEO') + tool('keExtra', 'scan-text', 'Další texty') + tool('keFind', 'replace', 'Najít a nahradit') + tool('keChanges', 'history', 'Změny')
      + '<details class="k-menu ke-more"><summary class="k-ibtn" aria-label="Další nástroje">' + kic('ellipsis') + '</summary>'
      + '<div class="k-pop" role="menu">' + opt('keSeo', 'search', 'SEO') + opt('keExtra', 'scan-text', 'Další texty') + opt('keFind', 'replace', 'Najít a nahradit')
      + opt('keChanges', 'history', 'Změny') + '</div></details>'
      + (PUBLISH ? '<button class="k-btn k-btn--primary k-btn--sm ke-pub" type="button" id="kePublish">' + kic('send') + '<span class="ke-lbl">Publikovat</span></button>' : '');
    document.body.insertBefore(bar, document.body.firstChild);
    banners.className = 'ke-banners ke-ui';
    bar.parentNode.insertBefore(banners, bar.nextSibling);
    document.body.classList.add('ke-edit');
    var syncTop = function () { document.body.style.setProperty('--ke-top', (48 + banners.offsetHeight) + 'px'); };
    if (window.ResizeObserver) new ResizeObserver(syncTop).observe(banners);
    // menu ⋯ na telefonu: položka spustí nástroj z lišty, klik mimo a Esc ho zavřou
    var more = $('.ke-more', bar);
    more.addEventListener('click', function (e) {
      var t = e.target.closest('[data-tool]');
      if (!t) return;
      more.open = false;
      var b = $('#' + t.dataset.tool);
      if (b) b.click();
    });
    document.addEventListener('click', function (e) { if (more.open && !more.contains(e.target)) more.open = false; });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && more.open) { more.open = false; $('summary', more).focus(); } });
  }

  function setCount(n) {
    if (typeof n !== 'number') return;
    $$('#keCount, [data-ke-count]').forEach(function (c) { c.textContent = n; });
  }

  /* ── navigace (nepustí pryč rozepsaný text) ── */
  function go(url) {
    if (cur && cur.el.innerHTML !== cur.before) {
      toast('Nejdřív ulož (Ctrl+Enter), nebo zruš (Esc) rozepsaný text.', 'info');
      return false;
    }
    location.href = url;
    return true;
  }
  function pageUrl(slug, q) { return BASE + '/' + slug + (q || ''); }
  var PAGES = {};          // slug → {kind, name} ze seznamu stránek (dialog sdílené změny)
  if (bar) {
    var sel = $('#kePage');
    api('GET', '/api/pages').then(function (r) {
      (r.body.pages || []).forEach(function (p) { PAGES[p.slug] = p; });
      sel.innerHTML = (r.body.pages || []).map(function (p) {
        return '<option value="' + esc(p.slug) + '"' + (p.slug === SLUG ? ' selected' : '') + '>/' + esc(p.slug === 'index' ? '' : p.slug)
          + (p.kind === 'owner' ? '  · owner' : p.kind === 'generated' ? '  · generovaná' : '') + '</option>';
      }).join('');
    }, function () {});
    sel.onchange = function () { if (!go(pageUrl(sel.value, '?e=1'))) sel.value = SLUG; };
    $('#keModeEdit').onclick = function () { if (MODE !== 'edit') go(pageUrl(SLUG, '?e=1')); };
    $('#keModeView').onclick = function () { go(pageUrl(SLUG)); };
  }

  /* ── bannery ── */
  // Banner je .k-notice v toku nad obsahem (tón warn, ikonu dává html), na úpravě stránky pod lištou;
  // cls ho nahradí (hlášky seznamu Web s plochou w-tone--…).
  function banner(id, html, cls) {
    var b = $('#keB-' + id, banners);
    if (!html) { if (b) b.remove(); return null; }
    if (!b) {
      b = document.createElement('div');
      b.id = 'keB-' + id;
      b.setAttribute('role', 'status');
      banners.appendChild(b);
    }
    b.className = cls || 'k-notice k-notice--warn';
    b.innerHTML = html;
    return b;
  }
  function note(icon, text, action) {
    return kic(icon) + '<div class="k-notice__body"><span>' + text + '</span></div>' + (action ? '<div class="k-notice__action">' + action + '</div>' : '');
  }
  // Owner stránku odemyká jen role admin, na 30 minut a jen pro sebe (ADR 0003, ADR 0014);
  // menu a patičku mění i bez odemčení, potvrzením v dialogu sdílené změny (ADR 0003 dodatek)
  var UNTIL = meta('ke-until');
  var PARTS = {};
  try { PARTS = JSON.parse(meta('ke-parts') || '{}'); } catch (e) { PARTS = {}; }
  var OWN_PART = Object.keys(PARTS).length ? ' Menu a patičku upravíš i bez odemčení, změnu potvrdíš v dialogu.' : '';
  if (MODE === 'edit' && LOCK && KIND === 'owner') {
    banner('lock', ROLE === 'admin'
      ? note('lock', '<b>Owner stránka</b>: texty jsou zamčené (ADR 0003). Odemknout ji smí jen admin, na 30 minut a jen pro sebe.' + OWN_PART,
        '<button class="k-btn k-btn--secondary k-btn--sm" type="button" id="keUnlock">' + kic('lock-open') + 'Odemknout na 30 minut</button>')
      : note('lock', '<b>Owner stránka</b>: texty jsou zamčené (ADR 0003). Odemknout ji smí jen admin.'));
    if ($('#keUnlock')) $('#keUnlock').onclick = function () {
      api('POST', '/api/unlock', {slug: SLUG}).then(function (r) {
        if (!r.ok) return toast(r.body.error, 'err');
        sess('ke-after', JSON.stringify({toast: 'Odemčeno do ' + r.body.until + ', jen pro tebe. Úpravy jdou do ' + PATH + '.'}));
        location.reload();
      }, offline);
    };
  } else if (MODE === 'edit' && LOCK) {
    banner('lock', note('lock', '<b>Generovaná stránka</b>: ' + esc(LOCK)));
  } else if (MODE === 'edit' && KIND === 'owner') {
    banner('lock', note('lock-open', '<b>Owner stránka je odemčená</b>' + (UNTIL ? ' do ' + esc(UNTIL) : '') + ', jen pro tebe. Úpravy jdou do ' + esc(PATH) + '.',
      '<button class="k-btn k-btn--secondary k-btn--sm" type="button" id="keRelock">' + kic('lock') + 'Zamknout</button>'));
    $('#keRelock').onclick = function () {
      if (cur) { toast('Nejdřív ulož (Ctrl+Enter), nebo zruš (Esc) rozepsaný text.', 'info'); return; }
      api('POST', '/api/lock', {slug: SLUG}).then(function (r) {
        if (!r.ok) return toast(r.body.error, 'err');
        sess('ke-after', JSON.stringify({toast: 'Zamčeno.'}));
        location.reload();
      }, offline);
    };
  }

  function statusBanner(st) {
    var msgs = (st.messages || []).concat((st.drift && st.drift.problems) || []);
    var key = JSON.stringify(msgs);
    if (!msgs.length || sess('ke-dismissed') === key) { banner('status', null); return; }
    var lines = msgs.map(function (m) { return '<div>' + esc(m) + '</div>'; }).join('');
    var b = banner('status', kic('triangle-alert') + '<div class="k-notice__body">' + lines + '</div>'
      + '<button class="k-notice__x" type="button" aria-label="Skrýt">' + kic('x') + '</button>',
      MODE === 'list' ? 'k-notice k-notice--warn w-tone--warn w-top' : null);
    $('.k-notice__x', b).onclick = function () { sess('ke-dismissed', key); banner('status', null); };
  }

  // čas commitu nebo publikace: „dnes 20:41“, jinak „14. 10. 20:41“ (iso = ISO 8601 nebo epoch v s)
  function when(t) {
    var d = typeof t === 'number' ? new Date(t * 1000) : new Date(t), now = new Date();
    if (isNaN(d)) return '';
    var hm = d.getHours() + ':' + ('0' + d.getMinutes()).slice(-2);
    return d.toDateString() === now.toDateString() ? 'dnes ' + hm : d.getDate() + '. ' + (d.getMonth() + 1) + '. ' + hm;
  }
  function slugText(s) { return '/' + (s === 'index' ? '' : s); }
  // commity z main (gitsync.log_detail): kdo je napsal, u agenta jméno z Co-authored-by
  function commitsHtml(list) {
    return '<ul class="w-commits">' + (list || []).map(function (c) {
      var who = (c.coauthors && c.coauthors.length ? c.coauthors[0] : c.author) || '';
      return '<li><span class="k-mono">' + esc(c.sha) + '</span><span class="w-who">' + esc(when(c.time)) + (who ? ' · ' + esc(who) : '')
        + '</span><span>' + esc(c.subject) + '</span></li>';
    }).join('') + '</ul>';
  }
  // Seznam Web: hlášky ze stavu editoru (sync checkoutu, nenasazená publikace, vývojový editor, běžící publikace)
  function webNotices(st) {
    var s = st.sync;
    if (s && s.blocked) {
      var pages = (s.pages || []).map(slugText);
      banner('sync', kic('git-merge') + '<div class="k-notice__body"><span><b>V main ' + (s.count === 1 ? 'čeká ' : 'čekají ') + plural(s.count, 'commit', 'commity', 'commitů')
        + ', které admin nestáhne:</b> mění ' + (pages.length ? esc(pages.slice(0, 3).join(', ')) + (pages.length > 3 ? ' a další stránky' : '') : 'soubory')
        + ' s nepublikovanými úpravami. Do publikace ukazuje web ve stavu z ' + esc(when(s.head)) + '. Publikuj, ať se úpravy nestřetnou s dalšími změnami.</span>'
        + commitsHtml(s.commits) + '</div>'
        + (PUBLISH ? '<div class="k-notice__action"><button class="k-btn k-btn--secondary k-btn--sm" type="button" data-pub>Zkontrolovat a publikovat</button></div>' : ''),
        'k-notice k-notice--warn w-tone--warn w-top');
    } else banner('sync', s && s.error ? kic('triangle-alert') + '<span><b>Stažení main se nepovedlo.</b> ' + esc(s.error) + '</span>' : null,
      'k-notice k-notice--warn w-tone--warn');
    var rd = st.redeploy;
    banner('redeploy', rd && !st.publishing ? kic('circle-alert') + '<div class="k-notice__body"><span><b>Publikace ' + esc(rd.commit.slice(0, 7)) + ' je v main, ale na webu není.</b> '
      + 'Nasadit znovu nasadí tentýž commit, nic dalšího se nepublikuje.</span></div>'
      + (PUBLISH ? '<div class="k-notice__action"><button class="k-btn k-btn--secondary k-btn--sm" type="button" data-redeploy>' + kic('refresh-cw') + 'Nasadit znovu</button></div>' : '')
      : null, 'k-notice k-notice--err w-tone--err');
    banner('running', st.publishing ? kic('loader-circle') + '<span><b>Právě běží publikace.</b> Ukládat úpravy jde zase po doběhnutí.</span>'
      + '<div class="k-notice__action"><button class="k-btn k-btn--secondary k-btn--sm" type="button" data-pub>Ukázat průběh</button></div>' : null, 'k-notice');
    var host = (PUBLISH_URL || '').replace(/^https?:\/\//, '');
    banner('local', PUBLISH ? null : kic('info') + '<span><b>Vývojový editor na ' + esc(location.host) + '.</b> Úpravy odsud na web nejdou, Publikovat je vypnuté v cms.toml.'
      + (PUBLISH_URL ? ' Texty webu uprav na <a class="k-link" href="' + esc(PUBLISH_URL) + '">' + esc(host) + '</a>.' : '') + '</span>', 'k-notice');
  }
  // Úprava stránky: commity z main čekají na publikaci (sync je kvůli úpravám nestáhl)
  function editSync(st) {
    var s = st.sync;
    banner('sync', s && s.blocked ? note('git-merge', '<b>V main ' + (s.count === 1 ? 'čeká ' : 'čekají ')
      + plural(s.count, 'commit', 'commity', 'commitů') + '</b>' + ((s.pages || []).indexOf(SLUG) >= 0 ? ', mění i tuhle stránku' : '')
      + '. Admin je stáhne až po publikaci, do té doby upravuješ stav z ' + esc(when(s.head)) + '.',
      PUBLISH ? '<a class="k-btn k-btn--secondary k-btn--sm" href="' + esc(BASE) + '/#publikovat">Zkontrolovat a publikovat</a>' : '') : null);
  }
  function pollStatus() {
    api('GET', '/api/status').then(function (r) {
      setCount(r.body.count);
      statusBanner(r.body);
      if (MODE === 'list') webNotices(r.body);
      if (MODE === 'edit') editSync(r.body);
      if (r.body.drift && r.body.drift.state === 'running') setTimeout(pollStatus, 3000);
    }, offline);
  }
  if (!HOSTED) pollStatus();

  /* ── toast a dialog ── */
  // Jeden toast naráz (nový nahradí starý), chyba drží 7 s, ostatní 4,2 s. .k-toast v kontejneru
  // .k-toasts vpravo dole; když je otevřený dialog, kontejner jde do něj, ať chyba nezůstane pod závojem.
  var toastEl = null, toastT = 0, toastBox = null;
  function dropToast() { clearTimeout(toastT); if (toastEl) { toastEl.remove(); toastEl = null; } }
  function toast(msg, kind) {
    dropToast();
    toastEl = document.createElement('div');
    toastEl.setAttribute('role', kind === 'err' ? 'alert' : 'status');
    if (!toastBox) { toastBox = document.createElement('div'); toastBox.className = 'k-toasts' + (UI ? ' ke-toasts' + UI : ''); }
    var host = document.querySelector('dialog.k-dialog[open]') || document.body;
    if (toastBox.parentNode !== host) host.appendChild(toastBox);
    toastEl.className = 'k-toast' + (kind === 'ok' ? ' k-toast--ok' : kind === 'err' ? ' k-toast--err' : '');
    toastEl.innerHTML = (kind === 'ok' ? kic('circle-check') : kind === 'err' ? kic('circle-alert') : kic('info'))
      + '<div class="k-toast__text">' + esc(msg) + '</div>'
      + '<button class="k-toast__x" type="button" aria-label="Zavřít">' + kic('x') + '</button>';
    $('.k-toast__x', toastEl).onclick = dropToast;
    toastBox.appendChild(toastEl);
    toastT = setTimeout(dropToast, kind === 'err' ? 7000 : 4200);
  }

  // Dialog design language (README § 6.9): nativní <dialog class="k-dialog"> přes showModal(),
  // Esc, klik na závoj a [data-close] zavřou, fokus se vrátí na spouštěč. html = obsah dialogu
  // (__head s .k-dialog__title, __body, __foot), cls = další třídy (k-dialog--sm/--lg).
  // onClose(how) dostane 'cancel' (závoj, [data-close]), 'esc', nebo hodnotu z closeDialog(how).
  var dlgEl = null, dlgSeq = 0;
  function dialog(html, onClose, cls) {
    closeDialog('replaced');
    var d = document.createElement('dialog');
    d.className = 'k-dialog' + (cls ? ' ' + cls : '') + UI;
    d.innerHTML = html;
    var t = $('.k-dialog__title', d);
    if (t) {
      if (!t.id) t.id = 'keDlg' + (++dlgSeq);
      d.setAttribute('aria-labelledby', t.id);
    }
    d.keBack = document.activeElement;
    d.keClose = onClose;
    var down = null;     // tažení výběru z pole ven na závoj dialog nezavře
    d.addEventListener('mousedown', function (e) { down = e.target; });
    d.addEventListener('click', function (e) {
      if ((e.target === d && down === d) || e.target.closest('[data-close]')) closeDialog('cancel');
    });
    // Esc: úklid hned při cancel; když ho prohlížeč přesto zavře sám, doběhne přes close
    d.addEventListener('cancel', function (e) { e.preventDefault(); closeDialog('esc'); });
    d.addEventListener('close', function () { if (dlgEl === d) closeDialog('esc'); });
    document.body.appendChild(d);
    dlgEl = d;
    d.showModal();
    return d;
  }
  function closeDialog(how) {
    if (!dlgEl) return;
    var d = dlgEl;
    dlgEl = null;
    if (toastBox && d.contains(toastBox)) document.body.appendChild(toastBox);
    if (d.open) d.close();
    d.remove();
    if (d.keBack && d.keBack.isConnected && d.keBack.focus) d.keBack.focus();
    if (d.keClose) d.keClose(how);
  }
  // Potvrzení a chyby editoru textů v k-dialog: o.title, o.desc (html), o.body (html), o.yes / o.no
  // (popisky tlačítek, data-a="yes" / "no"), o.done(ano) po zavření, o.cls (k-dialog--lg).
  // Bez o.yes je to hláška s jedním tlačítkem (data-a="ok").
  function ask(o) {
    var foot = o.yes
      ? '<button class="k-btn k-btn--secondary" type="button" data-a="no">' + esc(o.no || 'Zrušit') + '</button>'
        + '<button class="k-btn k-btn--primary" type="button" data-a="yes" autofocus>' + (o.yesIcon ? kic(o.yesIcon) : '') + esc(o.yes) + '</button>'
      : '<button class="k-btn k-btn--primary" type="button" data-a="ok" autofocus>Rozumím</button>';
    var d = dialog('<div class="k-dialog__head"><div><h2 class="k-dialog__title">' + esc(o.title) + '</h2>'
      + (o.desc ? '<p class="k-dialog__desc">' + o.desc + '</p>' : '') + '</div>'
      + '<button class="k-ibtn" type="button" data-close aria-label="Zavřít">' + kic('x') + '</button></div>'
      + '<div class="k-dialog__body"><div class="ke-dlg">' + (o.body || '') + '</div></div>'
      + '<div class="k-dialog__foot k-dialog__foot--bd">' + (o.note ? '<span class="k-dialog__note">' + esc(o.note) + '</span>' : '') + foot + '</div>',
      function (how) { if (o.done) o.done(how === 'yes'); }, o.cls);
    d.addEventListener('click', function (e) {
      var a = e.target.closest('[data-a]');
      if (a && !a.disabled) closeDialog(a.dataset.a);
    });
    return d;
  }
  function listHtml(items) {
    return items && items.length ? '<ul class="ke-list">' + items.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '';
  }
  function failDialog(b, hint) {
    ask({title: b.error || 'Nepovedlo se.', body: listHtml(b.details) + (hint ? '<p class="ke-hint">' + esc(hint) + '</p>' : '')});
  }

  /* ── slovní diff ── */
  function wdiff(a, b) {
    var A = String(a || '').split(/(\s+)/), B = String(b || '').split(/(\s+)/), n = A.length, m = B.length, i, j;
    var L = [];
    for (i = 0; i <= n; i++) L.push(new Array(m + 1).fill(0));
    for (i = n - 1; i >= 0; i--) for (j = m - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    var out = '', del = '', ins = '';
    function flush() {
      out += del.trim() ? '<del>' + esc(del) + '</del>' : esc(del);
      out += ins.trim() ? '<ins>' + esc(ins) + '</ins>' : esc(ins);
      del = ins = '';
    }
    i = 0; j = 0;
    while (i < n && j < m) {
      if (A[i] === B[j]) { flush(); out += esc(A[i]); i++; j++; }
      else if (L[i + 1][j] >= L[i][j + 1]) del += A[i++];
      else ins += B[j++];
    }
    while (i < n) del += A[i++];
    while (j < m) ins += B[j++];
    flush();
    return out;
  }

  /* ── zásuvky ── */
  // Zásuvka design language (README § 6.9): <aside class="k-drawer k-drawer--side"> vpravo bez
  // závoje, hlavička s titulkem a zavíracím .k-ibtn, vrací .k-drawer__body. Esc (když není otevřený
  // dialog) a křížek zavřou, fokus se vrátí na spouštěč; zmizí-li spouštěč překreslením, vezme se
  // opts.back() (element). opts.name = panel editoru textů (changes, find, seo), drží ho openDrawer.
  var sheetEl = null, sheetSeq = 0, openDrawer = null;
  function sheet(title, opts) {
    opts = opts || {};
    var back = document.activeElement;
    closeSheet(true);
    var a = document.createElement('aside'), tid = 'keSheet' + (++sheetSeq);
    a.className = 'k-drawer k-drawer--side' + (opts.cls ? ' ' + opts.cls : '') + UI;
    openDrawer = opts.name || null;
    a.setAttribute('aria-labelledby', tid);
    a.innerHTML = '<div class="k-drawer__head"><div class="k-drawer__titles"><h2 class="k-drawer__title" id="' + tid + '">' + esc(title) + '</h2></div>'
      + '<button class="k-ibtn" type="button" aria-label="Zavřít" data-sheet-x>' + kic('x') + '</button></div><div class="k-drawer__body"></div>';
    a.keBack = back;
    a.keOpts = opts;
    $('[data-sheet-x]', a).onclick = function () { closeSheet(); };
    document.body.appendChild(a);
    sheetEl = a;
    $('[data-sheet-x]', a).focus();
    return $('.k-drawer__body', a);
  }
  function closeSheet(silent) {
    if (!sheetEl) return;
    var a = sheetEl;
    sheetEl = null;
    openDrawer = null;
    a.remove();
    if (silent) return;
    var b = a.keBack && a.keBack.isConnected ? a.keBack : (a.keOpts.back ? a.keOpts.back() : null);
    if (b && b.focus) b.focus();
    if (a.keOpts.onClose) a.keOpts.onClose();
  }
  document.addEventListener('keydown', function (e) {
    if (sheetEl && e.key === 'Escape' && !dlgEl && !e.defaultPrevented) { e.preventDefault(); closeSheet(); }
  });
  // panel editoru textů: zásuvka se jménem pod lištou úpravy stránky (na seznamu Web pod lištou šasi)
  function drawer(name, title) { return sheet(title, {name: name, cls: 'ke-sheet'}); }
  function closeDrawer() { closeSheet(); }
  function hhmm(t) { var d = new Date(t * 1000); return d.getHours() + ':' + ('0' + d.getMinutes()).slice(-2); }
  function pageLink(s) {
    var name = '<b>/' + esc(s === 'index' ? '' : s) + '</b>';
    return s === SLUG ? name : '<a href="' + pageUrl(s, '?e=1') + '">' + name + '</a>';
  }
  // rozdíl po slovech ze serveru (publish.word_ops): [[op, text]], op = - + nebo =
  function opsHtml(ops) {
    return (ops || []).map(function (o) {
      if (o[0] === '=' || !o[1].trim()) return esc(o[1]);
      return o[0] === '-' ? '<del>' + esc(o[1]) + '</del>' : '<ins>' + esc(o[1]) + '</ins>';
    }).join('');
  }
  function undoBtn(id, slug) {
    return '<button class="k-btn k-btn--secondary k-btn--sm" type="button" data-undo="' + esc(id) + '" data-slug="' + esc(slug) + '">' + kic('rotate-ccw') + 'Vrátit</button>';
  }
  function slugLink(s) { return '<a href="' + pageUrl(s, '?e=1') + '">/' + esc(s === 'index' ? '' : s) + '</a>'; }
  function swap(before, after) { return '<del>' + esc(before) + '</del> ' + (after ? '<ins>' + esc(after) + '</ins>' : '<i>(smazat)</i>'); }

  // Najít a nahradit i sdílená změna menu a patičky jsou jedna úprava přes mnoho souborů: vlastní karta
  // s Vrátit a výčtem stránek (sdílená změna nese popisek se souborem šablony)
  function partLabel(x) { return x.label && x.label !== 'Najít a nahradit' ? x.label : ''; }
  function replaceCard(x) {
    return '<div class="ke-change"><div class="ke-meta"><span>' + (partLabel(x) ? '<b>' + esc(partLabel(x)) + '</b> · ' : '')
      + plural(x.pages.length, 'stránka', 'stránky', 'stránek') + ' · ' + hhmm(x.t) + '</span>'
      + undoBtn(x.id, x.pages.indexOf(SLUG) >= 0 ? SLUG : '') + '</div>'
      + '<div class="ke-diff">' + swap(x.before, x.after) + '</div>'
      + '<p class="ke-hint">' + x.pages.slice(0, 12).map(slugLink).join(', ') + (x.pages.length > 12 ? ' a ' + plural(x.pages.length - 12, 'další', 'další', 'dalších') : '') + '</p></div>';
  }

  function pageChanges(p) {
    var html = '<section class="ke-page"><div class="ke-page-head">' + pageLink(p.slug)
      + '<span>' + (p.count ? plural(p.count, 'úprava', 'úpravy', 'úprav') : 'jen sdílený text') + '</span></div>';
    if (p.via.length) html += '<p class="ke-hint">Mění ji i úprava sdíleného textu na ' + p.via.map(pageLink).join(', ') + '.</p>';
    if (p['new']) html += '<p class="ke-hint">Stránka v poslední publikaci ještě není, celý text je nový.</p>';
    if (p.replace && p.replace.length) html += '<p class="ke-hint">Mění ji i Najít a nahradit ' + p.replace.map(function (x) {
      return '„' + esc(x.before) + '“ → „' + esc(x.after) + '“';
    }).join(', ') + '. Vrátit ho jde nahoře, Zahodit stránku až potom.</p>';
    html += p.items.map(function (it) {
      var what = it.kind === 'seo' ? 'SEO' : it.kind === 'other' ? 'Mimo texty' : it.tag;
      var meta = esc(what) + (it.kind === 'block' && it.note ? ' · ' + esc(it.note) : '')
        + (it.edits > 1 ? ' · ' + plural(it.edits, 'úprava', 'úpravy', 'úprav') : '') + (it.t ? ' · ' + hhmm(it.t) : '');
      return '<div class="ke-change"><div class="ke-meta"><span>' + meta + '</span>' + (it.undo ? undoBtn(it.undo, p.slug) : '') + '</div>'
        + it.diffs.map(function (x) {
          return '<div class="ke-diff">' + (x.label ? '<span class="ke-lab">' + esc(x.label) + '</span>' : '') + opsHtml(x.ops) + '</div>';
        }).join('')
        + (it.kind === 'other' ? '<div class="ke-hint">' + esc(it.note) + '</div>' : '') + '</div>';
    }).join('');
    if (p.edits.length && p.items.length) html += '<p class="ke-h3">Další úpravy stránky</p>';
    html += p.edits.map(function (c) {
      var tag = String(c.label || '').split(' · ').slice(1).join(' · ');
      return '<div class="ke-change"><div class="ke-meta"><span>' + esc(tag) + ' · ' + hhmm(c.t) + '</span>' + undoBtn(c.id, p.slug) + '</div>'
        + (c.before || c.after ? '<div class="ke-diff">' + wdiff(c.before, c.after) + '</div>' : '<div class="ke-hint">Úprava celého zdroje stránky.</div>')
        + '</div>';
    }).join('');
    return html + '<div><button class="k-btn k-btn--ghost k-btn--sm" type="button" data-discard="' + esc(p.slug) + '">' + kic('trash-2') + 'Zahodit všechny změny stránky</button></div></section>';
  }

  function renderChanges() {
    var b = drawer('changes', 'Změny proti poslední publikaci');
    b.innerHTML = '<div class="ke-empty">Načítám…</div>';
    api('GET', '/api/changes').then(function (r) {
      if (openDrawer !== 'changes') return;
      var d = r.body, pages = d.pages || [];
      setCount(d.count);
      if (!r.ok) { b.innerHTML = '<div class="ke-empty">' + esc(d.error || 'Změny se nenačetly.') + '</div>'; return; }
      var reps = d.replaces || [];
      if (!pages.length && !reps.length) { b.innerHTML = '<div class="ke-empty">Zatím žádné změny.<br>Klikni na text na stránce a přepiš ho.</div>'; return; }
      pages.sort(function (x, y) { return (y.slug === SLUG) - (x.slug === SLUG); });
      b.innerHTML = '<p class="ke-hint">' + plural(d.count, 'úprava', 'úpravy', 'úprav') + ' ve ' + (d.files.length === 1 ? 'zdroji ' : 'zdrojích ')
        + d.files.slice(0, 6).map(esc).join(', ') + (d.files.length > 6 ? ' a ' + plural(d.files.length - 6, 'dalším', 'dalších', 'dalších') : '')
        + '. Vrátit jde vždy nejnovější úpravu souboru, Zahodit vrátí stránku do stavu poslední publikace.</p>'
        + (d.error ? '<p class="ke-warn">' + esc(d.error) + '</p>' : '')
        + (reps.length ? '<section class="ke-page"><p class="ke-h3">Najít a nahradit, menu a patička</p>' + reps.map(replaceCard).join('') + '</section>' : '')
        + pages.map(pageChanges).join('');
    }, offline);
    b.onclick = function (e) {
      var z = e.target.closest('[data-discard]');
      if (z) { discardPage(z.dataset.discard, null, z); return; }
      var u = e.target.closest('[data-undo]');
      if (!u) return;
      u.disabled = true;
      api('POST', '/api/undo', {id: u.dataset.undo}).then(function (r) {
        if (!r.ok) { u.disabled = false; return toast(r.body.error, 'err'); }
        setCount(r.body.count);
        if (u.dataset.slug === SLUG && MODE !== 'list' && !cur) {
          sess('ke-after', JSON.stringify({toast: 'Vráceno.', drawer: 'changes'}));
          location.reload();
          return;
        }
        toast(u.dataset.slug === SLUG ? 'Vráceno. Stránka se načte znovu po uložení rozepsaného textu.' : 'Vráceno.', 'ok');
        if (u.dataset.slug === SLUG) needReload = true;
        renderChanges();
      }, function () { u.disabled = false; offline(); });
    };
  }

  // Zahodit: první požadavek vrátí potvrzení s tokenem (co se vrátí, dotčené stránky), druhý zapisuje.
  function discardPage(slug, token, btn) {
    if (cur) { toast('Nejdřív ulož (Ctrl+Enter), nebo zruš (Esc) rozepsaný text.', 'info'); return; }
    if (btn) btn.disabled = true;
    api('POST', '/api/discard', {slug: slug, confirm: token}).then(function (r) {
      if (btn) btn.disabled = false;
      var d = r.body;
      if (r.ok && d.confirm) {
        ask({title: d.message, body: listHtml(d.details), yes: 'Zahodit', yesIcon: 'trash-2', no: 'Nechat',
          done: function (yes) { if (yes) discardPage(slug, d.confirm, null); }});
        return;
      }
      if (r.ok && d.ok) {
        setCount(d.count);
        var msg = 'Zahozeno: ' + plural(d.discarded, 'úprava', 'úpravy', 'úprav')
          + (d.pages.length > 1 ? ' na ' + plural(d.pages.length, 'stránce', 'stránkách', 'stránkách') : '') + '.';
        if (MODE === 'list') { reloadWith(msg); return; }
        if (MODE === 'edit' && d.pages.indexOf(SLUG) >= 0) {
          sess('ke-after', JSON.stringify({toast: msg, drawer: 'changes'}));
          location.reload();
          return;
        }
        toast(msg, 'ok');
        if (openDrawer === 'changes') renderChanges();
        return;
      }
      if (d.details && d.details.length) { failDialog(d); return; }
      toast(d.error || 'Zahození selhalo.', 'err');
      if (openDrawer === 'changes') renderChanges();
    }, function () { if (btn) btn.disabled = false; offline(); });
  }
  if ($('#keChanges')) $('#keChanges').onclick = function () { if (openDrawer === 'changes') closeDrawer(); else renderChanges(); };

  /* ── Najít a nahradit: cms/replace.py ── */
  var found = null, findT = 0, findSeq = 0;    // poslední výsledek hledání (sig pro nahrazení)
  var WJ = String.fromCharCode(0x2060);
  function normQ(q) { return String(q || '').split(WJ).join('').replace(/\s+/g, ' '); }
  function slugsText(list) { return list.map(slugText).join(', '); }
  function hitsHtml(d) {
    var files = d.files || [], sk = d.skipped || {}, skip = [];
    if (sk.locked) skip.push(sk.locked + '× v oblastech, které plní build z dat webu');
    if (sk.code) skip.push(sk.code + '× v kódu šablony (třídy, odkazy, skripty)');
    if (sk.unused) skip.push(sk.unused + '× v textu, který se na web nedostane');
    var html = '<p class="ke-h3">' + (files.length ? plural(d.total, 'výskyt', 'výskyty', 'výskytů') + ', ' + plural(files.length, 'soubor', 'soubory', 'souborů') : 'Nic nenalezeno') + '</p>';
    html += files.length ? '<div class="ke-hits">' + files.map(function (f) {
      var meta = [];
      if (f.kind === 'script' || f.kind === 'partial') meta.push('→ ' + plural(f.pages.length, 'stránka', 'stránky', 'stránek'));
      if (f.where.join() !== 'text') meta.push(f.where.join(', '));
      return '<label class="ke-hit' + (f.lock ? ' ke-locked' : '') + '"><input class="k-check" type="checkbox" data-path="' + esc(f.path) + '"' + (f.lock ? ' disabled' : ' checked') + '>'
        + '<span class="ke-hit-main"><span class="k-mono">' + esc(f.path) + '</span>'
        + (f.sample ? '<span class="ke-hit-sub">' + esc(f.sample[0]) + '<b>' + esc(f.sample[1]) + '</b>' + esc(f.sample[2]) + '</span>' : '')
        + (meta.length ? '<span class="ke-hit-sub">' + esc(meta.join(' · ')) + '</span>' : '')
        + (f.owners && f.owners.length ? '<span class="ke-hit-sub">Mění i owner stránky ' + esc(slugsText(f.owners)) + ', potvrdíš je při nahrazení.</span>' : '')
        + (f.lock ? '<span class="ke-hit-sub ke-hit-lock">' + esc(f.lock)
          + (f.slug && ROLE === 'admin' ? ' <button class="k-link" type="button" data-unlock="' + esc(f.slug) + '">Odemknout na 30 minut</button>' : '') + '</span>' : '')
        + '</span><span class="ke-n">' + f.n + '×</span></label>';
    }).join('') + '</div>' : '';
    if (skip.length) html += '<p class="ke-hint">Nemění se: ' + esc(skip.join(', ')) + '.</p>';
    if (d.error) html += '<p class="ke-warn">' + esc(d.error) + '</p>';
    if (files.length) html += '<div><button class="k-btn k-btn--primary" type="button" id="keRun">Ukázat výsledek a nahradit…</button></div>';
    return html;
  }
  function syncRun() {
    var btn = sheetEl && $('#keRun', sheetEl);
    if (btn) btn.disabled = !$$('.ke-hit input:checked', sheetEl).length;
  }
  function runFind() {
    if (openDrawer !== 'find') return;
    var q = $('#keQ', sheetEl).value, out = $('#keFound', sheetEl), seq = ++findSeq;
    clearTimeout(findT);
    if (normQ(q).trim().length < 2) { found = null; out.innerHTML = ''; return; }
    out.classList.add('ke-busy');
    if (!found) out.innerHTML = '<p class="ke-h3">Hledám…</p>';
    api('GET', '/api/find?q=' + encodeURIComponent(q)).then(function (r) {
      if (seq !== findSeq || openDrawer !== 'find') return;
      out.classList.remove('ke-busy');
      if (!r.ok) { found = null; out.innerHTML = '<p class="ke-warn">' + esc(r.body.error || 'Hledání selhalo.') + '</p>'; return; }
      found = r.body;
      out.innerHTML = hitsHtml(found);
      syncRun();
    }, offline);
  }
  function renderFind() {
    var b = drawer('find', 'Najít a nahradit napříč webem');
    found = null;
    b.innerHTML = '<div class="k-field"><label class="k-field__label" for="keQ">Najít</label><input class="k-input" id="keQ" autocomplete="off" spellcheck="false" value="' + esc(sess('ke-find-q') || '') + '"></div>'
      + '<div class="k-field"><label class="k-field__label" for="keR">Nahradit za</label><input class="k-input" id="keR" autocomplete="off" value="' + esc(sess('ke-find-r') || '') + '"></div>'
      + '<p class="ke-hint">Hledá ve zdrojích včetně menu, patičky a formulářů. Mezera najde i pevnou mezeru. Oblasti, které plní build z dat webu, se mění v datech.</p>'
      + '<div id="keFound"></div>';
    var qi = $('#keQ', b), ri = $('#keR', b);
    qi.focus();
    qi.select();
    qi.oninput = function () { sess('ke-find-q', qi.value); clearTimeout(findT); findT = setTimeout(runFind, 600); };
    ri.oninput = function () { sess('ke-find-r', ri.value); };
    qi.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); runFind(); } };
    ri.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); runReplace(null); } };
    b.onchange = function (e) { if (e.target.closest('.ke-hit')) syncRun(); };
    b.onclick = function (e) {
      var u = e.target.closest('[data-unlock]');
      if (u) {
        e.preventDefault();
        api('POST', '/api/unlock', {slug: u.dataset.unlock}).then(function (r) {
          if (!r.ok) return toast(r.body.error, 'err');
          toast(slugText(u.dataset.unlock) + ' je odemčená do ' + r.body.until + ', jen pro tebe.', 'ok');
          runFind();
        }, offline);
        return;
      }
      if (e.target.closest('#keRun')) runReplace(null);
    };
    runFind();
  }
  // První požadavek vrátí shrnutí zkušebního buildu s tokenem, druhý (po potvrzení) zapisuje;
  // owners = v dialogu zaškrtnuté, že sdílená část mění i owner stránky.
  function runReplace(token, owners) {
    var btn = sheetEl && $('#keRun', sheetEl);
    if (openDrawer !== 'find' || !found || !btn || btn.disabled) return;
    if (cur) { toast('Nejdřív ulož (Ctrl+Enter), nebo zruš (Esc) rozepsaný text.', 'info'); return; }
    if (normQ($('#keQ', sheetEl).value) !== found.query) { runFind(); return; }
    var files = $$('.ke-hit input:checked', sheetEl).map(function (i) { return i.dataset.path; });
    var label = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = token ? 'Nahrazuji…' : 'Zkušební build…';
    api('POST', '/api/replace', {q: found.query, r: $('#keR', sheetEl).value, files: files, sig: found.sig, confirm: token, owners: !!owners}).then(function (r) {
      btn.disabled = false;
      btn.innerHTML = label;
      var d = r.body;
      if (r.ok && d.confirm) { confirmReplace(d); return; }
      if (r.ok && d.unchanged) { toast('Nahrazení nic nemění, nic jsem nezapsal.', 'info'); return; }
      if (r.ok && d.ok) {
        setCount(d.count);
        sess('ke-find-q', null);
        sess('ke-find-r', null);
        var msg = 'Nahrazeno: ' + plural(d.hits, 'výskyt', 'výskyty', 'výskytů') + ' na ' + plural(d.pages.length, 'stránce', 'stránkách', 'stránkách')
          + '. V nepublikovaných úpravách je to jedna úprava.';
        if (MODE === 'list') { reloadWith(msg); return; }
        if (MODE === 'edit' && d.pages.indexOf(SLUG) >= 0) {
          sess('ke-after', JSON.stringify({toast: msg, drawer: 'changes'}));
          location.reload();
          return;
        }
        toast(msg, 'ok');
        renderChanges();
        return;
      }
      if (d.code === 'stale') { toast(d.error, 'err'); runFind(); return; }
      if (d.details && d.details.length) { failDialog(d); return; }
      toast(d.error || 'Nahrazení selhalo.', 'err');
    }, function () { btn.disabled = false; btn.innerHTML = label; offline(); });
  }
  function confirmReplace(d) {
    var body = '<p>Zkušební build ukázal změnu na <b>' + plural(d.pages.length, 'stránce', 'stránkách', 'stránkách') + '</b>'
      + (d.generated ? ' (generované stránky mají text ze sdíleného zdroje)' : '') + '. Build a verify prošly.</p>'
      + '<div class="ke-changes"><div class="ke-diff">' + swap(d.query, d.replacement) + '</div>'
      + (d.examples || []).map(function (x) {
        return '<div class="ke-diff"><span class="ke-diff__where">' + esc(slugText(x.page)) + ' · ' + esc(x.label) + '</span>' + opsHtml(x.ops) + '</div>';
      }).join('') + '</div>'
      + listHtml(d.details) + ownersHtml(d.owners);
    var dlg = ask({title: d.message, body: body, yes: 'Nahradit', yesIcon: 'check', cls: 'k-dialog--lg',
      done: function (yes) { if (yes) runReplace(d.confirm, !!(d.owners && d.owners.length)); }});
    ownersGate(dlg);
  }
  // Owner stránky v potvrzení sdílené změny (ADR 0003 dodatek): bez zaškrtnutí nejde uložit.
  // shared = co mají owner stránky stejné jako ostatní (výchozí menu a patička)
  function ownersHtml(owners, shared) {
    if (!owners || !owners.length) return '';
    return '<div class="ke-owner" role="group" aria-labelledby="keOwT"><div class="ke-owner__head">' + kic('lock')
      + '<span id="keOwT"><b>Mění i owner stránky:</b> ' + owners.map(function (s) {
        var p = PAGES[s];
        return esc(p && p.name ? p.name + ' (' + slugText(s) + ')' : slugText(s));
      }).join(' a ') + '. Jejich vlastní texty jsou zamčené (ADR 0003), ' + (shared || 'sdílené menu a patičku mají stejné') + ' jako ostatní stránky.</span></div>'
      + '<label><input class="k-check" type="checkbox" data-own-ok>Změnit i owner stránky</label></div>';
  }
  function ownersGate(dlg) {
    var cb = $('[data-own-ok]', dlg), yes = $('[data-a="yes"]', dlg);
    if (!cb || !yes) return;
    yes.disabled = true;
    cb.focus();
    cb.addEventListener('change', function () { yes.disabled = !cb.checked; });
  }
  if ($('#keFind')) $('#keFind').onclick = function () { if (openDrawer === 'find') closeDrawer(); else renderFind(); };

  /* ── panel SEO (B12, prototyp prototype/cms-b12, varianta A): oddíly Vyhledávání, Sdílení, Strukturovaná
     data, O vás a FAQ. Hlavička, texty JSON-LD a FAQ jsou texty stránky (cms/seo.py); O vás jsou údaje
     z dat webu (adapter Rules.entity), společné pro všechny stránky s blokem (dialog se seznamem stránek
     jako u menu), nebo jen téhle stránky. Seznamy (klíčová slova, další názvy, pozice, témata) po řádcích. ── */
  var seo = null, seoF = {}, seoIds = [], seoTab = 'hledani', seoBusy = false;
  var SEO_SEC = [{id: 'hledani', t: 'Vyhledávání'}, {id: 'sdileni', t: 'Sdílení'}, {id: 'ld', t: 'Strukturovaná data'},
    {id: 'osoba', t: 'O vás'}, {id: 'faq', t: 'FAQ'}];
  var SEO_IC = {Service: 'package', Article: 'file-text', CollectionPage: 'layout-grid', BreadcrumbList: 'chevron-right', ItemList: 'list-checks'};
  var SEO_HEAD = {title: '<title>', description: 'meta description', keywords: 'meta keywords', robots: 'meta robots'};
  function squash(s) { return String(s || '').replace(/\s+/g, ' ').trim(); }
  function seoClone(v) { return Array.isArray(v) ? v.slice() : v; }
  function seoSame(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  function seoModel(d) {
    seoF = {};
    seoIds = [];
    var add = function (f, sec, extra) {
      var o = {id: f.id, label: f.label, key: f.key || SEO_HEAD[f.id] || 'meta ' + f.id, value: seoClone(f.value), val: seoClone(f.value),
        list: !!f.list, state: f.state || (f.edit ? 'edit' : 'locked'), why: f.why, max: f.max, sep: f.sep, sec: sec};
      Object.keys(extra || {}).forEach(function (k) { o[k] = extra[k]; });
      seoF[o.id] = o;
      seoIds.push(o.id);
    };
    d.fields.forEach(function (f) { add(f, /^(og|twitter):/.test(f.id) ? 'sdileni' : 'hledani'); });
    d.ld.forEach(function (o, oi) { o.rows.forEach(function (r) { add(r, 'ld', {obj: oi}); }); });
    var P = d.person;
    if (P) {
      P.fields.forEach(function (f) { add(f, 'osoba', {scope: f.scope, entity: true}); });
      if (P.topics) {
        var t = P.topics;
        add(t, 'osoba', {scope: 'knows', entity: true, own: t.own, ownVal: t.own, dflt: t['default'], pageWhy: t.page_why, sharedWhy: t.shared_why});
      }
    }
    d.faq.forEach(function (q) {
      add({id: q.q.id, label: 'Otázka ' + (q.i + 1), key: 'Question.name', value: q.q.value, state: q.q.state, why: q.q.why}, 'faq', {faq: q.i});
      add({id: q.a.id, label: 'Odpověď', key: 'Answer.text', value: q.a.value, state: q.a.state, why: q.a.why}, 'faq', {faq: q.i});
    });
  }
  function seoAll() { return seoIds.map(function (id) { return seoF[id]; }); }
  function seoEditable(f) {
    if (f.id === 'knows') return !seoKnowsWhy(f);
    return f.state === 'edit' || f.state === 'shared';
  }
  // proč nejdou témata měnit: údaje na stránce nesedí s daty, vlastní = zámek stránky, výchozí = role
  function seoKnowsWhy(f) { return (seo.person && seo.person.drift) || (f.ownVal ? f.pageWhy : f.sharedWhy) || null; }
  function seoChanged(f) { return !seoSame(f.val, f.value) || (f.id === 'knows' && f.ownVal !== f.own); }
  function seoPending() { return seoAll().filter(function (f) { return seoEditable(f) && seoChanged(f); }); }
  // sdílená změna: společné údaje o vás (všechny stránky s blokem), výchozí témata (stránky, které je mají)
  function seoShared(f) { return f.scope === 'site' || (f.id === 'knows' && !f.ownVal && !seoSame(f.val, f.dflt)); }
  function seoShow(v) { return Array.isArray(v) ? v.join(', ') : v; }
  function seoUid(f) { return 'keS' + f.id.replace(/[^\w-]/g, '_'); }

  function seoCount(f) {
    if (f.max) return '<span class="ke-cnt' + (f.val.length > f.max ? ' over' : '') + '" data-cnt>' + f.val.length + ' / ' + f.max + '</span>';
    if (f.list) return '<span class="ke-cnt" data-cnt>' + plural(f.val.length, 'položka', 'položky', 'položek') + '</span>';
    return '';
  }
  function seoKnowsNote(f) {
    return f.ownVal ? kic('file-text', 12) + '<span>Jen na téhle stránce (' + esc(seo.person.file) + ', přepis pro ' + esc(slugText(SLUG)) + ').</span>'
      : kic('layers', 12) + '<span>Výchozí témata webu: změna se propíše na ' + plural(seo.person.default_pages.length, 'stránku', 'stránky', 'stránek') + ', které je mají.</span>';
  }
  function seoNote(f) {
    if (f.id === 'knows') {
      var kw = seoKnowsWhy(f);
      return kw ? '<div class="ke-xt-note">' + kic('lock', 12) + '<span>' + esc(kw) + '</span></div>' : '<div class="ke-xt-note" data-knote>' + seoKnowsNote(f) + '</div>';
    }
    if (f.state === 'locked' || f.state === 'ro') return '<div class="ke-xt-note">' + kic('lock', 12) + '<span>' + esc(f.why || '') + '</span></div>';
    if (f.state === 'computed') return '<div class="ke-xt-note">' + kic('blocks', 12) + '<span>' + esc(f.why || '') + '</span></div>';
    if (f.scope === 'site') return '<div class="ke-xt-note">' + kic('layers', 12) + '<span>Společné pro ' + plural(seo.person.pages.length, 'stránku', 'stránky', 'stránek') + ' (' + esc(seo.person.file) + ').</span></div>';
    if (f.scope === 'page' && f.entity) return '<div class="ke-xt-note">' + kic('file-text', 12) + '<span>Jen na téhle stránce (' + esc(seo.person.file) + ', přepis pro ' + esc(slugText(SLUG)) + ').</span></div>';
    return '';
  }
  // o.why === false: důvod stojí nad skupinou (sbalené Zamčené, Vyplní build); o.key === false: bez typu a klíče
  function seoRow(f, o) {
    o = o || {};
    var ro = !seoEditable(f), ch = seoChanged(f);
    return '<div class="ke-seo-row' + (ch ? ' ke-seo-row--changed' : '') + '" data-row="' + esc(f.id) + '">'
      + '<div class="ke-seo-lab">' + (ro && o.why === false ? kic('lock', 12) : '') + '<label for="' + seoUid(f) + '">' + esc(f.label) + '</label>'
      + (o.key === false ? '' : '<span class="k-mono ke-seo-key">' + esc(f.key) + '</span>') + '<span class="ke-seo-sp"></span>' + seoCount(f) + '</div>'
      + '<textarea class="k-input ke-seo-field" id="' + seoUid(f) + '" data-id="' + esc(f.id) + '" rows="1"' + (ro ? ' readonly' : '') + (f.list ? ' data-list' : '') + '>'
      + esc(f.list ? f.val.join('\n') : f.val) + '</textarea>'
      + (o.why === false ? '' : seoNote(f))
      + (f.list && !ro ? '<p class="ke-seo-hint">Jedna položka na řádek' + (f.sep ? ', na stránku se zapíšou oddělené čárkou' : '') + '.</p>' : '')
      + '<div class="ke-xt-was"' + (ch ? '' : ' hidden') + '><span>Původně: ' + (f.id === 'knows' && f.own !== f.ownVal ? (f.own ? 'vlastní témata, ' : 'výchozí témata, ') : '')
      + '„' + esc(seoShow(f.value)) + '“</span><button class="k-link" type="button" data-undo-f="' + esc(f.id) + '">Vrátit</button></div></div>';
  }
  function seoNotice(icon, html) { return '<div class="k-notice">' + kic(icon) + '<div class="k-notice__body"><span>' + html + '</span></div></div>'; }
  function seoByWhy(list, title, icon, open) {
    if (!list.length) return '';
    var groups = {}, whys = [];
    list.forEach(function (f) { if (!groups[f.why]) { groups[f.why] = []; whys.push(f.why); } groups[f.why].push(f); });
    return '<details class="ke-xt-locked"' + (open ? ' open' : '') + '><summary>' + kic(icon, 14) + esc(title) + ' <span class="ke-cnt">' + list.length + '</span>' + kic('chevron-down', 14) + '</summary>'
      + whys.map(function (w) { return '<p class="ke-xt-why">' + esc(w) + '</p>' + groups[w].map(function (f) { return seoRow(f, {why: false}); }).join(''); }).join('') + '</details>';
  }
  function seoSec(s) { return seoAll().filter(function (f) { return f.sec === s; }); }
  function seoAvail(s) {
    if (s === 'sdileni') return seoSec(s).length > 0;
    if (s === 'osoba') return !!seo.person;
    if (s === 'faq') return seo.faq.length > 0;
    if (s === 'ld') return seo.ld.length > 0;
    return true;
  }
  function seoSecCount(s) { return s === 'faq' ? seo.faq.length : seoSec(s).filter(seoEditable).length; }
  function seoHead(ids) {
    return ids.map(function (id) {
      if (id === 'keywords' && !seoF.keywords) return '<div class="ke-seo-row"><div class="ke-seo-lab"><label>Klíčová slova</label><span class="k-mono ke-seo-key">meta keywords</span></div>'
        + '<p class="ke-seo-hint">Stránka meta keywords nemá. Přidá je Claude Code se stránkou.</p></div>';
      return seoF[id] ? seoRow(seoF[id]) : '';
    }).join('');
  }
  function seoSecHtml(s) {
    var h = seo.lock ? seoNotice('lock', esc(seo.lock)) : '';
    if (s === 'hledani') {
      h += seoHead(['title', 'description', 'keywords', 'robots']);
      if (seo.derived) h += '<p class="ke-hint">Generovaná stránka: description se použije i pro og:description a twitter:description, title bez koncového „| …“ pro og:title a twitter:title.</p>';
    } else if (s === 'sdileni') {
      h += '<p class="ke-hint">Náhled při sdílení odkazu (Facebook, LinkedIn, X). Na ruční stránce mají vlastní znění. Sjednotit je vyplní z title (bez názvu webu na konci) a z description, zapíše je až Uložit.</p>'
        + seoSec('sdileni').map(function (f) { return seoRow(f); }).join('')
        + '<div><button class="k-btn k-btn--secondary k-btn--sm" type="button" data-sync' + (seo.lock ? ' disabled' : '') + '>Sjednotit s title a description</button></div>';
    } else if (s === 'ld') {
      h += '<p class="ke-hint">Texty ze strukturovaných dat (JSON-LD), které čtou vyhledávače a AI asistenti. Na stránce vidět nejsou. Otázky a odpovědi jsou v oddílu FAQ, údaje o vás v O vás.</p>'
        + seo.ld.map(seoObjHtml).join('');
    } else if (s === 'osoba') {
      h += seoPersonHtml();
    } else if (s === 'faq') {
      h += seoFaqHtml();
    }
    return '<div class="ke-seo-sec" data-sec="' + s + '">' + h + '</div>';
  }
  function seoObjHtml(o, oi) {
    var fs = seoSec('ld').filter(function (f) { return f.obj === oi; });
    var open = fs.filter(seoEditable), comp = fs.filter(function (f) { return f.state === 'computed'; }),
      lock = fs.filter(function (f) { return f.state === 'locked'; });
    return '<section class="ke-seo-group" data-obj="' + oi + '"><div class="ke-seo-group__head"><p class="ke-h3">' + kic(SEO_IC[o.type] || 'file-code', 14) + esc(o.label)
      + ' <span class="k-mono ke-seo-key">' + esc(o.type) + '</span></p></div>'
      + open.map(function (f) { return seoRow(f); }).join('')
      + seoByWhy(comp, 'Vyplní build', 'blocks', !open.length)
      + seoByWhy(lock, 'Zamčené', 'lock', !open.length && !comp.length) + '</section>';
  }
  function seoPersonHtml() {
    var P = seo.person, k = seoF.knows;
    if (!P) return '<div class="ke-empty">Stránka nemá údaje o vás.</div>';
    var h = P.drift ? seoNotice('triangle-alert', esc(P.drift))
      : seoNotice('layers', '<b>Údaje o vás jsou společné pro ' + plural(P.pages.length, 'stránku', 'stránky', 'stránek') + '</b> (' + esc(P.file)
        + '): strukturovaná data, podle kterých vyhledávače a AI poznají autora webu. Uložení potvrdíš v dialogu se seznamem stránek jako u menu a patičky.');
    h += '<section class="ke-seo-group">' + seoSec('osoba').filter(function (f) { return f.id !== 'knows'; }).map(function (f) { return seoRow(f); }).join('')
      + '<details class="ke-xt-locked"><summary>' + kic('lock', 14) + 'Kontakty a odkazy <span class="ke-cnt">' + P.contacts.length + '</span>' + kic('chevron-down', 14) + '</summary>'
      + '<p class="ke-xt-why">Nejsou to texty: mění je Claude Code v ' + esc(P.file) + '.</p>'
      + '<dl class="k-dl ke-seo-dl">' + P.contacts.map(function (c) { return '<dt>' + esc(c.label) + '</dt><dd>' + c.value.map(esc).join('<br>') + '</dd>'; }).join('') + '</dl></details></section>';
    if (k) {
      h += '<section class="ke-seo-group"><div class="ke-seo-group__head"><p class="ke-h3">' + kic('user-round', 14) + 'Témata na téhle stránce <span class="k-mono ke-seo-key">' + esc(k.key) + '</span></p></div>'
        + '<p class="ke-hint">Čemu rozumíte. Podle témat vyhledávače a AI spojí stránku s autorem, proto se na stránkách liší.</p>'
        + seoKnowsChoice(k) + seoRow(k, {key: false}) + '</section>';
    }
    return h;
  }
  function seoKnowsChoice(k) {
    var dis = k.pageWhy || seo.person.drift ? ' disabled' : '', P = seo.person;
    return '<div class="ke-seo-choices" role="radiogroup" aria-label="Témata na téhle stránce">'
      + '<label class="k-choice"><input class="k-radio" type="radio" name="keSknows" value="def"' + (k.ownVal ? '' : ' checked') + dis + '><span>Výchozí témata webu'
      + '<span class="k-choice__hint">Stejná na ' + plural(P.default_pages.length, 'stránce', 'stránkách', 'stránkách') + '. Úprava se propíše na všechny.</span></span></label>'
      + '<label class="k-choice"><input class="k-radio" type="radio" name="keSknows" value="own"' + (k.ownVal ? ' checked' : '') + dis + '><span>Vlastní pro tuhle stránku'
      + '<span class="k-choice__hint">Platí jen tady. Dnes je mají ' + plural(P.pages.length - P.default_pages.length, 'stránka', 'stránky', 'stránek') + '.</span></span></label></div>';
  }
  function seoFaqHtml() {
    var h = '<p class="ke-hint">' + (seo.kind === 'generated' ? 'Na generované stránce má JSON-LD vlastní otázky i odpovědi, úprava FAQ na stránce je nemění.'
      : 'Když otázku upravíš na stránce a tady zní stejně, propíše se sem sama.')
      + ' <b>Odpověď je v JSON-LD samostatný text:</b> úprava odpovědi na stránce ji nezmění a naopak. Ukládá se prostý text bez formátování a pevných mezer.</p>'
      + (seo.faq_note ? '<p class="ke-warn">' + esc(seo.faq_note) + '</p>' : '');
    return h + seo.faq.map(function (q) {
      var fq = seoF[q.q.id], fa = seoF[q.a.id];
      var take = seoEditable(fq) && seoEditable(fa) && q.vis_a != null;
      return '<div class="ke-faq" data-faq="' + q.i + '">' + seoRow(fq, {key: false}) + seoRow(fa, {key: false})
        + (q.vis_a != null ? '<div class="ke-vis"><b>Na stránce:</b> ' + (q.vis_q !== fq.val ? esc(q.vis_q) + ' · ' : '') + esc(q.vis_a) + '</div>' : '')
        + '<div class="ke-row"' + (seoFaqDiff(q) ? '' : ' hidden') + '><span class="ke-diffnote">Liší se od FAQ na stránce.</span>'
        + (take ? '<button class="k-btn k-btn--secondary k-btn--sm" type="button" data-take="' + q.i + '">Převzít z viditelné</button>' : '') + '</div></div>';
    }).join('');
  }
  function seoFaqDiff(q) { return q.vis_a != null && (squash(seoF[q.q.id].val) !== q.vis_q || squash(seoF[q.a.id].val) !== q.vis_a); }

  function seoPanelEl() { return openDrawer === 'seo' && sheetEl ? sheetEl : null; }
  function seoGrow(t) { t.style.height = 'auto'; t.style.height = t.scrollHeight + 2 + 'px'; }
  function seoRender(o) {
    o = o || {};
    var a = seoPanelEl();
    if (!a) return;
    var body = $('.k-drawer__body', a), top = o.keep ? body.scrollTop : 0;
    if (!seoAvail(seoTab)) seoTab = 'hledani';
    var tabs = $('.ke-seo-tabs', a);
    if (!tabs) {
      tabs = document.createElement('div');
      tabs.className = 'k-tabs ke-seo-tabs';
      tabs.setAttribute('role', 'tablist');
      tabs.setAttribute('aria-label', 'Oddíly panelu SEO');
      a.insertBefore(tabs, body);
    }
    tabs.innerHTML = SEO_SEC.filter(function (s) { return seoAvail(s.id); }).map(function (s) {
      return '<button class="k-tab" type="button" role="tab" aria-selected="' + (s.id === seoTab) + '" data-tab="' + s.id + '">' + esc(s.t)
        + ' <span class="k-count">' + seoSecCount(s.id) + '</span></button>';
    }).join('');
    body.innerHTML = seoSecHtml(seoTab);
    var foot = $('.ke-seo-foot', a);
    if (!foot) {
      foot = document.createElement('div');
      foot.className = 'k-drawer__foot ke-xt-foot ke-seo-foot';
      a.appendChild(foot);
    }
    seoFoot();
    $$('.ke-seo-field', body).forEach(seoGrow);
    body.scrollTop = top;
  }
  function seoFoot() {
    var a = seoPanelEl(), foot = a && $('.ke-seo-foot', a);
    if (!foot) return;
    var p = seoPending(), sh = p.filter(seoShared), where = [];
    p.forEach(function (f) { var s = SEO_SEC.filter(function (x) { return x.id === f.sec; })[0]; if (s && where.indexOf(s.t) < 0) where.push(s.t); });
    foot.innerHTML = '<span class="ke-xt-foot__n">' + (p.length ? plural(p.length, 'změna', 'změny', 'změn') + ' k uložení (' + esc(where.join(', ')) + ')'
      + (sh.length ? ', ' + (sh.some(function (f) { return f.scope === 'site'; }) ? 'údaje o vás společné pro ' : 'výchozí témata společná pro ') + plural(seoSharedPages(sh).length, 'stránku', 'stránky', 'stránek') : '') : 'Bez změn') + '</span>'
      + '<button class="k-btn k-btn--primary" type="button" data-save' + (p.length && !seoBusy ? '' : ' disabled') + '>Uložit <span class="k-kbd">Ctrl+Enter</span></button>';
  }
  function seoSharedPages(sh) {
    return sh.some(function (f) { return f.scope === 'site'; }) ? seo.person.pages : seo.person.default_pages;
  }
  function seoRefresh(f) {
    var a = seoPanelEl();
    if (!a) return;
    $$('[data-row="' + f.id + '"]', a).forEach(function (row) {
      var ch = seoChanged(f);
      row.classList.toggle('ke-seo-row--changed', ch);
      var was = $('.ke-xt-was', row);
      if (was) was.hidden = !ch;
      var c = $('[data-cnt]', row);
      if (c) c.outerHTML = seoCount(f);
    });
    if (f.sec === 'faq') {
      var q = seo.faq[f.faq], card = $('[data-faq="' + f.faq + '"]', a);
      if (card && q.vis_a != null) $('.ke-row', card).hidden = !seoFaqDiff(q);
    }
    seoFoot();
  }

  function renderSeo(o) {
    o = o || {};
    var b = sheetEl && openDrawer === 'seo' && o.keep ? $('.k-drawer__body', sheetEl) : sheet('SEO a strukturovaná data', {name: 'seo', cls: 'ke-sheet k-drawer--md ke-seo-sheet',
      onClose: function () { var t = $('#keSeo'); if (t) t.removeAttribute('aria-pressed'); }});
    var t = $('#keSeo');
    if (t) t.setAttribute('aria-pressed', 'true');
    if (!o.keep) b.innerHTML = '<div class="ke-empty">Načítám…</div>';
    api('GET', '/api/seo?slug=' + encodeURIComponent(SLUG)).then(function (r) {
      if (openDrawer !== 'seo') return;
      if (!r.ok) { b.innerHTML = '<div class="ke-empty">' + esc(r.body.error || 'Panel SEO se nenačetl.') + '</div>'; return; }
      seo = r.body;
      seoModel(seo);
      if (o.tab) seoTab = o.tab;
      seoRender({keep: o.keep});
    }, offline);
  }
  function seoLines(v) { return v.split('\n').map(function (x) { return x.trim(); }).filter(Boolean); }
  document.addEventListener('input', function (e) {
    var a = seoPanelEl(), t = e.target;
    if (!a || !a.contains(t) || !t.classList.contains('ke-seo-field')) return;
    var f = seoF[t.dataset.id];
    if (!f) return;
    f.val = f.list ? seoLines(t.value) : t.value;
    seoGrow(t);
    seoRefresh(f);
  });
  document.addEventListener('change', function (e) {
    var a = seoPanelEl(), t = e.target;
    if (!a || !a.contains(t) || t.name !== 'keSknows') return;
    var k = seoF.knows;
    k.ownVal = t.value === 'own';
    if (!k.ownVal && k.own) k.val = seoClone(k.dflt);
    seoRender({keep: true});
  });
  document.addEventListener('click', function (e) {
    var a = seoPanelEl(), t = e.target;
    if (!a || !a.contains(t)) return;
    var x = t.closest('[data-tab]');
    if (x) { seoTab = x.dataset.tab; seoRender(); return; }
    x = t.closest('[data-undo-f]');
    if (x) {
      var f = seoF[x.dataset.undoF];
      f.val = seoClone(f.value);
      if (f.id === 'knows') f.ownVal = f.own;
      seoRender({keep: true});
      return;
    }
    x = t.closest('[data-take]');
    if (x) {
      var q = seo.faq[+x.dataset.take];
      seoF[q.q.id].val = q.vis_q;
      seoF[q.a.id].val = q.vis_a;
      seoRender({keep: true});
      toast('Převzato. Uložením se zapíše prostým textem bez pevných mezer.', 'info');
      return;
    }
    if (t.closest('[data-sync]')) {
      var title = String(seoF.title ? seoF.title.val : '').replace(SUFFIX, ''), desc = seoF.description ? seoF.description.val : '';
      ['og:title', 'twitter:title'].forEach(function (id) { if (seoF[id] && seoEditable(seoF[id])) seoF[id].val = title; });
      ['og:description', 'twitter:description'].forEach(function (id) { if (seoF[id] && seoEditable(seoF[id])) seoF[id].val = desc; });
      seoRender({keep: true});
      toast('Sjednoceno. Zapíše se až tlačítkem Uložit.', 'info');
      return;
    }
    if (t.closest('[data-save]')) saveSeo();
  });
  // Ctrl+Enter uloží panel SEO i bez fokusu v něm; rozepsaný blok má přednost (jeho Ctrl+Enter už udělal
  // preventDefault). Enter v jednořádkovém poli nic nedělá (text je na jeden řádek).
  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || dlgEl || openDrawer !== 'seo' || e.key !== 'Enter') return;
    var t = e.target, inPanel = sheetEl && sheetEl.contains(t);
    if (e.ctrlKey || e.metaKey) {
      if (cur && !inPanel) return;
      e.preventDefault();
      saveSeo();
      return;
    }
    if (inPanel && t.classList && t.classList.contains('ke-seo-field') && !t.hasAttribute('data-list')) e.preventDefault();
  });

  /* ── uložení: texty stránky (save_seo) a pak údaje o vás (save_entity přes týž endpoint). Sdílená změna
     údajů o vás se nejdřív potvrdí v dialogu se seznamem stránek (náhled bez zápisu), teprve pak se zapisuje. ── */
  function seoChange(f) {
    var c = {id: f.id, old: f.value, 'new': f.val};
    if (f.id === 'knows') c.own = f.ownVal;
    return c;
  }
  function saveSeo() {
    if (!seo || seoBusy || openDrawer !== 'seo') return;
    var p = seoPending();
    if (!p.length) { toast('Beze změny, nic jsem nezapsal.', 'info'); return; }
    var empty = p.filter(function (f) { return f.list ? !f.val.length : !squash(f.val); })[0];
    if (empty) { toast('Pole „' + empty.label + '“ nesmí být prázdné.', 'err'); return; }
    var page = p.filter(function (f) { return !f.entity; }), ent = p.filter(function (f) { return f.entity; });
    var res = {files: [], pages: [], body: false};
    seoBusy = true;
    seoFoot();
    var stop = function () { seoBusy = false; seoFoot(); };
    var done = function () {
      seoBusy = false;
      if (!res.files.length) { toast('Jako prostý text je to beze změny, nic jsem nezapsal.', 'info'); renderSeo({keep: true}); return; }
      var msg = 'Uloženo do ' + res.files.join(', ') + (res.pages.length ? ', přestaveno ' + plural(res.pages.length, 'stránka', 'stránky', 'stránek') : '') + '. Na web půjde s Publikovat.';
      if (res.body && !cur) { sess('ke-after', JSON.stringify({toast: msg, drawer: 'seo'})); location.reload(); return; }
      if (res.body) needReload = true;
      toast(msg, 'ok');
      renderSeo({keep: true});
    };
    var run = function (owners) {
      seoPost(page, null, false, res, function () { seoPost(ent, null, owners, res, done, stop, true); }, stop);
    };
    if (ent.some(seoShared)) {
      api('POST', '/api/seo', {slug: SLUG, changes: ent.map(seoChange)}).then(function (r) {
        var b = r.body;
        if (r.ok && b.confirm) { seoConfirmShared(b, page.length, run, stop); return; }
        stop();
        seoFail(r);
      }, function () { stop(); offline(); });
      return;
    }
    run(false);
  }
  // auto = uživatel už sdílenou změnu potvrdil v dialogu: nový token (plán po uložení textů stránky) se potvrdí sám
  function seoPost(list, token, owners, res, next, stop, auto) {
    if (!list.length) { next(); return; }
    api('POST', '/api/seo', {slug: SLUG, changes: list.map(seoChange), confirm: token, owners: !!owners}).then(function (r) {
      var b = r.body;
      if (r.ok && b.confirm && auto && !token) { seoPost(list, b.confirm, owners, res, next, stop, auto); return; }
      if (r.ok && b.confirm) {
        ask({title: b.message, body: listHtml(b.details), yes: 'Uložit i tak', no: 'Upravit dál',
          done: function (yes) { if (yes) seoPost(list, b.confirm, owners, res, next, stop, auto); else stop(); }});
        return;
      }
      if (r.ok && (b.ok || b.unchanged)) {
        setCount(b.count);
        if (b.page) setMeta('ke-page', b.page);
        if (b.title) document.title = b.title;
        (b.files || []).forEach(function (f) { if (res.files.indexOf(f) < 0) res.files.push(f); });
        (b.pages || []).forEach(function (s) { if (res.pages.indexOf(s) < 0) res.pages.push(s); });
        if (b.body) res.body = true;
        next();
        return;
      }
      stop();
      seoFail(r);
    }, function () { stop(); offline(); });
  }
  function seoFail(r) {
    var b = r.body;
    if (b.code === 'stale') { toast(b.error, 'err'); renderSeo({keep: true}); return; }
    if (b.details && b.details.length) { failDialog(b); return; }
    toast(b.error || 'Uložení selhalo.', 'err');
  }
  function seoConfirmShared(b, rest, run, stop) {
    var n = b.pages.length, by = {handbuilt: [], generated: [], owner: []};
    b.pages.forEach(function (p) { (by[p.kind] || (by[p.kind] = [])).push(p.slug); });
    var li = function (kind) {
      return function (s) {
        var p = PAGES[s];
        return '<li' + (p && p.name ? ' title="' + esc(p.name) + '"' : '') + '>' + esc(slugText(s))
          + (kind === 'owner' ? ' <span>owner</span>' : kind === 'generated' ? ' <span>generovaná</span>' : '') + '</li>';
      };
    };
    var topics = b.changes.some(function (c) { return c.id === 'knows' && c.shared; }) && !b.changes.some(function (c) { return c.shared && c.id !== 'knows'; });
    var body = '<div class="ke-changes">' + b.changes.map(function (c) {
      var lists = Array.isArray(c.old) || Array.isArray(c['new']);
      return '<div class="ke-diff"><span class="ke-diff__where">' + esc(c.label + (c.shared ? '' : ' · jen na téhle stránce')) + '</span>'
        + (lists ? seoListDiff(c.old || [], c['new'] || []) : wdiff(c.old, c['new'])) + '</div>';
    }).join('') + '</div>'
      + '<div class="ke-scope"><div class="ke-scope__head"><span class="ke-scope__n">Změní se ' + plural(n, 'stránka', 'stránky', 'stránek') + '</span>'
      + '<span class="ke-scope__kinds">' + plural(by.handbuilt.length, 'ruční', 'ruční', 'ručních') + ', '
      + plural(by.generated.length, 'generovaná', 'generované', 'generovaných') + ', ' + by.owner.length + ' owner</span></div>'
      + '<details class="ke-pages"><summary>' + (topics ? 'Stránky s výchozími tématy ' : 'Všechny stránky s údaji o vás ') + kic('chevron-down', 14) + '</summary><ul class="ke-pages__grid">'
      + by.owner.map(li('owner')).join('') + by.handbuilt.map(li('handbuilt')).join('') + by.generated.map(li('generated')).join('') + '</ul></details></div>'
      + seoOwnersHtml(b.owners, topics)
      + (rest ? '<div class="ke-check-note">' + kic('file-text', 14) + '<span>S nimi se uloží ' + plural(rest, 'text', 'texty', 'textů') + ' jen na téhle stránce (' + esc(seo.source) + ').</span></div>' : '')
      + '<div class="ke-check-note">' + kic('circle-check', 14) + '<span>Po uložení se stránky přestaví a kontrola webu ověří, že údaje o vás jsou na všech stránkách podle ' + esc(b.file) + '.</span></div>';
    var dlg = ask({title: (topics ? 'Uložit výchozí témata' : 'Uložit údaje o vás') + ' na ' + plural(n, 'stránku', 'stránky', 'stránek') + '?',
      desc: 'Sdílené údaje z <span class="k-mono">' + esc(b.file) + '</span>. Na web půjdou až s Publikovat, do té doby je uvidíš jen v editoru.',
      body: body, yes: 'Uložit na ' + plural(n, 'stránku', 'stránky', 'stránek'), yesIcon: 'check', no: 'Upravit dál',
      note: 'Vrátit jde ve Změnách.', cls: 'k-dialog--lg',
      done: function (ok) { if (ok) run(!!(b.owners && b.owners.length)); else stop(); }});
    ownersGate(dlg);
  }
  function seoOwnersHtml(owners, topics) {
    if (!owners || !owners.length) return '';
    return '<div class="ke-owner" role="group" aria-labelledby="keSOwT"><div class="ke-owner__head">' + kic('lock')
      + '<span id="keSOwT"><b>Mění i owner stránky:</b> ' + owners.map(function (s) {
        var p = PAGES[s];
        return esc(p && p.name ? p.name + ' (' + slugText(s) + ')' : slugText(s));
      }).join(' a ') + '. Jejich vlastní texty jsou zamčené (ADR 0003), ' + (topics ? 'výchozí témata mají stejná' : 'údaje o vás mají stejné') + ' jako ostatní stránky.</span></div>'
      + '<label><input class="k-check" type="checkbox" data-own-ok>Změnit i owner stránky</label></div>';
  }
  function seoListDiff(a, b) {
    return b.map(function (x) { return a.indexOf(x) < 0 ? '<ins>' + esc(x) + '</ins>' : esc(x); })
      .concat(a.filter(function (x) { return b.indexOf(x) < 0; }).map(function (x) { return '<del>' + esc(x) + '</del>'; })).join(', ');
  }
  if ($('#keSeo')) $('#keSeo').onclick = function () { if (openDrawer === 'seo') closeDrawer(); else renderSeo(); };

  /* ── Publikovat: dialog na seznamu Web (prototyp A4: připraveno, střet, běží, hotovo, nenasazeno) ──
     Z úpravy stránky vede Publikovat na <base>/#publikovat, dialog žije jen tady. */
  var PUB_STEP = {pending: ['wait', 'circle-dashed'], running: ['run', 'loader-circle'], done: ['done', 'circle-check'],
    failed: ['err', 'circle-alert']};
  var pubDlg = null, pubP = null, pubDirty = false, pubSeq = 0;
  function mmss(t) { return t == null ? '' : Math.floor(t / 60) + ':' + ('0' + (t % 60)).slice(-2); }
  function stepsHtml(steps) {
    return '<ol class="w-steps" aria-label="Kroky publikace">' + (steps || []).map(function (s) {
      var k = PUB_STEP[s.state] || PUB_STEP.pending;
      return '<li class="w-step w-step--' + k[0] + '">' + kic(k[1]) + '<span class="w-step__label">' + esc(s.label) + '</span>'
        + '<span class="w-step__t">' + mmss(s.t) + '</span>' + (s.detail ? '<span class="w-step__detail">' + esc(s.detail) + '</span>' : '') + '</li>';
    }).join('') + '</ol>';
  }
  function sumHtml(pages) {
    return pages.length ? '<div class="w-sum">' + pages.map(function (x) {
      return '<div class="w-sum__row"><span class="w-sum__page">' + esc(x.name || slugText(x.slug)) + '<span class="w-pg__slug">' + esc(slugText(x.slug)) + '</span></span>'
        + (x.count ? '<span class="w-sum__n">' + plural(x.count, 'úprava', 'úpravy', 'úprav') + '</span>' : '') + '</div>';
    }).join('') + '</div>' : '';
  }
  function whoHtml(author) {
    var m = /^(.*\S)\s*<([^<>]+)>$/.exec(author || '');
    return '<div class="w-who-line">' + kic('user-round') + 'Autor commitu: '
      + (m ? '<b>' + esc(m[1]) + '</b> &lt;' + esc(m[2]) + '&gt;, přihlášený uživatel' : 'identita gitu na serveru') + '</div>';
  }
  function errNotice(html) {
    return '<div class="k-notice k-notice--err w-tone--err w-top" role="alert">' + kic('circle-alert') + '<div class="k-notice__body"><span>' + html + '</span></div></div>';
  }
  function edCount(p) { return plural(p.entries, 'úprava', 'úpravy', 'úprav') + ' na ' + plural(p.pages.length, 'stránce', 'stránkách', 'stránkách'); }
  function pubFrame(state, title, desc, body, foot) {
    return {state: state, html: '<div class="k-dialog__head"><div><h2 class="k-dialog__title" id="wPubT">' + esc(title) + '</h2>'
      + '<p class="k-dialog__desc">' + desc + '</p></div>'
      + '<button class="k-ibtn" type="button" data-close aria-label="Zavřít">' + kic('x') + '</button></div>'
      + '<div class="k-dialog__body"><div class="w-dlg">' + body + '</div></div>'
      + '<div class="k-dialog__foot k-dialog__foot--bd">' + foot + '</div>'};
  }
  // Dialog jeden pro všechny stavy: běžící publikace mění jen kroky, ať zůstane fokus na Zavřít
  function pubShow(f) {
    if (pubDlg && dlgEl === pubDlg) {
      if (f.state === 'running' && pubDlg.dataset.state === 'running') {
        $('.w-steps', pubDlg).outerHTML = $('.w-steps', Object.assign(document.createElement('div'), {innerHTML: f.html})).outerHTML;
        return;
      }
      pubDlg.innerHTML = f.html;
      var a = $('[autofocus]', pubDlg);
      if (a) a.focus();
    } else {
      pubDlg = dialog(f.html, function () {
        pubDlg = null;
        pubSeq++;
        if (pubDirty) { pubDirty = false; location.reload(); }
      }, 'k-dialog--lg');
    }
    pubDlg.dataset.state = f.state;
  }

  function pubReady(p) {
    var probs = p.problems || [], rd = p.redeploy;
    var body = sumHtml(p.pages)
      + probs.map(function (x) { return errNotice(esc(x)); }).join('')
      + (rd ? errNotice('<b>Publikace ' + esc(rd.commit.slice(0, 7)) + ' je v main, ale na webu není.</b> Nasadit znovu nasadí tentýž commit.') : '')
      + (p.incoming ? '<div class="k-notice w-top">' + kic('git-merge') + '<div class="k-notice__body"><span><b>Sloučí se se změnami z main ('
        + plural(p.incoming, 'commit', 'commity', 'commitů') + ').</b> Zkouška sloučení proběhla teď, bez střetu.</span>' + commitsHtml(p.commit_list) + '</div></div>' : '')
      + (p.entries ? '<div class="k-field"><label class="k-field__label" for="wPubMsg">Zpráva commitu</label>'
        + '<input class="k-input" id="wPubMsg" value="' + esc(p.message) + '" autocomplete="off"' + (probs.length ? '' : ' autofocus') + '></div>'
        + whoHtml(p.author) + stepsHtml(p.steps) : '');
    var foot = '<span class="k-dialog__note">Z adminu jde na web jen HTML a sitemap.</span>'
      + '<button class="k-btn k-btn--secondary" type="button" data-close' + (probs.length ? ' autofocus' : '') + '>Zrušit</button>'
      + (rd ? '<button class="k-btn k-btn--secondary" type="button" data-redeploy>' + kic('refresh-cw') + 'Nasadit znovu</button>' : '')
      + (p.entries ? '<button class="k-btn k-btn--primary" type="button" data-go' + (probs.length ? ' disabled' : '') + '>' + kic('send')
        + 'Publikovat ' + plural(p.entries, 'úpravu', 'úpravy', 'úprav') + '</button>' : '');
    return pubFrame('ready', 'Publikovat na ' + p.host, p.entries ? edCount(p) + '. Na webu budou zhruba za minutu.' : 'Není co publikovat.', body, foot);
  }

  // Střet úprav se změnou v main (publish._merge): úprava s Vrátit, co ji v main změnilo
  function pubConflict(p) {
    var n = 0;
    var groups = p.conflicts.map(function (c) {
      var cm = (c.commit_list || [])[0], by = cm ? ((cm.coauthors && cm.coauthors[0]) || cm.author) : '';
      var hit = c.why || ('v main ' + (cm ? cm.sha + ' (' + by + ', ' + when(cm.time) + ')' : '') + ' změnil totéž místo');
      var rows = c.edits.map(function (e) {
        n++;
        var lab = String(e.label || '');               // „slug · značka · místo“, stránku nese skupina
        if (c.page && lab.indexOf(c.page + ' · ') === 0) lab = lab.slice(c.page.length + 3);
        return '<div class="w-ed w-ed--hit"><div class="w-ed__meta"><b>' + esc(lab) + '</b>' + (e.t ? ' · ' + esc(when(e.t)) : '') + (e.user ? ' · ' + esc(e.user) : '') + '</div>'
          + '<div class="w-ed__diff">' + (e.text ? '„' + esc(e.text) + '“' : '') + '<div class="w-hit">' + kic('circle-alert') + 'Střet: ' + esc(hit) + '</div></div>'
          + (e.undo ? '<div class="w-ed__act"><button class="k-btn k-btn--secondary k-btn--sm" type="button" data-undo="' + esc(e.id) + '">' + kic('rotate-ccw') + 'Vrátit</button></div>' : '')
          + '</div>';
      }).join('');
      return '<div class="w-pg"><div class="w-pg__head"><div class="w-pg__name"><span class="w-pg__title">' + esc(c.page ? slugText(c.page) : c.path) + '</span>'
        + '<span class="w-pg__slug">' + esc(c.path) + '</span></div></div><div class="w-eds">' + rows + '</div></div>';
    }).join('');
    var steps = (p.steps || []).map(function (s) { return s.key === 'git' ? Object.assign({}, s, {state: 'failed', detail: 'střet v ' + p.conflicts.map(function (c) { return c.path; }).join(', ')}) : s; });
    var body = errNotice('<b>' + (n === 1 ? '1 úprava se střetla' : plural(n, 'úprava se střetla', 'úpravy se střetly', 'úprav se střetlo')) + ' se změnou v main, nic se nezměnilo.</b> '
      + 'Vrať ji, publikuj zbytek a po publikaci ji udělej znovu na novém textu. Vrátit jde vždy nejnovější úpravu souboru.')
      + '<div class="w-pend w-sum">' + groups + '</div>' + stepsHtml(steps);
    var foot = '<span class="k-dialog__note">Po Vrátit se sloučení zkusí znovu.</span>'
      + '<button class="k-btn k-btn--secondary" type="button" data-close autofocus>Zavřít</button>'
      + '<button class="k-btn k-btn--primary" type="button" disabled>' + kic('send') + 'Publikovat</button>';
    return pubFrame('conflict', 'Publikovat na ' + p.host, 'Publikovat teď nejde: úpravy se střetly se změnou v main.', body, foot);
  }

  function pubRunning(s, redeploy) {
    redeploy = redeploy || !!(s && s.steps.length && s.steps[0].key === 'deploy');    // Nasadit znovu má jen deploy a kontrolu
    var p = pubP, pages = p.pages.length ? p.pages : ((s && s.pages) || []).map(function (x) { return {slug: x}; });
    var steps = s ? s.steps : p.steps.filter(function (x) { return !redeploy || x.key === 'deploy' || x.key === 'smoke'; })
      .map(function (x, i) { return i ? x : Object.assign({}, x, {state: 'running'}); });
    var body = sumHtml(pages) + stepsHtml(steps) + (redeploy ? '' : whoHtml(p.author));
    var foot = '<span class="k-dialog__note">Ukládat úpravy jde zase po doběhnutí.</span>'
      + '<button class="k-btn k-btn--secondary" type="button" data-close autofocus>Zavřít, poběží dál</button>';
    return pubFrame('running', (redeploy ? 'Nasazuji na ' : 'Publikuji na ') + p.host,
      (p.entries && !redeploy ? edCount(p) + '. ' : '') + 'Okno můžeš zavřít, publikace doběhne i bez něj.', body, foot);
  }

  function pubDone(s) {
    var site = pubP.site, d = new Date();
    var links = (s.pages || []).map(function (x) {
      var u = site + '/' + (x === 'index' ? '' : x);
      return '<li><a href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(u.replace(/^https?:\/\//, '')) + kic('external-link') + '</a></li>';
    }).join('');
    var body = '<div class="k-notice k-notice--ok w-tone--ok">' + kic('circle-check') + '<span><b>Na webu od ' + d.getHours() + ':' + ('0' + d.getMinutes()).slice(-2)
      + '.</b> Verze ' + esc(s.version || '') + '.</span></div>' + stepsHtml(s.steps) + (links ? '<ul class="w-links">' + links + '</ul>' : '');
    return pubFrame('done', 'Publikováno', plural((s.pages || []).length, 'stránka', 'stránky', 'stránek') + (s.commit ? ', commit ' + esc(s.commit.slice(0, 7)) : '') + '.',
      body, '<button class="k-btn k-btn--primary" type="button" data-close autofocus>Hotovo</button>');
  }

  function pubFailed(s) {
    var failed = s.steps.filter(function (x) { return x.state === 'failed'; })[0] || {};
    var close = '<button class="k-btn k-btn--secondary" type="button" data-close' + (s.redeploy ? '' : ' autofocus') + '>Zavřít</button>';
    if (failed.key === 'deploy' && s.redeploy) {
      return pubFrame('failed', 'Publikováno, ale nenasazeno', 'Commit ' + esc((s.commit || s.redeploy.commit).slice(0, 7)) + ' je v main, na ' + esc(pubP.host) + ' zatím není.',
        errNotice('<b>Úpravy jsou v main, na web nešly.</b> Nasadit znovu nasadí tentýž commit, nic dalšího se nepublikuje.') + stepsHtml(s.steps),
        '<span class="k-dialog__note">Nasadit znovu najdeš i na stránce Web.</span>' + close
        + '<button class="k-btn k-btn--primary" type="button" data-redeploy autofocus>' + kic('refresh-cw') + 'Nasadit znovu</button>');
    }
    if (failed.key === 'smoke') {
      return pubFrame('failed', 'Nasazeno, kontrola neprošla', 'Verze ' + esc(s.version || '') + ' je na webu, ale nový text na ní kontrola nenašla.',
        errNotice('<b>Zkontroluj stránky na webu.</b> Úpravy jsou v main i na webu, chybí jen potvrzení kontrolou.') + stepsHtml(s.steps), close);
    }
    return pubFrame('failed', 'Publikace selhala', s.commit ? 'Commit ' + esc(s.commit.slice(0, 7)) + ' je v main.' : 'Nic se nepublikovalo, úpravy zůstávají v editoru.',
      stepsHtml(s.steps), close);
  }

  function openPublish(redeploy) {
    if (!PUBLISH) { toast('Odsud se nepublikuje' + (PUBLISH_URL ? ', publikuj na ' + PUBLISH_URL : '') + '.', 'info'); return; }
    api('GET', '/api/publish/preview').then(function (r) {
      if (!r.ok) return toast(r.body.error || 'Náhled publikace se nenačetl.', 'err');
      pubP = r.body;
      pubP.host = String(pubP.site || '').replace(/^https?:\/\//, '') || 'web';
      if (pubP.running) { pubShow(pubRunning(null, false)); pollPub(); return; }
      if (redeploy) { startPub(true); return; }
      pubShow(pubP.conflicts && pubP.conflicts.length ? pubConflict(pubP) : pubReady(pubP));
    }, offline);
  }
  function startPub(redeploy) {
    var msg = pubDlg && $('#wPubMsg', pubDlg), btns = pubDlg ? $$('[data-go], [data-redeploy]', pubDlg) : [];
    btns.forEach(function (b) { b.disabled = true; });
    api('POST', redeploy ? '/api/deploy' : '/api/publish', redeploy ? {} : {message: msg ? msg.value : ''}).then(function (r) {
      if (!r.ok) {
        btns.forEach(function (b) { b.disabled = false; });
        toast(r.body.error || 'Publikace se nespustila.', 'err');
        return;
      }
      pubDirty = true;
      pubShow(pubRunning(null, redeploy));
      pollPub();
    }, function () { btns.forEach(function (b) { b.disabled = false; }); offline(); });
  }
  function pollPub() {
    var seq = ++pubSeq;
    api('GET', '/api/publish/status').then(function (r) {
      if (seq !== pubSeq || !pubDlg) return;
      var s = r.body;
      setCount(s.count);
      if (s.state === 'running') { pubShow(pubRunning(s)); setTimeout(function () { if (seq === pubSeq) pollPub(); }, 800); return; }
      if (s.state === 'done') { pubShow(pubDone(s)); return; }
      var failed = s.steps.filter(function (x) { return x.state === 'failed'; })[0] || {};
      if (failed.key !== 'git') { pubShow(pubFailed(s)); return; }
      // střet až při publikaci (main se mezitím změnil): ukázat úpravy s Vrátit jako před publikací
      api('GET', '/api/publish/preview').then(function (q) {
        if (seq !== pubSeq || !pubDlg) return;
        if (q.ok && q.body.conflicts && q.body.conflicts.length) { pubP = Object.assign(q.body, {host: pubP.host}); pubShow(pubConflict(pubP)); }
        else pubShow(pubFailed(s));
      }, function () { pubShow(pubFailed(s)); });
    }, function () { if (seq === pubSeq) setTimeout(function () { if (seq === pubSeq) pollPub(); }, 2000); });
  }
  document.addEventListener('click', function (e) {
    if (!pubDlg || !pubDlg.contains(e.target)) return;
    var t = e.target.closest('[data-go], [data-redeploy], [data-undo]');
    if (!t || t.disabled) return;
    if (t.hasAttribute('data-undo')) {
      t.disabled = true;
      api('POST', '/api/undo', {id: t.dataset.undo}).then(function (r) {
        if (!r.ok) { t.disabled = false; toast(r.body.error, 'err'); return; }
        pubDirty = true;
        setCount(r.body.count);
        toast('Vráceno, sloučení zkouším znovu.', 'ok');
        openPublish();
      }, function () { t.disabled = false; offline(); });
      return;
    }
    startPub(t.hasAttribute('data-redeploy'));
  });
  document.addEventListener('keydown', function (e) {
    if (pubDlg && e.key === 'Enter' && e.target.id === 'wPubMsg') { e.preventDefault(); var g = $('[data-go]', pubDlg); if (g && !g.disabled) startPub(false); }
  });
  if ($('#kePublish')) $('#kePublish').onclick = function () {
    if (MODE === 'list') { openPublish(); return; }
    go(BASE + '/#publikovat');             // úprava stránky: dialog je na seznamu Web
  };
  banners.addEventListener('click', function (e) {
    var t = e.target.closest('[data-pub], [data-redeploy]');
    if (t) openPublish(t.hasAttribute('data-redeploy'));
  });

  /* ── zámky, sdílený text a díry stránky v menu a patičce: tooltip ── */
  function sharedText(el, verb) {
    var n = +el.dataset.keShared, pages = (el.dataset.kePages || '').split(' ').filter(Boolean);
    return 'Sdílený text: ' + verb + ' i na ' + plural(n, 'další stránce', 'dalších stránkách', 'dalších stránkách')
      + ' (' + pages.slice(0, 4).join(', ') + (pages.length > 4 ? ' …' : '') + ').';
  }
  // Sdílené části stránky (menu a patička ze sdíleného souboru): <header> nebo <footer> s bloky
  // data-ke-part. Bloky v nich bez data-ke-part jsou díry stránky (kotvy, tlačítko, vlastní sloupec).
  var REGIONS = [];
  if (MODE === 'edit') $$('[data-ke-part]').forEach(function (b) {
    var r = b.closest('header, footer');
    if (r && !REGIONS.some(function (x) { return x.el === r; })) REGIONS.push({el: r, kind: r.tagName.toLowerCase(), rel: b.dataset.kePart});
  });
  function regionOf(el) { return REGIONS.filter(function (r) { return r.el.contains(el); })[0] || null; }
  function isOwn(el) { return !el.dataset.kePart && !!regionOf(el); }
  function ownText(el) {
    return regionOf(el).kind === 'footer' ? 'Tahle část patičky patří jen téhle stránce, ukládá se do ní.'
      : 'Tahle položka menu patří jen téhle stránce (kotva nebo tlačítko v liště), ukládá se do ní.';
  }
  var tip = null;
  if (MODE === 'edit') document.addEventListener('mousemove', function (e) {
    var t = e.target.closest && e.target.closest('[data-ke-lock], [data-ke]');
    var text = null, lock = false, xt = xTip(e.target);
    if (xt) { text = xt.text; lock = xt.lock; }       // obrázek, pole nebo text v SVG (Další texty)
    else if (t && !(cur && cur.el === t) && !e.target.closest('.ke-ui')) {
      if (t.hasAttribute('data-ke-lock')) { text = t.getAttribute('data-ke-lock'); lock = true; }
      else if (t.dataset.keShared) text = sharedText(t, 'je');
      else if (isOwn(t)) text = ownText(t);
    }
    if (!text) { if (tip) { tip.remove(); tip = null; } return; }
    if (!tip) { tip = document.createElement('div'); tip.className = 'k-pop k-pop--tip ke-tip ke-ui'; document.body.appendChild(tip); }
    tip.innerHTML = (lock ? kic('lock') : kic('info')) + '<span>' + esc(text) + '</span>';
    var x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 12), y = e.clientY + 18;
    if (y + tip.offsetHeight > innerHeight - 8) y = e.clientY - tip.offsetHeight - 12;
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  });

  /* ── mega a mobilní menu: skripty webu v úpravách neběží, editor je otevírá sám ──
     Selektory dává adaptér webu (Rules.menu → meta ke-menu): mega = spouštěč s aria-expanded,
     toggle a nav = tlačítko a navigace na telefonu (nav dostane třídu open), narrow = šířka okna,
     do které má web navigaci za tlačítkem, frame = prvek, podle kterého se kreslí rám oblasti. */
  var MENU = {};
  try { MENU = JSON.parse(meta('ke-menu') || '{}'); } catch (e) { MENU = {}; }
  var trig = MODE === 'edit' && MENU.mega ? $(MENU.mega) : null;
  var burger = MODE === 'edit' && MENU.toggle ? $(MENU.toggle) : null;
  var nav = MODE === 'edit' && MENU.nav ? $(MENU.nav) : null;
  function narrow() { return !!MENU.narrow && innerWidth <= MENU.narrow; }
  function megaOpen() { return !!trig && trig.getAttribute('aria-expanded') === 'true'; }
  function navOpen() { return !!nav && nav.classList.contains(MENU.open || 'is-open'); }
  // menu dosedá animací (web: nav-drop, ~250 ms): rám a plovoucí lištu změřit znovu po ní
  function settle() { place(); if (cur) placeFloat(cur); setTimeout(function () { place(); if (cur) placeFloat(cur); }, 320); }
  function setMega(open) { if (!trig) return; trig.setAttribute('aria-expanded', String(open)); settle(); }
  function setNav(open) {
    if (!nav) return;
    nav.classList.toggle(MENU.open || 'is-open', open);
    if (burger) burger.setAttribute('aria-expanded', String(open));
    if (!open) setMega(false);
    settle();
  }
  function trigLabel() { var t = trig ? trig.textContent.replace(/\s+/g, ' ').trim().split(' ')[0] : ''; return t || 'menu'; }

  /* ── oblast sdílené části: rám a štítek s rozsahem změny ── */
  var region = null, regionHot = null;
  if (REGIONS.length) {
    region = document.createElement('div');
    region.className = 'ke-region ke-ui';
    region.hidden = true;
    document.body.appendChild(region);
    REGIONS.forEach(function (r) {
      r.el.addEventListener('mouseenter', function () { regionHot = r; place(); });
      r.el.addEventListener('mouseleave', function (e) {
        if (e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('.ke-region')) return;
        regionHot = null;
        place();
      });
    });
    region.addEventListener('mouseleave', function () { regionHot = null; place(); });
    region.addEventListener('mousedown', function (e) { e.preventDefault(); });   // fokus zůstane v rozepsaném bloku
    region.addEventListener('click', function (e) {
      var b = e.target.closest('[data-tab]');
      if (!b) return;
      if (b.dataset.tab === 'mega') setMega(!megaOpen());
      else setNav(!navOpen());
    });
  }
  function regionRect(r) {
    var sel = MENU.frame && MENU.frame[r.kind];
    var base = (sel && $(sel, r.el)) || r.el.firstElementChild || r.el;
    var q = base.getBoundingClientRect(), u = {left: q.left, top: q.top, right: q.right, bottom: q.bottom};
    if (r.kind === 'header' && navOpen() && nav.offsetParent) {
      var n = nav.getBoundingClientRect();
      if (n.width && n.height) {
        u.left = Math.min(u.left, n.left); u.top = Math.min(u.top, n.top);
        u.right = Math.max(u.right, n.right); u.bottom = Math.max(u.bottom, n.bottom);
      }
    }
    return u;
  }
  function tabHtml(r) {
    var out = kic('layers', 14) + '<span>' + (r.kind === 'header' ? 'Sdílené menu' : 'Sdílená patička') + ' · '
      + plural(PARTS[r.rel] || 0, 'stránka', 'stránky', 'stránek') + '</span>';
    if (r.kind === 'header' && burger && narrow()) {
      out += '<button class="ke-tab__btn" type="button" data-tab="nav">' + (navOpen() ? kic('x', 14) + 'Zavřít menu' : kic('menu', 14) + 'Otevřít menu') + '</button>';
    } else if (r.kind === 'header' && trig && (!narrow() || navOpen())) {
      out += '<button class="ke-tab__btn" type="button" data-tab="mega">' + (megaOpen() ? kic('chevron-up', 14) + 'Zavřít ' : kic('chevron-down', 14) + 'Otevřít ')
        + esc(trigLabel()) + '</button>';
    }
    if (ROLE !== 'admin') out += '<span class="ke-tab__btn" aria-disabled="true">' + kic('lock', 14) + 'Mění admin</span>';
    return out;
  }
  function place() {
    if (!region) return;
    var own = cur && !cur.el.dataset.kePart;          // díra stránky: štítek sdílené části by mátl
    var keep = !own && cur ? regionOf(cur.el) : null;
    if (!own && !keep && (megaOpen() || navOpen())) keep = REGIONS.filter(function (r) { return r.kind === 'header'; })[0] || null;
    var r = own ? null : regionHot || keep;
    if (!r) { region.hidden = true; return; }
    var q = regionRect(r), pad = 6, top = Math.max(52, q.top - pad);
    region.style.left = q.left - pad + 'px';
    region.style.top = top + 'px';
    region.style.width = q.right - q.left + pad * 2 + 'px';
    region.style.height = Math.max(0, q.bottom + pad - top) + 'px';
    region.classList.toggle('ke-region--up', r.kind === 'footer' || q.bottom > innerHeight - 60);
    region.innerHTML = '<div class="ke-tab">' + tabHtml(r) + '</div>';
    region.hidden = false;
  }
  if (MODE === 'edit') {
    addEventListener('scroll', function () { place(); if (cur && cur.fixed) placeFloat(cur); }, {passive: true});
    addEventListener('resize', function () { place(); if (cur) placeFloat(cur); });
  }

  /* ── editace bloků ── */
  var cur = null;          // {el, before, atoms, float, fixed}
  var needReload = false;
  var orig = {};           // n → innerHTML na serveru (po posledním uložení)
  function blocksAll() { return $$('[data-ke]'); }
  function atomsOf(el) { return $$('[data-ke-atom]', el); }
  function countAtoms(html) { var t = document.createElement('template'); t.innerHTML = html; return t.content.querySelectorAll('[data-ke-atom]').length; }
  function draftKey(el) { return 'ke-draft:' + SLUG + ':' + el.dataset.ke + ':' + el.dataset.keH; }
  function dropDraft(el) { store(draftKey(el), null); el.classList.remove('ke-draft'); }
  function keepDraft(el) { store(draftKey(el), el.innerHTML); el.classList.add('ke-draft'); }

  if (MODE === 'edit') {
    var keys = {};
    blocksAll().forEach(function (el) { orig[el.dataset.ke] = el.innerHTML; keys[draftKey(el)] = el; });
    var restored = 0;
    try {
      for (var i = localStorage.length - 1; i >= 0; i--) {
        var k = localStorage.key(i);
        if (!k || k.indexOf('ke-draft:' + SLUG + ':') !== 0) continue;
        var el = keys[k], d = localStorage.getItem(k);
        if (el && d && d !== el.innerHTML) { el.innerHTML = d; el.classList.add('ke-draft'); restored++; }
        else localStorage.removeItem(k);       // blok se mezitím změnil nebo draft nic nemění
      }
    } catch (e) { /* bez localStorage se drafty jen nedrží */ }
    if (restored) toast('Na stránce ' + (restored === 1 ? 'je rozepsaný text' : 'jsou rozepsané texty (' + restored + ')')
      + ' z minula. Klikni do bloku a ulož Ctrl+Enter, Esc ho zahodí.', 'info');
  }

  function partWhat(el) { var r = regionOf(el); return r && r.kind === 'footer' ? 'patičky' : 'menu'; }
  function hasOwners() { return Object.keys(PAGES).some(function (s) { return PAGES[s].kind === 'owner'; }); }
  function floatFor(el) {
    var f = document.createElement('div');
    f.className = 'ke-float ke-ui';
    var notes = [];
    var part = el.dataset.kePart;
    var files = part ? [part] : el.dataset.keFile ? el.dataset.keFile.split(',') : [meta('ke-source')];
    if (part) {
      notes.push(['layers', '<b>Sdílený text ' + partWhat(el) + ':</b> uloží se na ' + plural(PARTS[part] || 0, 'stránce', 'stránkách', 'stránkách')
        + ' webu' + (hasOwners() ? ', i na owner stránkách' : '') + '. Potvrdíš to při uložení.']);
    } else if (isOwn(el)) {
      notes.push(['file-text', '<b>Jen tahle stránka:</b> ' + esc(ownText(el))]);
    }
    if (el.closest('summary')) notes.push(['info', esc(KIND === 'generated'
      ? 'Otázka FAQ: JSON-LD má na generované stránce vlastní znění, tady se nemění.'
      : 'Otázka FAQ: po uložení se propíše i do FAQ v JSON-LD (prostým textem).')]);
    if (el.dataset.keShared) notes.push(['layers', esc(sharedText(el, 'uloží se') + ' Potvrdíš to při uložení.')]);
    if (el.hasAttribute('data-ke-plain')) notes.push(['info', esc('Text jde i do hlavičky nebo JSON-LD stránky, tam se ukládá prostý bez formátování a pevných mezer.')]);
    f.innerHTML = '<div class="ke-float__row"><button class="k-btn k-btn--primary k-btn--sm" type="button" data-a="save">Uložit <span class="k-kbd">Ctrl+Enter</span></button>'
      + '<button class="k-btn k-btn--secondary k-btn--sm" type="button" data-a="cancel">Zrušit <span class="k-kbd">Esc</span></button>'
      + '<span class="ke-float__keys">Ctrl+B tučně · Ctrl+I kurzíva · Ctrl+K odkaz</span></div>'
      + '<div class="ke-float__src">' + kic('file-pen-line', 14) + '<span class="k-mono">' + esc(files.join(' · ')) + '</span></div>'
      + notes.map(function (n) { return '<div class="ke-float__note">' + kic(n[0]) + '<span>' + n[1] + '</span></div>'; }).join('');
    f.addEventListener('mousedown', function (e) { e.preventDefault(); });
    f.onclick = function (e) { var a = e.target.closest('[data-a]'); if (a) finish(a.dataset.a === 'save'); };
    document.body.appendChild(f);
    return f;
  }
  // blok v pevně umístěné liště webu (menu): plovoucí lišta je taky pevná, jinak jde se stránkou
  function fixedIn(el) {
    for (var x = el; x && x !== document.body; x = x.parentElement) if (getComputedStyle(x).position === 'fixed') return true;
    return false;
  }
  function placeFloat(c) {
    var f = c.float, r = c.el.getBoundingClientRect();
    f.style.position = c.fixed ? 'fixed' : 'absolute';
    var oy = c.fixed ? 0 : scrollY, ox = c.fixed ? 0 : scrollX;
    var above = r.top - f.offsetHeight - 10, below = r.bottom + 10;
    var top = !c.fixed && above >= 56 ? above : below;
    if (top + f.offsetHeight > innerHeight - 8 && above >= 56) top = above;
    f.style.top = top + oy + 'px';
    f.style.left = Math.max(12, Math.min(r.left, innerWidth - f.offsetWidth - 12)) + ox + 'px';
  }

  function start(el, e) {
    if (cur && cur.el === el) return;
    if (el.classList.contains('ke-saving')) return;
    if (cur) finish(true);
    if (tip) { tip.remove(); tip = null; }
    var before = orig[el.dataset.ke];
    atomsOf(el).forEach(function (a) { a.contentEditable = 'false'; });
    el.contentEditable = 'true';
    el.focus();
    cur = {el: el, before: before, atoms: countAtoms(before), fixed: fixedIn(el)};
    cur.float = floatFor(el);
    placeFloat(cur);
    var r = e && document.caretRangeFromPoint ? document.caretRangeFromPoint(e.clientX, e.clientY) : null;
    if (r && el.contains(r.startContainer)) { var s = getSelection(); s.removeAllRanges(); s.addRange(r); }
    place();
  }

  function finish(save) {
    if (!cur) return;
    var c = cur;
    cur = null;
    c.float.remove();
    c.el.removeAttribute('contenteditable');
    atomsOf(c.el).forEach(function (a) { a.removeAttribute('contenteditable'); });
    place();
    if (!save || c.el.innerHTML === c.before) {
      c.el.innerHTML = c.before;
      dropDraft(c.el);
    } else if (atomsOf(c.el).length !== c.atoms) {
      c.el.innerHTML = c.before;
      dropDraft(c.el);
      toast('Ikona, obrázek ani číslo z dat webu se smazat nesmí. Text jsem vrátil do původní podoby.', 'err');
    } else {
      send(c.el, null);
      return;
    }
    maybeReload();
  }

  // Vrátit na téhle stránce během editace: načíst znovu, až nic neběží.
  function maybeReload() {
    if (!needReload || cur || $$('.ke-saving').length || dlgEl) return;
    sess('ke-after', JSON.stringify({drawer: openDrawer}));
    location.reload();
  }

  // owners = v dialogu sdílené změny zaškrtnuté, že se mění i owner stránky
  function send(el, token, owners) {
    el.classList.add('ke-saving');
    keepDraft(el);
    var part = el.dataset.kePart;
    if (part && !token) toast('Zkouším změnu na ' + plural(PARTS[part] || 0, 'stránce', 'stránkách', 'stránkách') + ', chvíli to trvá.', 'info');
    api('POST', '/api/save', {slug: SLUG, n: +el.dataset.ke, h: el.dataset.keH, page: meta('ke-page'),
      html: el.innerHTML, confirm: token || null, owners: !!owners}).then(function (r) {
      el.classList.remove('ke-saving');
      var b = r.body;
      if (r.ok && b.confirm) { dropToast(); return b.part ? confirmPart(el, b) : confirmSave(el, b); }
      if (r.ok && b.ok) {
        dropDraft(el);
        if (b.unchanged) { dropToast(); el.innerHTML = orig[el.dataset.ke]; return; }
        el.innerHTML = b.inner;
        orig[el.dataset.ke] = b.inner;
        el.dataset.keH = b.hash;
        if (b.inner.indexOf('data-ke-a=') >= 0) xRead();      // obrázek v bloku: nový prvek pro Další texty
        setMeta('ke-page', b.page);
        setCount(b.count);
        var msg;
        if (b.part) msg = 'Uloženo do ' + b.part + ', přestaveno ' + plural(b.pages.length, 'stránka', 'stránky', 'stránek') + '. Na web půjde s Publikovat.';
        else msg = 'Uloženo do ' + (b.files && b.files.length
          ? b.files.join(', ') + ' a do ' + (b.pages && b.pages.length ? plural(1 + b.pages.length, 'stránky', 'stránek', 'stránek') : PATH)
          : PATH + (b.linked ? ', otázka i do FAQ v JSON-LD' : '')) + '.';
        toast(msg, 'ok');
        if (openDrawer === 'changes') renderChanges();
        return maybeReload();
      }
      failSave(el, b);
    }, function () { el.classList.remove('ke-saving'); offline(); });
  }

  function failSave(el, b) {
    if (b.code === 'atom' || b.code === 'locked' || b.code === 'role') {
      el.innerHTML = orig[el.dataset.ke];
      dropDraft(el);
      toast((b.error || 'Tohle místo se upravit nedá.') + ' Text jsem vrátil do původní podoby.', 'err');
    } else if (b.code === 'stale') {
      toast(b.error + ' Rozepsaný text zůstal v prohlížeči.', 'err');
    } else if (b.details && b.details.length) {
      failDialog(b, 'Text zůstal rozepsaný v bloku, oprav ho a ulož znovu.');
    } else {
      toast((b.error || 'Uložení selhalo.') + ' Text zůstal rozepsaný v bloku.', 'err');
    }
  }

  // Úprava, která změní víc než blok (apply._save_mapped); sdílená hodnota z dat na owner stránkách
  // chce zaškrtnutí jako sdílená změna menu (ADR 0003 dodatek)
  function confirmSave(el, b) {
    var own = !!(b.owners && b.owners.length);
    var dlg = ask({title: b.message, body: listHtml(b.details) + ownersHtml(b.owners, 'sdílený text z dat mají stejný'), yes: 'Uložit i tak', no: 'Upravit dál',
      done: function (yes) { if (yes) send(el, b.confirm, own); else start(el); }});
    if (own) ownersGate(dlg);
  }

  // Sdílená změna menu nebo patičky (apply.save_part): rozdíl po slovech, kde je, kolik stránek
  // a jakého druhu, owner stránky zvlášť se zaškrtnutím (ADR 0003 dodatek). Zrušit vrátí do úpravy.
  function whereOf(el) {
    var lead = partWhat(el) === 'menu' ? 'Menu' : 'Patička';
    var mi = el.closest('[role="menuitem"]');
    if (mi && mi !== el) {
      var first = $('[data-ke]', mi);
      var name = first ? String(first === el ? orig[el.dataset.ke] : first.innerHTML).replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim() : '';
      return lead + (name ? ' · ' + name : '') + (first === el ? ' · název' : ' · popis');
    }
    if (trig && trig === el) return lead + ' · nápis rozbalovacího menu';
    var nv = el.closest('nav[aria-label]');
    return lead + (nv ? ' · ' + nv.getAttribute('aria-label') : '');
  }
  function confirmPart(el, b) {
    var n = b.pages.length, by = {handbuilt: [], generated: [], owner: []};
    b.pages.forEach(function (p) { (by[p.kind] || (by[p.kind] = [])).push(p.slug); });
    var li = function (kind) {
      return function (s) {
        var p = PAGES[s];
        return '<li' + (p && p.name ? ' title="' + esc(p.name) + '"' : '') + '>' + esc(slugText(s))
          + (kind === 'owner' ? ' <span>owner</span>' : kind === 'generated' ? ' <span>generovaná</span>' : '') + '</li>';
      };
    };
    var body = '<div class="ke-changes"><div class="ke-diff"><span class="ke-diff__where">' + esc(whereOf(el)) + '</span>' + wdiff(b.before, b.after) + '</div></div>'
      + '<div class="ke-scope"><div class="ke-scope__head"><span class="ke-scope__n">Změní se ' + plural(n, 'stránka', 'stránky', 'stránek') + '</span>'
      + '<span class="ke-scope__kinds">' + plural(by.handbuilt.length, 'ruční', 'ruční', 'ručních') + ', '
      + plural(by.generated.length, 'generovaná', 'generované', 'generovaných') + ', ' + by.owner.length + ' owner</span></div>'
      + '<details class="ke-pages"><summary>Všechny stránky ' + kic('chevron-down', 14) + '</summary><ul class="ke-pages__grid">'
      + by.owner.map(li('owner')).join('') + by.handbuilt.map(li('handbuilt')).join('') + by.generated.map(li('generated')).join('') + '</ul></details></div>'
      + listHtml(b.details) + ownersHtml(b.owners)
      + '<div class="ke-check-note">' + kic('circle-check', 14) + '<span>Po uložení se stránky přestaví a kontrola webu ověří, že menu a patička jsou na všech stránkách stejné jako šablona.</span></div>';
    var dlg = ask({title: 'Uložit změnu ' + partWhat(el) + ' na ' + plural(n, 'stránku', 'stránky', 'stránek') + '?',
      desc: 'Sdílený text z <span class="k-mono">' + esc(b.part) + '</span>. Na web půjde až s Publikovat, do té doby ho uvidíš jen v editoru.',
      body: body, yes: 'Uložit na ' + plural(n, 'stránku', 'stránky', 'stránek'), yesIcon: 'check', no: 'Upravit dál',
      note: 'Vrátit jde ve Změnách.', cls: 'k-dialog--lg',
      done: function (yes) { if (yes) send(el, b.confirm, !!(b.owners && b.owners.length)); else start(el); }});
    ownersGate(dlg);
  }

  function makeLink() {
    var s = getSelection();
    if (!s.rangeCount || s.isCollapsed) { toast('Nejdřív označ text, ze kterého má být odkaz.', 'info'); return; }
    var r = s.getRangeAt(0).cloneRange();
    var u = prompt('Adresa odkazu (např. /ppc-audit):', '/');
    if (!cur) return;
    cur.el.focus();
    s.removeAllRanges();
    s.addRange(r);
    if (u && u.trim()) document.execCommand('createLink', false, u.trim());
  }

  function openLink(a) {
    var u = new URL(a.getAttribute('href') || '', location.href);
    if (u.origin !== location.origin) { window.open(u.href, '_blank', 'noopener'); return; }
    var path = BASE && u.pathname.indexOf(BASE + '/') === 0 ? u.pathname.slice(BASE.length) : u.pathname;
    var slug = path.replace(/^\/+|\.html$/g, '') || 'index';
    if (slug === SLUG) { if (u.hash) location.hash = u.hash; return; }
    go(pageUrl(slug, '?e=1' + u.hash));
  }

  if (MODE === 'edit') {
    // Odkazy a tlačítka webu v editaci nic nedělají, <summary> nepřepíná <details>.
    document.addEventListener('click', function (e) {
      if (e.target.closest('.ke-ui')) return;
      // tlačítko menu na telefonu navigaci otevře a zavře; spouštěč mega menu první klik otevře,
      // u otevřeného se jeho nápis přepíše (když ho uživatel měnit smí), jinak menu zavře
      if (burger && burger.contains(e.target)) { e.preventDefault(); if (cur) finish(true); setNav(!navOpen()); return; }
      if (trig && trig.contains(e.target)) {
        var editable = trig.hasAttribute('data-ke');
        if (!megaOpen() || !editable) { e.preventDefault(); if (cur) finish(true); setMega(!megaOpen()); return; }
      }
      var a = e.target.closest('a');
      if (a) { e.preventDefault(); if (e.ctrlKey || e.metaKey) { openLink(a); return; } }
      if (e.target.closest('summary, button, label')) e.preventDefault();
      // obrázek, pole formuláře a text v SVG s texty mimo bloky otevřou popover (Další texty)
      var xt = xTarget(e.target);
      if (xt) { e.preventDefault(); if (cur) finish(true); xOpen(xt.el, xt.its); return; }
      var el = e.target.closest('[data-ke]');
      if (el) { if (!cur || cur.el !== el) start(el, e); return; }
      if (cur) finish(true);
    }, true);
    document.addEventListener('submit', function (e) { e.preventDefault(); }, true);
    // klik mimo otevřené mega menu ho zavře (na telefonu je uvnitř navigace, ta zůstává)
    document.addEventListener('mousedown', function (e) {
      if (!megaOpen() || narrow() || e.target.closest('.ke-ui')) return;
      var box = trig.parentElement;
      if (box.contains(e.target) || (cur && box.contains(cur.el))) return;
      setMega(false);
    });
    // Esc bez rozepsaného textu a dialogu zavře mega menu, pak navigaci na telefonu
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape' || e.defaultPrevented || cur || dlgEl) return;
      if (megaOpen()) { e.preventDefault(); setMega(false); } else if (navOpen()) { e.preventDefault(); setNav(false); }
    });

    document.addEventListener('keydown', function (e) {
      if (!cur || dlgEl || (e.target.closest && e.target.closest('.ke-ui'))) return;     // psaní v panelu (SEO) nepatří bloku
      var k = e.key;
      if (k === 'Escape') { e.preventDefault(); finish(false); }
      else if (k === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); finish(true); }
      else if (k === 'Enter') {
        // nový blok ani <br> se nevytváří: Chrome po Shift+Enter a Backspace maže
        // sousední mezeru (spike A, 136 ze 619 případů)
        e.preventDefault();
      } else if (k === ' ' && cur.el.closest('summary, button')) { e.preventDefault(); document.execCommand('insertText', false, ' '); }
      else if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 'k') { e.preventDefault(); makeLink(); }
    }, true);
    document.addEventListener('keyup', function (e) {
      if (e.key === ' ' && cur && cur.el.closest('summary, button')) e.preventDefault();
    }, true);

    // Vložení vždy jako prostý text na jeden řádek; přetažení do bloku se nepustí.
    document.addEventListener('paste', function (e) {
      if (!cur || !cur.el.contains(e.target)) return;
      e.preventDefault();
      var t = (e.clipboardData || window.clipboardData).getData('text/plain') || '';
      document.execCommand('insertText', false, t.replace(/\s*\r?\n\s*/g, ' '));
    }, true);
    document.addEventListener('drop', function (e) { if (cur && cur.el.contains(e.target)) e.preventDefault(); }, true);
    document.addEventListener('input', function (e) {
      if (cur && cur.el.contains(e.target)) store(draftKey(cur.el), cur.el.innerHTML);
    }, true);
  }

  /* ── Další texty stránky (B7, prototyp prototype/cms-b7, varianta A) ──
     Texty, které na stránce nejsou blok: alt, title, aria-label, placeholder a text v SVG (prvky
     s data-ke-a z view.edit_view: pořadí, data-ke-an jména textů, data-ke-alock proč je uživatel
     nezmění, data-ke-apart sdílený soubor menu nebo patičky) a skryté bloky. Panel v zásuvce má
     všechno, klik na obrázek, pole formuláře nebo text v SVG otevře popover u prvku. Ukládá
     /api/attrs (apply.save_attrs) prostým textem, sdílený text potvrdí dialog jako blok menu (B6).
     Skryté bloky měří editor v prohlížeči: Ukázat je odkryje na místě (display předků z pravidel
     stylů, které platí na druhé šířce) a otevře k úpravě jako každý blok. */
  var XT = '#text';
  var XLAB = {alt: 'Popisek obrázku', title: 'Nápověda po najetí myší', 'aria-label': 'Popisek pro čtečku obrazovky',
    placeholder: 'Zástupný text pole'};
  XLAB[XT] = 'Text v grafu';
  var XGROUPS = [
    {id: 'alt', t: 'Popisky obrázků', s: 'Obrázky', icon: 'image', hint: 'Alt přečte vyhledávač a čtečka obrazovky, ukáže se i místo obrázku, který se nenačte.'},
    {id: 'aria-label', t: 'Popisky pro čtečky obrazovky', s: 'Čtečky', icon: 'accessibility', hint: 'Na stránce vidět nejsou. Čtečka je přečte u ikon, tlačítek, navigace a sekcí.'},
    {id: 'title', t: 'Nápovědy po najetí myší', s: 'Nápovědy', icon: 'message-square-text', hint: 'Ukážou se, když návštěvník na prvek najede myší.'},
    {id: 'placeholder', t: 'Zástupné texty polí', s: 'Pole', icon: 'text-cursor-input', hint: 'Šedý text v prázdném poli formuláře.'},
    {id: 'svg', t: 'Texty v grafech (SVG)', s: 'Grafy', icon: 'chart-line', hint: 'Popisky os a řad v grafu, který je ve stránce jako SVG.'},
    {id: 'hidden', t: 'Skryté bloky', s: 'Skryté', icon: 'eye-off', hint: 'Texty stránky, které teď nejsou vidět. Ukázat je odkryje na místě a upravíš je klikem jako každý jiný text.'}
  ];
  var XHK = {
    skript: ['file-code', 'Ukáže ho skript webu'],
    stav: ['mouse-pointer-click', 'Jen v jiném stavu'],
    pocitac: ['monitor', 'Jen na počítači'],
    telefon: ['smartphone', 'Jen na telefonu']
  };
  var xItems = [], xFilter = '', xFocus = null, xPopEl = null, xBusy = false, xShown = [], xHlEl = null;
  var xMark = null, xTags = null;
  function xnorm(s) { return String(s == null ? '' : s).split(WJ).join('').replace(/\s+/g, ' ').trim(); }
  function xGroup(it) { return it.a === XT ? 'svg' : it.a; }
  function xLocked(it) { return !!it.lock; }
  // texty z anotace stránky; znovu po uložení bloku (obrázek v bloku je nový prvek), rozepsané hodnoty zůstanou
  function xRead() {
    var prev = {};
    xItems.forEach(function (it) { if (it.val !== it.v) prev[it.i + '|' + it.a] = it.val; });
    xItems = [];
    $$('[data-ke-a]').forEach(function (el) {
      (el.getAttribute('data-ke-an') || '').split(' ').forEach(function (a) {
        if (!a) return;
        var v = xnorm(a === XT ? el.textContent : el.getAttribute(a)), key = el.getAttribute('data-ke-a') + '|' + a;
        xItems.push({k: xItems.length, i: +el.getAttribute('data-ke-a'), a: a, el: el, v: v, val: key in prev ? prev[key] : v,
          lock: el.getAttribute('data-ke-alock') || '', part: el.getAttribute('data-ke-apart') || ''});
      });
    });
    if (xPopEl) {
      var its = xItems.filter(function (it) { return it.i === xPopEl.its[0].i; });
      if (its.length) { xPopEl.el = its[0].el; xPopEl.its = its; } else xClosePop(false);
    }
  }
  if (MODE === 'edit' && bar) xRead();
  function xPartWhat(rel) { var r = REGIONS.filter(function (x) { return x.rel === rel; })[0]; return r && r.kind === 'footer' ? 'patičky' : 'menu'; }
  function xClip(t, n) { return t.length > n ? t.slice(0, n - 1).trim() + '…' : t; }
  // kde na stránce: menu, patička, nebo nadpis sekce
  function xWhere(el) {
    var r = regionOf(el);
    if (r) return r.kind === 'footer' ? 'Patička' : 'Menu';
    var s = el.closest('section, dialog') || el.closest('main, body');
    var h = s && ($('h1, h2', s) || $('h3', s));
    if (h && xnorm(h.textContent)) return xClip(xnorm(h.textContent), 46);
    if (s && s.getAttribute('aria-label')) return xClip(xnorm(s.getAttribute('aria-label')), 46);
    if (el.closest('nav[aria-label]')) return 'Navigace';
    return 'Stránka';
  }
  function xName(el) {
    var t = el.tagName.toLowerCase();
    var m = {img: 'obrázek', text: 'text v grafu', tspan: 'text v grafu', svg: 'graf', nav: 'navigace', button: 'tlačítko',
      a: 'odkaz', section: 'sekce', input: 'pole', textarea: 'pole na zprávu', dialog: 'okno', video: 'video', iframe: 'vložené okno'};
    if (m[t]) return m[t];
    if (el.getAttribute('role') === 'img' || $('svg', el)) return 'ikona';
    return 'prvek';
  }
  function xVisible(el) {
    if (!el.getClientRects().length) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    var cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && +cs.opacity !== 0;
  }

  /* skryté bloky: proč a jak je odkrýt (pravidla stylů stránky, ze všech @media) */
  var xRules = null, xKinds = new WeakMap();
  function xRulesAll() {
    if (xRules) return xRules;
    xRules = [];
    var walk = function (list, media) {
      for (var i = 0; i < list.length; i++) {
        var r = list[i];
        if (r.selectorText !== undefined && r.style) { xRules.push({sel: r.selectorText, st: r.style, media: media}); continue; }
        if (!r.cssRules) continue;
        var m = r.media && r.media.mediaText;
        walk(r.cssRules, m ? (media ? media + ' and ' + m : m) : media);
      }
    };
    Array.prototype.forEach.call(document.styleSheets, function (s) {
      var list = null;
      try { list = s.cssRules; } catch (e) { /* styly z cizího původu (písma) */ }
      if (list) walk(list, '');
    });
    return xRules;
  }
  function xMatches(el, sel) { try { return el.matches(sel); } catch (e) { return false; } }
  function xMediaOn(m) { try { return !m || matchMedia(m).matches; } catch (e) { return false; } }
  // prvek, který blok skrývá (hidden, display: none, visibility, průhlednost), nebo null
  function xHider(el) {
    for (var x = el; x && x !== document.body; x = x.parentElement) {
      if (x.hasAttribute('hidden')) return x;
      var cs = getComputedStyle(x);
      if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return x;
    }
    return null;
  }
  // důvod: atribut hidden (skript webu), pravidlo v @media pro úzké nebo široké okno, jinak jiný stav
  function xKind(h) {
    if (xKinds.has(h)) return xKinds.get(h);
    var k = 'stav', media = null;
    for (var x = h; x && x !== document.body; x = x.parentElement) if (x.hasAttribute('hidden')) k = 'skript';
    if (k !== 'skript') {
      xRulesAll().forEach(function (r) {
        var st = r.st;
        if ((st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') && xMediaOn(r.media) && xMatches(h, r.sel)) media = r.media;
      });
      if (media) k = /max-width/.test(media) ? 'pocitac' : /min-width/.test(media) ? 'telefon' : 'stav';
    }
    xKinds.set(h, k);
    return k;
  }
  // display, který prvku dává poslední pravidlo stylů s jinou hodnotou než none (třeba na druhé šířce)
  function xDisplay(y) {
    var d = '';
    xRulesAll().forEach(function (r) { var v = r.st.display; if (v && v !== 'none' && xMatches(y, r.sel)) d = v; });
    return d || 'revert';
  }
  function xInMenu(b) { return (nav && nav.contains(b)) || (trig && trig.parentElement && trig.parentElement.contains(b)); }
  // bloky pod jedním skrytým obalem (štítek u fotky) jsou jeden řádek a odkryjí se spolu
  function xHidden() {
    var out = [], by = new Map();
    $$('[data-ke]').forEach(function (b) {
      if (xInMenu(b) || !/\p{L}{2}/u.test(b.textContent)) return;   // menu otevírá editor sám (B6)
      var sh = xShown.filter(function (s) { return s.el.contains(b); })[0];
      var h = sh ? sh.el : xVisible(b) ? null : xHider(b);
      if (!h) return;
      var g = by.get(h);
      if (!g) { g = {n: b.dataset.ke, el: h, blocks: [], kind: sh ? sh.kind : xKind(h)}; by.set(h, g); out.push(g); }
      g.blocks.push(b);
    });
    out.forEach(function (g) {
      g.part = g.blocks[0].dataset.kePart || '';
      g.text = g.blocks.map(function (b) { return xnorm(b.textContent); }).join(' · ');
    });
    return out;
  }
  // řádky panelu: stejný text se stejným důvodem (15× Zkopírováno) je jeden řádek s počtem
  function xHiddenRows() {
    var rows = [], seen = {};
    xHidden().forEach(function (h) {
      var k = h.kind + '|' + h.text;
      if (seen[k]) { seen[k].same.push(h); return; }
      h.same = [h];
      seen[k] = h;
      rows.push(h);
    });
    return rows;
  }

  /* ── panel ── */
  function xPanelEl() { return openDrawer === 'extra' ? sheetEl : null; }
  function xRows(g) { return xItems.filter(function (it) { return xGroup(it) === g; }); }
  function xCounts() {
    var c = {};
    XGROUPS.forEach(function (g) { c[g.id] = g.id === 'hidden' ? xHiddenRows().length : xRows(g.id).length; });
    return c;
  }
  function xThumb(it) {
    if (it.el.tagName === 'IMG') return '<img class="k-thumb k-thumb--sm ke-xt-thumb" src="' + esc(it.el.currentSrc || it.el.getAttribute('src')) + '" alt="">';
    var g = XGROUPS.filter(function (x) { return x.id === xGroup(it); })[0];
    return '<span class="k-thumb k-thumb--sm k-thumb--empty ke-xt-thumb">' + kic(g ? g.icon : 'type') + '</span>';
  }
  function xSharedNote(rel) { return 'Sdílený text ' + xPartWhat(rel) + ', platí na ' + plural(PARTS[rel] || 0, 'stránce', 'stránkách', 'stránkách'); }
  function xRowHtml(it) {
    var lock = xLocked(it), changed = it.val !== it.v;
    return '<div class="ke-xt-row' + (lock ? ' ke-xt-row--lock' : '') + (changed ? ' ke-xt-row--changed' : '') + '" data-k="' + it.k + '"'
      + (it.part ? ' data-part="' + esc(it.part) + '"' : '') + '>'
      + xThumb(it)
      + '<div class="ke-xt-main"><div class="ke-xt-where">' + (lock ? kic('lock', 12) : '') + '<span>' + esc(xName(it.el) + ' · ' + xWhere(it.el)) + '</span></div>'
      + '<textarea class="k-input ke-xt-field" rows="' + (it.val.length > 52 ? 2 : 1) + '" data-k="' + it.k + '" aria-label="' + esc(XLAB[it.a] + ': ' + xWhere(it.el)) + '"'
      + (lock ? ' readonly' : '') + '>' + esc(it.val) + '</textarea>'
      + (it.part && !lock ? '<div class="ke-xt-note">' + kic('layers', 12) + '<span>' + esc(xSharedNote(it.part)) + '</span></div>' : '')
      + '<div class="ke-xt-was"' + (changed ? '' : ' hidden') + '><span>Původně: „' + esc(it.v) + '“</span><button class="k-link" type="button" data-undo-k="' + it.k + '">Vrátit</button></div>'
      + '</div>'
      + '<button class="k-ibtn ke-xt-loc" type="button" data-loc="' + it.k + '" aria-label="Ukázat na stránce" title="Ukázat na stránce">' + kic('locate') + '</button></div>';
  }
  function xHiddenRow(h) {
    var k = XHK[h.kind];
    return '<div class="ke-xt-row ke-xt-row--hid" data-n="' + esc(h.n) + '">'
      + '<span class="k-thumb k-thumb--sm k-thumb--empty ke-xt-thumb">' + kic(k[0]) + '</span>'
      + '<div class="ke-xt-main"><div class="ke-xt-where"><span>' + esc(xWhere(h.el)) + '</span></div>'
      + '<div class="ke-xt-hidtext">' + esc(xClip(h.text, 140)) + '</div>'
      + '<div class="ke-xt-hidmeta"><span class="k-chip">' + kic(k[0]) + esc(k[1]) + '</span>'
      + (h.same.length > 1 ? '<span class="k-chip k-chip--outline">' + h.same.length + '× na stránce</span>' : '')
      + (h.part ? '<span class="k-chip k-chip--outline">' + kic('layers') + 'Sdílená ' + (xPartWhat(h.part) === 'patičky' ? 'patička' : 'lišta') + '</span>' : '')
      + '</div>'
      + (h.same.length > 1 ? '<div class="ke-xt-note">' + kic('info', 12) + '<span>Každý výskyt je samostatný blok, tady se otevře první. Všechny najednou změní Najít a nahradit.</span></div>' : '')
      + '</div>'
      + '<button class="k-btn k-btn--secondary k-btn--sm ke-xt-show" type="button" data-show="' + esc(h.n) + '">' + kic('eye') + 'Ukázat a upravit</button></div>';
  }
  function xGroupHtml(g) {
    if (g.id === 'hidden') {
      var hb = xHiddenRows();
      if (!hb.length) return '';
      return '<section class="ke-xt-group" data-g="hidden"><div class="ke-xt-group__head"><p class="ke-h3">' + kic(g.icon, 14) + esc(g.t) + ' <span class="ke-cnt">' + hb.length + '</span></p>'
        + (hb.length > 1 ? '<button class="k-btn k-btn--ghost k-btn--sm" type="button" data-showall>' + kic('eye') + 'Ukázat všechny</button>' : '') + '</div>'
        + '<p class="ke-hint">' + esc(g.hint) + '</p>' + hb.map(xHiddenRow).join('') + '</section>';
    }
    var all = xRows(g.id);
    if (!all.length) return '';
    var open = all.filter(function (it) { return !xLocked(it); }), locked = all.filter(xLocked), byWhy = {}, whys = [];
    locked.forEach(function (it) { if (!byWhy[it.lock]) { byWhy[it.lock] = []; whys.push(it.lock); } byWhy[it.lock].push(it); });
    var showLocked = xFocus !== null && locked.some(function (it) { return it.k === xFocus; });
    return '<section class="ke-xt-group" data-g="' + esc(g.id) + '"><div class="ke-xt-group__head"><p class="ke-h3">' + kic(g.icon, 14) + esc(g.t) + ' <span class="ke-cnt">' + all.length + '</span></p></div>'
      + '<p class="ke-hint">' + esc(g.hint) + '</p>' + open.map(xRowHtml).join('')
      + (locked.length ? '<details class="ke-xt-locked"' + (showLocked || !open.length ? ' open' : '') + '><summary>' + kic('lock', 14) + 'Zamčené <span class="ke-cnt">' + locked.length + '</span>' + kic('chevron-down', 14) + '</summary>'
        + whys.map(function (w) { return '<p class="ke-xt-why">' + esc(w) + '</p>' + byWhy[w].map(xRowHtml).join(''); }).join('') + '</details>' : '')
      + '</section>';
  }
  function xPanel(o) {
    o = o || {};
    if (o.filter !== undefined) xFilter = o.filter;
    xFocus = o.focus !== undefined ? o.focus : null;
    var a = xPanelEl(), body = a ? $('.k-drawer__body', a) : sheet('Další texty stránky', {name: 'extra', cls: 'ke-sheet k-drawer--md ke-xt-sheet', onClose: xOnClose});
    var top = body.scrollTop;
    a = body.parentElement;
    $('#keExtra').setAttribute('aria-pressed', 'true');
    var c = xCounts(), total = 0;
    XGROUPS.forEach(function (g) { total += c[g.id]; });
    if (xFilter && !c[xFilter]) xFilter = '';
    var seg = '<div class="k-seg k-seg--sm ke-xt-seg" role="radiogroup" aria-label="Druh textu"><button class="k-seg__opt" type="button" role="radio" aria-checked="' + (xFilter === '') + '" data-f="">Vše <span class="k-seg__count">' + total + '</span></button>'
      + XGROUPS.filter(function (g) { return c[g.id]; }).map(function (g) {
        return '<button class="k-seg__opt" type="button" role="radio" aria-checked="' + (xFilter === g.id) + '" data-f="' + esc(g.id) + '">' + esc(g.s) + ' <span class="k-seg__count">' + c[g.id] + '</span></button>';
      }).join('') + '</div>';
    var notice = KIND === 'generated'
      ? '<div class="k-notice">' + kic('info') + '<div class="k-notice__body"><span><b>Generovaná stránka:</b> vlastní texty v atributech má generátor stránek, tady jsou jen ke čtení. Sdílené menu, patičku a skryté bloky upravíš jako jinde.</span></div></div>' : '';
    body.innerHTML = total ? '<p class="ke-hint">Texty, které na stránce nejsou blok: popisky obrázků, popisky pro čtečky, nápovědy, zástupné texty polí'
      + (c.svg ? ', texty v grafech' : '') + ' a skryté bloky. Citace recenzí tu nejsou, zůstávají zamčené.</p>'
      + notice + seg + XGROUPS.filter(function (g) { return !xFilter || xFilter === g.id; }).map(xGroupHtml).join('')
      : '<div class="ke-empty">Na stránce nejsou žádné další texty.</div>';
    var foot = $('.ke-xt-foot', a);
    if (!foot) {
      foot = document.createElement('div');
      foot.className = 'k-drawer__foot ke-xt-foot';
      a.appendChild(foot);
    }
    xFoot();
    $$('.ke-xt-field', body).forEach(xGrow);
    body.scrollTop = top;
    if (xFocus !== null) {
      var row = $('.ke-xt-row[data-k="' + xFocus + '"]', body);
      if (row) {
        row.scrollIntoView({block: 'center'});
        row.classList.add('ke-xt-row--focus');
        var f = $('.ke-xt-field', row);
        if (f && !f.readOnly) { f.focus({preventScroll: true}); f.setSelectionRange(f.value.length, f.value.length); }
        xHl(xItems[xFocus].el, true);
      }
    }
  }
  function xOnClose() { var b = $('#keExtra'); if (b) b.removeAttribute('aria-pressed'); xHl(null); }
  function xPending() { return xItems.filter(function (it) { return !xLocked(it) && it.val !== it.v; }); }
  function xFoot() {
    var a = xPanelEl(), foot = a && $('.ke-xt-foot', a);
    if (!foot) return;
    var p = xPending();
    foot.innerHTML = '<span class="ke-xt-foot__n">' + (p.length ? plural(p.length, 'změna', 'změny', 'změn') + ' k uložení' : 'Bez změn') + '</span>'
      + '<button class="k-btn k-btn--primary" type="button" data-xsave' + (p.length && !xBusy ? '' : ' disabled') + '>Uložit <span class="k-kbd">Ctrl+Enter</span></button>';
  }
  function xGrow(t) { if (t.tagName !== 'TEXTAREA') return; t.style.height = 'auto'; t.style.height = t.scrollHeight + 2 + 'px'; }

  /* rám prvku (řádek pod myší, řádek s fokusem, prvek s popoverem): překryv nad stránkou, obrys na
     prvku by ořízl obal s overflow: hidden (fotka v O mně) */
  function xHl(el, keep) {
    xHlEl = el && xVisible(el) ? el : null;
    if (!keep && el === null) xFocus = null;
    xDrawMark();
  }
  function xDrawMark() {
    var el = (xPopEl && xPopEl.el) || xHlEl;
    if (!xMark) {
      if (!el) return;
      xMark = document.createElement('div');
      xMark.className = 'ke-region ke-ui ke-xt-mark';
      document.body.appendChild(xMark);
    }
    if (!el || !xVisible(el)) { xMark.hidden = true; return; }
    var r = el.getBoundingClientRect(), pad = el.tagName.toLowerCase() === 'text' || el.tagName.toLowerCase() === 'tspan' ? 3 : 4;
    var top = Math.max(48, r.top - pad);
    xMark.hidden = r.bottom < 48 || r.top > innerHeight;
    xMark.style.left = r.left - pad + 'px';
    xMark.style.top = top + 'px';
    xMark.style.width = r.width + pad * 2 + 'px';
    xMark.style.height = Math.max(0, r.bottom + pad - top) + 'px';
  }
  function xLocate(el) {
    if (!xVisible(el)) {
      toast('Prvek je na téhle šířce stránky skrytý (' + xName(el) + ' · ' + xWhere(el) + '). Text upravíš tady v panelu.', 'info');
      return;
    }
    el.scrollIntoView({block: 'center', behavior: 'instant'});
    xHl(el);
  }

  if (MODE === 'edit' && bar) {
    $('#keExtra').onclick = function () { if (xPanelEl()) closeDrawer(); else xPanel({}); };
    document.addEventListener('input', function (e) {
      var t = e.target;
      if (!t.classList || !t.classList.contains('ke-xt-field')) return;
      var it = xItems[+t.dataset.k];
      it.val = t.value;
      xGrow(t);
      var row = t.closest('.ke-xt-row');
      if (row) {
        row.classList.toggle('ke-xt-row--changed', it.val !== it.v);
        var was = $('.ke-xt-was', row);
        if (was) was.hidden = it.val === it.v;
      }
      $$('.ke-xt-field[data-k="' + it.k + '"]').forEach(function (o) { if (o !== t) { o.value = it.val; xGrow(o); } });
      xFoot();
    });
    document.addEventListener('click', function (e) {
      var p = xPanelEl();
      if (!p || !p.contains(e.target)) return;
      var f = e.target.closest('[data-f]');
      if (f) { xPanel({filter: f.dataset.f}); return; }
      var l = e.target.closest('[data-loc]');
      if (l) { xLocate(xItems[+l.dataset.loc].el); return; }
      var u = e.target.closest('[data-undo-k]');
      if (u) { var it = xItems[+u.dataset.undoK]; it.val = it.v; xPanel({}); return; }
      var s = e.target.closest('[data-show]');
      if (s) { xShowHidden(s.dataset.show, true); return; }
      if (e.target.closest('[data-showall]')) { xShowAll(); return; }
      if (e.target.closest('[data-xsave]')) xSave(xPending());
    });
    // Enter v poli nic nedělá (text je na jeden řádek), Ctrl+Enter uloží všechny změny panelu
    document.addEventListener('keydown', function (e) {
      var p = xPanelEl();
      if (!p || !p.contains(e.target) || e.key !== 'Enter' || dlgEl) return;
      if (!(e.ctrlKey || e.metaKey)) { if (e.target.classList.contains('ke-xt-field')) e.preventDefault(); return; }
      e.preventDefault();
      xSave(xPending());
    });
    // řádek pod myší ukáže prvek na stránce
    document.addEventListener('mouseover', function (e) {
      var p = xPanelEl();
      if (!p || !p.contains(e.target)) return;
      var r = e.target.closest('.ke-xt-row');
      if (!r) { if (xFocus === null) xHl(null); return; }
      xHl(r.dataset.n ? $('[data-ke="' + r.dataset.n + '"]') : xItems[+r.dataset.k].el);
    });
  }

  /* ── uložení: /api/attrs, sdílený text menu a patičky potvrdí dialog jako blok v B6 ── */
  function xSave(list, after, token, owners) {
    if (!list.length) { toast('Beze změny, nic jsem nezapsal.', 'info'); return; }
    if (xBusy) return;
    if (cur) { toast('Nejdřív ulož (Ctrl+Enter), nebo zruš (Esc) rozepsaný text.', 'info'); return; }
    var shared = list.filter(function (it) { return it.part; });
    if (shared.length && !token) toast('Zkouším změnu na ' + plural(PARTS[shared[0].part] || 0, 'stránce', 'stránkách', 'stránkách') + ', chvíli to trvá.', 'info');
    xBusy = true;
    xFoot();
    api('POST', '/api/attrs', {slug: SLUG, changes: list.map(function (it) { return {i: it.i, a: it.a, old: it.v, 'new': it.val}; }),
      confirm: token || null, owners: !!owners}).then(function (r) {
      xBusy = false;
      var b = r.body;
      if (r.ok && b.confirm) {
        dropToast();
        xFoot();
        if (b.pages) { xConfirmShared(list, b, after); return; }
        ask({title: b.message, body: listHtml(b.details), yes: 'Uložit i tak', no: 'Upravit dál',
          done: function (yes) { if (yes) xSave(list, after, b.confirm); }});
        return;
      }
      if (r.ok && b.unchanged) {
        list.forEach(function (it) { it.val = it.v; });
        toast('Jako prostý text je to beze změny, nic jsem nezapsal.', 'info');
        if (xPanelEl()) xPanel({filter: xFilter});
        if (after) after();
        return;
      }
      if (r.ok && b.ok) { xSaved(list, b); if (after) after(); return; }
      xFoot();
      if (b.code === 'stale') { toast(b.error, 'err'); return; }
      if (b.details && b.details.length) { failDialog(b); return; }
      toast(b.error || 'Uložení selhalo.', 'err');
    }, function () { xBusy = false; xFoot(); offline(); });
  }
  function xSaved(list, b) {
    (b.values || []).forEach(function (v) {
      xItems.forEach(function (it) {
        if (it.i !== v.i || it.a !== v.a) return;
        if (it.a === XT) it.el.textContent = v.v; else it.el.setAttribute(it.a, v.v);
        it.v = it.val = v.v;
      });
    });
    // obrázek v bloku: blok má nový otisk, další úprava bloku ho potřebuje
    (b.blocks || []).forEach(function (x) {
      var el = $('[data-ke="' + x.n + '"]');
      if (!el) return;
      el.dataset.keH = x.h;
      orig[x.n] = el.innerHTML;
    });
    setMeta('ke-page', b.page);
    setCount(b.count);
    toast(b.part ? 'Uloženo do ' + b.files.join(', ') + ', přestaveno ' + plural(b.pages.length, 'stránka', 'stránky', 'stránek') + '. Na web půjde s Publikovat.'
      : 'Uloženo do ' + b.files.join(', ') + '. Na web půjde s Publikovat.', 'ok');
    if (xPanelEl()) xPanel({filter: xFilter, focus: xFocus});
  }
  function xConfirmShared(list, b, after) {
    var n = b.pages.length, by = {handbuilt: [], generated: [], owner: []}, part = b.parts[0];
    b.pages.forEach(function (p) { (by[p.kind] || (by[p.kind] = [])).push(p.slug); });
    var li = function (kind) {
      return function (s) {
        var p = PAGES[s];
        return '<li' + (p && p.name ? ' title="' + esc(p.name) + '"' : '') + '>' + esc(slugText(s))
          + (kind === 'owner' ? ' <span>owner</span>' : kind === 'generated' ? ' <span>generovaná</span>' : '') + '</li>';
      };
    };
    var byKey = {};
    list.forEach(function (it) { byKey[it.i + '|' + it.a] = it; });
    var shared = b.changes.filter(function (c) { return c.part; }), rest = b.changes.length - shared.length;
    var body = '<div class="ke-changes">' + shared.map(function (c) {
      var it = byKey[c.i + '|' + c.a];
      var where = it ? xWhere(it.el) + ' · ' + xName(it.el) + ' · ' + XLAB[c.a].toLowerCase() : XLAB[c.a];
      return '<div class="ke-diff"><span class="ke-diff__where">' + esc(where) + '</span>' + wdiff(c.old, c['new']) + '</div>';
    }).join('') + '</div>'
      + '<div class="ke-scope"><div class="ke-scope__head"><span class="ke-scope__n">Změní se ' + plural(n, 'stránka', 'stránky', 'stránek') + '</span>'
      + '<span class="ke-scope__kinds">' + plural(by.handbuilt.length, 'ruční', 'ruční', 'ručních') + ', '
      + plural(by.generated.length, 'generovaná', 'generované', 'generovaných') + ', ' + by.owner.length + ' owner</span></div>'
      + '<details class="ke-pages"><summary>Všechny stránky ' + kic('chevron-down', 14) + '</summary><ul class="ke-pages__grid">'
      + by.owner.map(li('owner')).join('') + by.handbuilt.map(li('handbuilt')).join('') + by.generated.map(li('generated')).join('') + '</ul></details></div>'
      + listHtml(b.details) + ownersHtml(b.owners)
      + (rest ? '<div class="ke-check-note">' + kic('file-text', 14) + '<span>S nimi se uloží ' + plural(rest, 'text', 'texty', 'textů') + ' jen na téhle stránce (' + esc(PATH) + ').</span></div>' : '')
      + '<div class="ke-check-note">' + kic('circle-check', 14) + '<span>Po uložení se stránky přestaví a kontrola webu ověří, že menu a patička jsou na všech stránkách stejné jako šablona.</span></div>';
    var what = b.parts.length > 1 ? 'menu a patičky' : xPartWhat(part);
    var dlg = ask({title: 'Uložit změnu ' + what + ' na ' + plural(n, 'stránku', 'stránky', 'stránek') + '?',
      desc: 'Sdílený text z <span class="k-mono">' + b.parts.map(esc).join(', ') + '</span>. Na web půjde až s Publikovat, do té doby ho uvidíš jen v editoru.',
      body: body, yes: 'Uložit na ' + plural(n, 'stránku', 'stránky', 'stránek'), yesIcon: 'check', no: 'Upravit dál',
      note: 'Vrátit jde ve Změnách.', cls: 'k-dialog--lg',
      done: function (yes) { if (yes) xSave(list, after, b.confirm, !!(b.owners && b.owners.length)); }});
    ownersGate(dlg);
  }

  /* ── klik na stránce: obrázek, pole formuláře, text v SVG ── */
  function xTarget(t) {
    if (MODE !== 'edit' || !bar || !t || !t.closest || t.closest('.ke-ui')) return null;
    var el = t.closest('img[data-ke-a], input[data-ke-a], textarea[data-ke-a], text[data-ke-a], tspan[data-ke-a]');
    if (!el) return null;
    var its = xItems.filter(function (it) { return it.el === el; });
    return its.length ? {el: el, its: its} : null;
  }
  // tooltip u cíle: co klik udělá, nebo proč je text zamčený (kreslí ho tooltip zámků výš)
  function xTip(t) {
    var x = xTarget(t);
    if (!x || (xPopEl && xPopEl.el === x.el)) return null;
    var it = x.its[0], lock = x.its.every(xLocked);
    if (lock) return {text: it.lock, lock: true};
    return {lock: false, text: it.a === XT ? 'Klikni a přepiš text v grafu.'
      : 'Klikni a uprav ' + (it.a === 'alt' ? 'popisek obrázku (alt)' : it.a === 'placeholder' ? 'zástupný text pole' : XLAB[it.a].toLowerCase()) + '.'
        + (it.part ? ' ' + xSharedNote(it.part) + '.' : '')};
  }
  if (MODE === 'edit' && bar) window.addEventListener('mousedown', function (e) {
    var x = xTarget(e.target);
    if (x && (x.el.tagName === 'INPUT' || x.el.tagName === 'TEXTAREA')) e.preventDefault();     // pole se v úpravě nefokusuje
  }, true);

  /* popover u prvku: pole textů prvku, Uložit a Zrušit, zdroj a poznámky jako plovoucí lišta bloku */
  function xOpen(el, its) {
    xClosePop(false);
    if (tip) { tip.remove(); tip = null; }
    var lock = its.every(xLocked), part = its.filter(function (it) { return it.part; })[0];
    var f = document.createElement('div');
    f.className = 'ke-float ke-ui ke-xt-pop';
    f.setAttribute('role', 'dialog');
    f.setAttribute('aria-label', XLAB[its[0].a]);
    var g = function (a) { return XGROUPS.filter(function (x) { return x.id === (a === XT ? 'svg' : a); })[0]; };
    f.innerHTML = '<div class="ke-xt-pop__fields">' + its.map(function (it) {
      var ro = xLocked(it);
      var field = it.a === XT
        ? '<input class="k-input k-input--sm ke-xt-field" data-k="' + it.k + '" value="' + esc(it.val) + '"' + (ro ? ' readonly' : '') + '>'
        : '<textarea class="k-input ke-xt-field" rows="' + (it.val.length > 60 ? 2 : 1) + '" data-k="' + it.k + '"' + (ro ? ' readonly' : '') + '>' + esc(it.val) + '</textarea>';
      return '<label class="ke-xt-pop__lab">' + kic(g(it.a).icon, 14) + '<span>' + esc(XLAB[it.a]) + ' <span class="k-mono">' + esc(it.a === XT ? '<' + it.el.tagName.toLowerCase() + '>' : it.a) + '</span></span></label>' + field;
    }).join('') + '</div>'
      + '<div class="ke-float__row">'
      + (lock ? '<button class="k-btn k-btn--secondary k-btn--sm" type="button" data-p="cancel">Zavřít <span class="k-kbd">Esc</span></button>'
        : '<button class="k-btn k-btn--primary k-btn--sm" type="button" data-p="save">Uložit <span class="k-kbd">Ctrl+Enter</span></button>'
        + '<button class="k-btn k-btn--secondary k-btn--sm" type="button" data-p="cancel">Zrušit <span class="k-kbd">Esc</span></button>')
      + '<span class="ke-xt-sp"></span><button class="k-btn k-btn--ghost k-btn--sm" type="button" data-p="panel">' + kic('scan-text') + 'Další texty</button></div>'
      + '<div class="ke-float__src">' + kic('file-pen-line', 14) + '<span class="k-mono">' + esc(part ? part.part : PATH) + '</span></div>'
      + (part && !lock ? '<div class="ke-float__note">' + kic('layers') + '<span><b>Sdílený text ' + esc(xPartWhat(part.part)) + ':</b> uloží se na '
        + plural(PARTS[part.part] || 0, 'stránce', 'stránkách', 'stránkách') + ' webu' + (hasOwners() ? ', i na owner stránkách' : '') + '. Potvrdíš to při uložení.</span></div>' : '')
      + (lock ? '<div class="ke-float__note">' + kic('lock') + '<span>' + esc(its[0].lock) + '</span></div>' : '');
    document.body.appendChild(f);
    xPopEl = {el: el, f: f, its: its};
    xPlacePop();
    xDrawMark();
    var first = $('.ke-xt-field:not([readonly])', f);
    if (first) { first.focus({preventScroll: true}); first.setSelectionRange(first.value.length, first.value.length); }
    else $('[data-p]', f).focus({preventScroll: true});
    $$('.ke-xt-field', f).forEach(xGrow);
    f.addEventListener('click', function (e) {
      var b = e.target.closest('[data-p]');
      if (!b) return;
      if (b.dataset.p === 'save') xPopSave();
      else if (b.dataset.p === 'cancel') xClosePop(true);
      else { var k = its[0].k; xClosePop(false); xPanel({filter: '', focus: k}); }
    });
    f.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); xClosePop(true); }
      else if (e.key === 'Enter') { e.preventDefault(); if (e.ctrlKey || e.metaKey || e.target.tagName === 'INPUT') xPopSave(); }
    });
  }
  function xPlacePop() {
    if (!xPopEl) return;
    var f = xPopEl.f, r = xPopEl.el.getBoundingClientRect();
    var top = r.top - f.offsetHeight - 10 >= 56 ? r.top - f.offsetHeight - 10 : r.bottom + 10;
    if (top + f.offsetHeight > innerHeight - 8) top = Math.max(56, Math.min(r.top + 12, innerHeight - f.offsetHeight - 8));
    f.style.position = 'absolute';
    f.style.top = top + scrollY + 'px';
    f.style.left = Math.max(12, Math.min(r.left, innerWidth - f.offsetWidth - 12)) + scrollX + 'px';
  }
  function xPopSave() {
    if (!xPopEl) return;
    var list = xPopEl.its.filter(function (it) { return !xLocked(it) && it.val !== it.v; });
    if (!list.length) { xClosePop(false); return; }
    var p = xPopEl;
    xSave(list, function () { if (xPopEl === p) xClosePop(false); });
  }
  function xClosePop(revert) {
    if (!xPopEl) return;
    if (revert) xPopEl.its.forEach(function (it) { it.val = it.v; });
    xPopEl.f.remove();
    xPopEl = null;
    xDrawMark();
    if (xPanelEl()) xPanel({filter: xFilter});
  }
  if (MODE === 'edit' && bar) {
    // klik mimo popover: změna se uloží jako u bloku, beze změny se jen zavře
    document.addEventListener('mousedown', function (e) {
      if (!xPopEl || xPopEl.f.contains(e.target) || e.target.closest('.k-dialog, .k-toasts')) return;
      var x = xTarget(e.target);
      if (x && x.el === xPopEl.el) return;
      if (xPending().some(function (it) { return xPopEl.its.indexOf(it) >= 0; })) xPopSave(); else xClosePop(false);
    });
    addEventListener('resize', function () { xPlacePop(); xPlaceTags(); xDrawMark(); });
    addEventListener('scroll', function () { xPlacePop(); xPlaceTags(); xDrawMark(); }, true);
  }

  /* ── skryté bloky: Ukázat odkryje blok na místě s rámem a štítkem, Skrýt ho vrátí ── */
  function xReveal(el) {
    var undo = [];
    for (var x = el; x && x !== document.body; x = x.parentElement) (function (y) {
      if (y.hasAttribute('hidden')) { y.removeAttribute('hidden'); undo.push(function () { y.setAttribute('hidden', ''); }); }
      var cs = getComputedStyle(y), st = y.getAttribute('style'), touched = false;
      if (cs.display === 'none') { y.style.setProperty('display', xDisplay(y), 'important'); touched = true; }
      if (cs.visibility === 'hidden') { y.style.setProperty('visibility', 'visible', 'important'); touched = true; }
      if (+cs.opacity === 0) { y.style.setProperty('opacity', '1', 'important'); touched = true; }
      if (touched) undo.push(function () { if (st === null) y.removeAttribute('style'); else y.setAttribute('style', st); });
    })(x);
    return undo;
  }
  function xRevealGroup(h) {
    if (xShown.some(function (s) { return s.el === h.el; })) return;
    var undo = [];
    h.blocks.forEach(function (b) { undo = undo.concat(xReveal(b)); });
    h.el.classList.add('ke-xt-shown');
    xShown.push({n: h.n, el: h.el, kind: h.kind, undo: undo});
  }
  function xShowHidden(n, edit) {
    var h = xHidden().filter(function (x) { return x.n === n; })[0];
    if (!h) return;
    closeDrawer();
    xRevealGroup(h);
    h.el.scrollIntoView({block: 'center', behavior: 'instant'});
    xDrawTags();
    if (edit) setTimeout(function () { start(h.blocks[0]); xPlaceTags(); }, 60);
  }
  function xShowAll() {
    var hb = xHidden();
    closeDrawer();
    hb.forEach(xRevealGroup);
    if (hb[0]) hb[0].el.scrollIntoView({block: 'center', behavior: 'instant'});
    xDrawTags();
    toast('Odkryto ' + plural(hb.length, 'skrytý blok', 'skryté bloky', 'skrytých bloků') + '. Klikni na text a uprav ho, Skrýt je vrátí.', 'info');
  }
  function xHide(n) {
    if (cur && xShown.some(function (s) { return (n === undefined || s.n === n) && s.el.contains(cur.el); })) finish(true);
    xShown = xShown.filter(function (s) {
      if (n !== undefined && s.n !== n) return true;
      s.el.classList.remove('ke-xt-shown');
      s.undo.reverse().forEach(function (f) { f(); });
      return false;
    });
    xDrawTags();
  }
  function xDrawTags() {
    if (!xTags) {
      xTags = document.createElement('div');
      xTags.className = 'ke-xt-tags';
      document.body.appendChild(xTags);
      xTags.addEventListener('mousedown', function (e) { e.preventDefault(); });     // fokus zůstane v rozepsaném bloku
      xTags.addEventListener('click', function (e) { var b = e.target.closest('[data-hide]'); if (b) xHide(b.dataset.hide); });
    }
    xTags.innerHTML = xShown.map(function (s) {
      return '<div class="ke-region ke-ui ke-xt-region" data-tag="' + esc(s.n) + '"><div class="ke-tab">' + kic('eye-off', 14) + '<span>Skrytý blok · ' + esc(XHK[s.kind][1]) + '</span>'
        + '<button class="ke-tab__btn" type="button" data-hide="' + esc(s.n) + '">' + kic('eye-off', 14) + 'Skrýt</button></div></div>';
    }).join('');
    xPlaceTags();
  }
  // štítek jde na opačnou stranu než plovoucí lišta upravovaného bloku, ať se nepřekrývají,
  // u pravého okraje okna se zarovná doprava
  function xPlaceTags() {
    if (!xTags || !xShown.length) return;
    var fr = cur && cur.float ? cur.float.getBoundingClientRect() : null;
    xShown.forEach(function (s) {
      var t = $('[data-tag="' + s.n + '"]', xTags);
      if (!t) return;
      var r = s.el.getBoundingClientRect(), pad = 5, top = Math.max(52, r.top - pad);
      t.hidden = r.bottom < 52 || r.top > innerHeight;
      t.style.left = r.left - pad + 'px';
      t.style.top = top + 'px';
      t.style.width = r.width + pad * 2 + 'px';
      t.style.height = Math.max(0, r.bottom + pad - top) + 'px';
      var mine = fr && cur && s.el.contains(cur.el);
      var floatBelow = mine && fr.top >= r.bottom - 2, floatAbove = mine && fr.bottom <= r.top + 2;
      t.classList.toggle('ke-region--up', floatBelow || (!floatAbove && r.bottom > innerHeight - 60));
      var tab = $('.ke-tab', t);
      t.classList.toggle('ke-region--end', !!tab && r.left - pad + tab.offsetWidth > innerWidth - 8);
    });
  }
  if (MODE === 'edit' && bar) {
    new MutationObserver(function () { if (xShown.length) xPlaceTags(); })
      .observe(document.body, {attributes: true, attributeFilter: ['contenteditable'], subtree: true});
    // Esc bez rozepsaného textu, popoveru, dialogu a panelu vrátí odkryté bloky
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && xShown.length && !e.defaultPrevented && !xPopEl && !cur && !dlgEl && !sheetEl) { e.preventDefault(); xHide(); }
    });
  }

  /* ── seznam stránek Web (varianta B prototypu A4): karta Nepublikované úpravy a tabulka s filtrem ── */
  function edRow(meta, diff, undo) {
    return '<div class="w-ed"><div class="w-ed__meta">' + meta + '</div><div class="w-ed__diff w-diff">' + diff + '</div>'
      + (undo ? '<div class="w-ed__act"><button class="k-btn k-btn--secondary k-btn--sm" type="button" data-undo="' + esc(undo) + '">'
        + kic('rotate-ccw') + 'Vrátit</button></div>' : '') + '</div>';
  }
  function byWhom(t, user) { return (t ? ' · ' + esc(when(t)) : '') + (user ? ' · ' + esc(user) : ''); }
  // Stránka s úpravami (publish.changes): rozdíl proti poslední publikaci po blocích, Vrátit nejnovější úpravy
  function pendGroup(p) {
    var rows = p.items.map(function (it) {
      var what = it.kind === 'seo' ? 'SEO' : it.kind === 'other' ? 'Mimo texty' : it.tag;
      var meta = '<b>' + esc(what) + '</b>' + (it.kind === 'block' && it.note ? ' · ' + esc(it.note) : '')
        + (it.edits > 1 ? ' · ' + plural(it.edits, 'úprava', 'úpravy', 'úprav') : '') + byWhom(it.t, it.user);
      var diff = it.diffs.map(function (x) {
        return (x.label ? '<span class="w-ed__lab">' + esc(x.label) + '</span> ' : '') + opsHtml(x.ops);
      }).join('<br>') + (it.kind === 'other' ? esc(it.note) : '');
      return edRow(meta, diff, it.undo);
    }).concat(p.edits.map(function (c) {
      var tag = String(c.label || '').split(' · ').slice(1).join(' · ') || 'Úprava';
      return edRow('<b>' + esc(tag) + '</b>' + byWhom(c.t, c.user),
        c.before || c.after ? wdiff(c.before, c.after) : 'Úprava celého zdroje stránky.', c.id);
    }));
    var notes = [];
    if (p.via.length) notes.push('Mění ji i úprava sdíleného textu na ' + p.via.map(slugText).map(esc).join(', ') + '.');
    if (p['new']) notes.push('Stránka v poslední publikaci ještě není, celý text je nový.');
    if (p.replace && p.replace.length) notes.push('Mění ji i Najít a nahradit, Zahodit stránku jde až po jeho vrácení.');
    return '<div class="w-pg"><div class="w-pg__head"><div class="w-pg__name"><a class="w-pg__title" href="' + esc(pageUrl(p.slug, '?e=1')) + '">'
      + esc(p.name || slugText(p.slug)) + '</a><span class="w-pg__slug">' + esc(slugText(p.slug)) + ' · '
      + (p.count ? plural(p.count, 'úprava', 'úpravy', 'úprav') : 'jen sdílený text') + '</span></div>'
      + '<div class="w-pg__acts"><a class="k-btn k-btn--secondary k-btn--sm" href="' + esc(pageUrl(p.slug, '?e=1')) + '">' + kic('pencil') + 'Upravit</a>'
      + '<button class="k-btn k-btn--ghost k-btn--sm" type="button" data-discard="' + esc(p.slug) + '">' + kic('trash-2') + 'Zahodit úpravy stránky</button></div></div>'
      + (notes.length ? '<p class="w-pg__note">' + notes.join(' ') + '</p>' : '')
      + '<div class="w-eds">' + rows.join('') + '</div></div>';
  }
  function replaceGroup(reps) {
    return '<div class="w-pg"><div class="w-pg__head"><div class="w-pg__name"><span class="w-pg__title">Najít a nahradit, menu a patička</span>'
      + '<span class="w-pg__slug">' + plural(reps.length, 'úprava', 'úpravy', 'úprav') + ' napříč webem</span></div></div>'
      + '<div class="w-eds">' + reps.map(function (x) {
        var pages = x.pages.slice(0, 8).map(slugText).join(', ') + (x.pages.length > 8 ? ' a ' + plural(x.pages.length - 8, 'další', 'další', 'dalších') : '');
        return edRow((partLabel(x) ? '<b>' + esc(partLabel(x)) + '</b> · ' : '') + '<b>' + plural(x.pages.length, 'stránka', 'stránky', 'stránek') + '</b>' + byWhom(x.t, x.user) + ' · ' + esc(pages), swap(x.before, x.after), x.id);
      }).join('') + '</div></div>';
  }
  function pendRender() {
    var card = $('#wPend'), body = $('#wPendBody');
    api('GET', '/api/changes').then(function (r) {
      var d = r.body, pages = d.pages || [], reps = d.replaces || [];
      if (!r.ok) {
        card.hidden = false;
        body.innerHTML = '<div class="k-state k-state--row"><p class="k-state__desc">' + esc(d.error || 'Úpravy se nenačetly.') + '</p></div>';
        return;
      }
      setCount(d.count);
      card.hidden = !pages.length && !reps.length;
      if (card.hidden) return;
      var slugs = {}, first = null;
      pages.forEach(function (p) { slugs[p.slug] = 1; });
      reps.forEach(function (x) { x.pages.forEach(function (s) { slugs[s] = 1; }); });
      pages.forEach(function (p) {
        p.items.concat(p.edits).forEach(function (x) { if (x.t && (first === null || x.t < first)) first = x.t; });
      });
      reps.forEach(function (x) { if (x.t && (first === null || x.t < first)) first = x.t; });
      $('#wPendMeta').textContent = plural(d.count, 'úprava', 'úpravy', 'úprav') + ' na ' + plural(Object.keys(slugs).length, 'stránce', 'stránkách', 'stránkách')
        + (first ? ' · od ' + when(first) : '');
      body.innerHTML = (d.error ? '<div class="w-pg">' + errNotice(esc(d.error)) + '</div>' : '')
        + (reps.length ? replaceGroup(reps) : '') + pages.map(pendGroup).join('');
    }, offline);
  }
  // po Vrátit, Zahodit a nahrazení se seznam načte znovu (počty v tabulce a v sidebaru)
  function reloadWith(msg) { sess('ke-after', JSON.stringify({toast: msg})); location.reload(); }

  if (MODE === 'list') {
    var q = $('#keSearch'), only = $('#keOnly'), kind = '', rows = $$('.w-list tbody tr');
    var filter = function () {
      var t = q.value.trim().toLowerCase(), shown = 0;
      rows.forEach(function (tr) {
        var ok = (!kind || tr.dataset.kind === kind) && (!t || tr.dataset.q.indexOf(t) >= 0)
          && (!only.checked || tr.classList.contains('w-row--pend'));
        tr.hidden = !ok;
        if (ok) shown++;
      });
      $('#keNone').hidden = shown > 0;
    };
    q.addEventListener('input', filter);
    only.addEventListener('change', filter);
    q.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      var first = rows.filter(function (tr) { return !tr.hidden; })[0];
      if (first) location.href = $('.w-open', first).href;
    });
    $$('.k-seg [data-kind]').forEach(function (b) {
      b.onclick = function () {
        kind = b.dataset.kind;
        $$('.k-seg [data-kind]').forEach(function (x) { x.setAttribute('aria-checked', String(x === b)); });
        filter();
      };
    });
    $('#wPend').addEventListener('click', function (e) {
      var z = e.target.closest('[data-discard]');
      if (z) { discardPage(z.dataset.discard, null, z); return; }
      var u = e.target.closest('[data-undo]');
      if (!u) return;
      u.disabled = true;
      api('POST', '/api/undo', {id: u.dataset.undo}).then(function (r) {
        if (!r.ok) { u.disabled = false; toast(r.body.error, 'err'); return; }
        reloadWith('Vráceno.');
      }, function () { u.disabled = false; offline(); });
    });
    pendRender();
    // Publikovat z úpravy stránky vede sem s #publikovat; dialog se otevře a kotva z adresy zmizí
    var pubHash = function () {
      if (location.hash !== '#publikovat') return;
      history.replaceState(null, '', location.pathname + location.search);
      openPublish();
    };
    addEventListener('hashchange', pubHash);
    pubHash();
  }

  /* ── celý zdroj ── */
  if (MODE === 'source') {
    var ta = $('#keSrc'), out = $('#keSrcOut'), saveBtn = $('#keSrcSave');
    var saveSource = function () {
      if (saveBtn.disabled) return;
      saveBtn.disabled = true;
      api('POST', '/api/source', {slug: SLUG, content: ta.value, base: meta('ke-page')}).then(function (r) {
        saveBtn.disabled = false;
        var b = r.body;
        if (r.ok) {
          out.hidden = true;
          if (b.page) setMeta('ke-page', b.page);
          setCount(b.count);
          toast(b.unchanged ? 'Beze změny, nic jsem nezapsal.' : 'Uloženo. Úprava je mezi nepublikovanými úpravami na stránce Web.', 'ok');
          return;
        }
        out.hidden = false;
        out.innerHTML = '<b>' + esc(b.error) + '</b>' + (b.details && b.details.length
          ? '<ul>' + b.details.map(function (d) { return '<li>' + esc(d) + '</li>'; }).join('') + '</ul>' : '');
      }, function () { saveBtn.disabled = false; offline(); });
    };
    saveBtn.onclick = saveSource;
    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveSource(); }
    });
  }

  /* ── šasi nabídek: zásuvka menu na telefonu a menu ⋯ (details.k-menu) ── */
  if (SHELL) {
    // Pod 768 px je sidebar zásuvka: tlačítko menu ji otevře (fokus do navigace), závoj
    // a Esc zavřou (fokus zpět na tlačítko). Nad zlomem se zavře sama.
    var navBtn = $('.k-top__menu', SHELL);
    var setNav = function (open, back) {
      if (open) SHELL.setAttribute('data-nav-open', ''); else SHELL.removeAttribute('data-nav-open');
      navBtn.setAttribute('aria-expanded', String(open));
      if (open) {
        var first = $('.k-side [aria-current="page"]', SHELL) || $('.k-side a', SHELL);
        if (first) requestAnimationFrame(function () { first.focus(); });
      } else if (back) navBtn.focus();
    };
    $$('[data-nav-toggle]', SHELL).forEach(function (el) {
      el.addEventListener('click', function () { setNav(!SHELL.hasAttribute('data-nav-open'), true); });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && SHELL.hasAttribute('data-nav-open')) { e.preventDefault(); setNav(false, true); }
    });
    if (window.matchMedia) {
      var wide = matchMedia('(min-width: 768px)');
      var onWide = function () { if (wide.matches && SHELL.hasAttribute('data-nav-open')) setNav(false, false); };
      if (wide.addEventListener) wide.addEventListener('change', onWide);
    }

    // Menu ⋯ přes <details class="k-menu">: otevřené jedno, klik mimo, Esc, scroll a změna
    // okna ho zavřou. Plocha .k-pop má position: fixed u spouštěče, takže ji neořízne posuvný
    // obal tabulky na telefonu (nad spouštěčem, když dole nezbývá místo).
    var closeMenus = function (except) {
      $$('details.k-menu[open]').forEach(function (m) { if (m !== except) m.open = false; });
    };
    document.addEventListener('toggle', function (e) {
      var m = e.target;
      if (!m.matches || !m.matches('details.k-menu')) return;
      var pop = $('.k-pop', m), sum = $('summary', m);
      if (!pop || !sum) return;
      if (!m.open) { pop.removeAttribute('style'); return; }
      closeMenus(m);
      var r = sum.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
      var below = innerHeight - r.bottom - 14, up = h > below && r.top - 14 > below;
      var left = Math.min(Math.max(8, r.right - w), innerWidth - w - 8);
      Object.assign(pop.style, {position: 'fixed', right: 'auto', left: left + 'px',
        top: up ? 'auto' : (r.bottom + 6) + 'px', bottom: up ? (innerHeight - r.top + 6) + 'px' : 'auto'});
    }, true);
    document.addEventListener('click', function (e) {
      if (!e.target.closest || !e.target.closest('details.k-menu')) closeMenus(null);
    });
    document.addEventListener('keydown', function (e) {
      var m = e.key === 'Escape' && document.activeElement && document.activeElement.closest
        && document.activeElement.closest('details.k-menu[open]');
      if (m) { e.preventDefault(); m.open = false; $('summary', m).focus(); }
    });
    window.addEventListener('scroll', function () { closeMenus(null); }, true);
    window.addEventListener('resize', function () { closeMenus(null); });
  }

  // Nápověda „?“ (README § 6, vzor catalog.html): spouštěč .k-help s popovertarget, plocha
  // .k-pop.k-pop--tip s atributem popover. Otevřená stojí pod spouštěčem, scroll a změna okna
  // ji zavřou, aria-expanded na spouštěči drží stav. help() vrací spouštěč, helpTip() (KE.tip) plochu
  // (plochu dej mimo nadpis, třeba za kartu). id i text jsou konstanty z kódu.
  function help(id, label) {
    return '<button class="k-help" type="button" aria-label="' + esc(label) + '" aria-expanded="false" popovertarget="' + esc(id) + '">'
      + kic('circle-help') + '</button>';
  }
  function helpTip(id, html) { return '<div class="k-pop k-pop--tip" id="' + esc(id) + '" popover>' + html + '</div>'; }
  if (SHELL) {
    // jedna plocha může mít víc spouštěčů (nabídka: „?“ u každé sekce a položky): pod spouštěč,
    // kterým se otevřela (klik i Enter/mezerník jdou přes click), aria-expanded jen na něm
    var helpFrom = null;
    document.addEventListener('click', function (e) {
      var t = e.target.closest && e.target.closest('[popovertarget]');
      if (t) helpFrom = t;
    }, true);
    document.addEventListener('toggle', function (e) {
      var pop = e.target;
      if (!pop.matches || !pop.matches('.k-pop--tip[popover]')) return;
      var open = e.newState === 'open';
      var trigger = open && helpFrom && helpFrom.isConnected && helpFrom.getAttribute('popovertarget') === pop.id ? helpFrom
        : (!open && pop._kFrom) || document.querySelector('[popovertarget="' + pop.id + '"]');
      if (!trigger) return;
      if (open) pop._kFrom = trigger;
      trigger.setAttribute('aria-expanded', String(open));
      var close = pop._kClose || (pop._kClose = function (ev) {
        if (!(ev.target instanceof Node && pop.contains(ev.target))) pop.hidePopover();
      });
      if (!open) { window.removeEventListener('scroll', close, true); window.removeEventListener('resize', close); return; }
      var r = trigger.getBoundingClientRect();
      var left = Math.min(Math.max(8, r.left - 12), innerWidth - pop.offsetWidth - 8);
      Object.assign(pop.style, {inset: 'auto', margin: '0', left: left + 'px', top: (r.bottom + 6) + 'px'});
      window.addEventListener('scroll', close, true);
      window.addEventListener('resize', close);
    }, true);
  }

  /* ── pro nabidky.js: API, toast, dialogy, ikony; tools = .k-top__tools šasi nabídek ── */
  window.KE = {api: api, toast: toast,
    dialog: dialog, closeDialog: closeDialog, sheet: sheet, closeSheet: closeSheet, ic: kic, banner: banner, help: help, tip: helpTip,
    esc: esc, offline: offline, plural: plural, meta: meta, bar: bar, tools: SHELL ? $('.k-top__tools', SHELL) : null};

  /* ── po znovunačtení: toast a otevřený panel ── */
  var after = sess('ke-after');
  if (after) {
    sess('ke-after', null);
    try {
      after = JSON.parse(after);
      if (after.toast) toast(after.toast, 'ok');
      if (after.drawer === 'changes') renderChanges();
      if (after.drawer === 'seo' && $('#keSeo')) renderSeo();
      if (after.drawer === 'extra' && $('#keExtra')) xPanel({});
      if (after.drawer === 'find') renderFind();
    } catch (e) { /* nic */ }
  }
})();
