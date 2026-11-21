// Açıklama kutuları: [data-tip] olan öğelerin üzerine gelince (telefonda dokununca) beyaz kutu açılır.
(function () {
  let tip = null;
  let owner = null;

  function hide() {
    if (!tip) return;
    const t = tip;
    t.classList.remove('show');
    setTimeout(() => t.remove(), 160);
    tip = null;
    owner = null;
  }

  function show(el) {
    if (owner === el) return;
    hide();
    owner = el;
    tip = document.createElement('div');
    tip.className = 'tip';
    tip.setAttribute('role', 'tooltip');
    tip.textContent = el.getAttribute('data-tip');
    document.body.appendChild(tip);
    const r = el.getBoundingClientRect();
    const t = tip.getBoundingClientRect();
    const gap = 10;
    let top = r.top - t.height - gap;
    let below = false;
    if (top < 8) { top = r.bottom + gap; below = true; }
    let left = r.left + r.width / 2 - t.width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - t.width - 8));
    tip.style.top = top + 'px';
    tip.style.left = left + 'px';
    tip.style.setProperty('--arrow-x', (r.left + r.width / 2 - left) + 'px');
    if (below) tip.classList.add('below');
    requestAnimationFrame(() => tip && tip.classList.add('show'));
  }

  document.addEventListener('pointerover', e => {
    const el = e.target.closest('[data-tip]');
    if (el && e.pointerType !== 'touch') show(el);
  });
  document.addEventListener('pointerout', e => {
    const el = e.target.closest('[data-tip]');
    if (el && el === owner && !el.contains(e.relatedTarget)) hide();
  });
  // Telefonda dokununca aç/kapat; bağlantının içindeyse sayfaya gitmeden önce açıklamayı göster
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-tip]');
    if (el && window.matchMedia('(hover: none)').matches) {
      e.preventDefault();
      if (owner === el) hide(); else show(el);
      return;
    }
    if (!el) hide();
  }, true);
  document.addEventListener('focusin', e => {
    const el = e.target.closest('[data-tip]');
    if (el) show(el);
  });
  document.addEventListener('focusout', hide);
  window.addEventListener('scroll', hide, {passive: true});
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });
})();

// Telefonda menü: "Menü" düğmesi bağlantıları açar/kapatır.
(function () {
  const btn = document.querySelector('.nav-toggle');
  const nav = document.getElementById('ana-menu');
  if (!btn || !nav) return;
  btn.addEventListener('click', () => {
    const open = nav.classList.toggle('open');
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
})();

// Sorumluluk reddi: alttaki bağlantı pencereyi animasyonla açar; ×, "Anladım", Esc ya da dışına tıklamak
// pencereyi yine animasyonla kapatır.
(function () {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');

  function closeModal(dlg) {
    if (!dlg.open || dlg.classList.contains('closing')) return;
    if (reduce.matches) { dlg.close(); return; }
    dlg.classList.add('closing');
    setTimeout(() => { dlg.classList.remove('closing'); dlg.close(); }, 210);
  }

  document.addEventListener('click', e => {
    const link = e.target.closest('[data-modal]');
    if (link) {
      const dlg = document.getElementById(link.getAttribute('data-modal'));
      if (dlg && typeof dlg.showModal === 'function') {
        e.preventDefault();
        dlg.showModal();
      }
      return;
    }
    const dlg = e.target.closest('dialog.modal');
    if (!dlg) return;
    // dialog'un kendisine tıklandıysa (içerik dışında, arka plan) ya da kapat düğmesine basıldıysa
    if (e.target === dlg || e.target.closest('form[method="dialog"] button')) {
      e.preventDefault();
      closeModal(dlg);
    }
  });
  document.querySelectorAll('dialog.modal').forEach(dlg => {
    dlg.addEventListener('cancel', e => { e.preventDefault(); closeModal(dlg); });  // Esc
  });
})();

// Koyu / açık tema: düğmeye basınca sayfa kısa bir solmayla değişir (View Transitions; desteklemeyen
// tarayıcıda ve "hareketi azalt" açıkken anında). Seçim tarayıcıda hatırlanır.
(function () {
  const root = document.documentElement;
  const btn = document.querySelector('.theme-toggle');
  function label() {
    if (!btn) return;
    const dark = root.getAttribute('data-theme') === 'dark';
    btn.setAttribute('aria-label', dark ? 'Açık temaya geç' : 'Koyu temaya geç');
    btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
  }
  label();
  if (!btn) return;
  btn.addEventListener('click', () => {
    const apply = () => {  // hedef tema uygulandığı anda hesaplanır: art arda iki tıklama ikisi de sayılır
      const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('tema', next); } catch (e) {}
      label();
      document.dispatchEvent(new CustomEvent('temadegisti', {detail: next}));
    };
    const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (document.startViewTransition && !reduce) document.startViewTransition(apply);
    else apply();
  });
})();

// Açılır bölümler (<details>): içerik yükseklik animasyonuyla açılıp kapanır.
(function () {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');

  function prepare(d) {
    if (d._acc) return;
    const summary = d.querySelector(':scope > summary');
    if (!summary) return;
    let body = d.querySelector(':scope > .acc-body');
    if (!body) {
      body = document.createElement('div');
      body.className = 'acc-body';
      while (summary.nextSibling) body.appendChild(summary.nextSibling);
      d.appendChild(body);
    }
    d._acc = {summary, body, anim: null};
    summary.addEventListener('click', e => {
      e.preventDefault();
      toggle(d);
    });
  }

  function toggle(d) {
    const {body} = d._acc;
    if (d._acc.anim) d._acc.anim.cancel();
    const opening = !d.open || d.classList.contains('acc-closing');
    if (reduce.matches || !body.animate) {
      d.open = opening;
      if (opening) d.dispatchEvent(new Event('acc-open'));
      return;
    }
    const startH = d.open ? body.offsetHeight : 0;
    if (opening) {
      d.classList.remove('acc-closing');
      d.open = true;
      d.dispatchEvent(new Event('acc-open'));
      const endH = body.scrollHeight;
      d._acc.anim = body.animate([{height: startH + 'px', opacity: startH ? 1 : 0}, {height: endH + 'px', opacity: 1}],
                                 {duration: 320, easing: 'cubic-bezier(.2,.8,.2,1)'});
    } else {
      d.classList.add('acc-closing');
      d._acc.anim = body.animate([{height: startH + 'px', opacity: 1}, {height: '0px', opacity: 0}],
                                 {duration: 260, easing: 'ease-in'});
    }
    d._acc.anim.onfinish = () => {
      if (d.classList.contains('acc-closing')) { d.open = false; d.classList.remove('acc-closing'); }
      d._acc.anim = null;
    };
  }

  document.querySelectorAll('details').forEach(prepare);
  document.addEventListener('htmx:afterSwap', e => e.target.querySelectorAll && e.target.querySelectorAll('details').forEach(prepare));
})();

// Geri alınamayan işlemlerden (kasa silme, sıfırlama) önce onay iste.
document.addEventListener('submit', (e) => {
  const msg = e.target.getAttribute && e.target.getAttribute('data-confirm');
  if (msg && !window.confirm(msg)) e.preventDefault();
}, true);
