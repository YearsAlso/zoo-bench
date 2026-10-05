    (function () {
      var stage = document.getElementById('deck-stage');
      var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));
      var prev = document.getElementById('deck-prev');
      var next = document.getElementById('deck-next');
      var cur = document.getElementById('deck-cur');
      var total = document.getElementById('deck-total');
      var STORE = 'deck:idx:' + (location.pathname || '/');
      var idx = 0;

      // ---- scale-to-fit ---------------------------------------------------
      function fit() {
        var sw = window.innerWidth;
        var sh = window.innerHeight;
        var pad = 32;
        var s = Math.min((sw - pad) / 1920, (sh - pad) / 1080);
        if (!isFinite(s) || s <= 0) s = 1;
        var tx = (sw - 1920 * s) / 2;
        var ty = (sh - 1080 * s) / 2;
        stage.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + s + ')';
      }

      // ---- navigation -----------------------------------------------------
      function pad2(n) { return (n < 10 ? '0' : '') + n; }
      function paint() {
        slides.forEach(function (el, i) { el.classList.toggle('active', i === idx); });
        if (cur) cur.textContent = pad2(idx + 1);
        if (total) total.textContent = pad2(slides.length);
        if (prev) prev.toggleAttribute('disabled', idx <= 0);
        if (next) next.toggleAttribute('disabled', idx >= slides.length - 1);
        postDeckState();
      }
      function go(i) {
        idx = Math.max(0, Math.min(slides.length - 1, i));
        paint();
        try { localStorage.setItem(STORE, String(idx)); } catch (_) {}
      }
      var OD_DECK_PROTOCOL_VERSION = 1;
      function postDeckState() {
        try {
          window.parent.postMessage({
            type: 'od:slide-state',
            protocolVersion: OD_DECK_PROTOCOL_VERSION,
            active: idx,
            count: slides.length
          }, '*');
        } catch (_) {}
      }
      function announceDeckProtocol() {
        try {
          window.parent.postMessage({
            type: 'od:deck-ready',
            protocolVersion: OD_DECK_PROTOCOL_VERSION,
            capabilities: ["absolute-navigation","state-events"]
          }, '*');
        } catch (_) {}
      }
      window.addEventListener('message', function (event) {
        var data = event && event.data;
        if (!data || data.type !== 'od:slide') return;
        if (data.protocolVersion != null && data.protocolVersion !== OD_DECK_PROTOCOL_VERSION) return;
        var target = idx;
        if (data.action === 'go') {
          if (typeof data.index !== 'number' || !isFinite(data.index)) return;
          target = Math.floor(data.index);
        } else if (data.action === 'next') target = idx + 1;
        else if (data.action === 'prev') target = idx - 1;
        else if (data.action === 'first') target = 0;
        else if (data.action === 'last') target = slides.length - 1;
        else return;
        go(target);
      });
      function onKey(e) {
        if (e.__odDeckKeyHandled) return;
        var t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
        if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
        if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') { e.__odDeckKeyHandled = true; e.preventDefault(); go(idx + 1); }
        else if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.__odDeckKeyHandled = true; e.preventDefault(); go(idx - 1); }
        else if (e.key === 'Home' || String(e.key).toLowerCase() === 'r') { e.__odDeckKeyHandled = true; e.preventDefault(); go(0); }
        else if (e.key === 'End') { e.__odDeckKeyHandled = true; e.preventDefault(); go(slides.length - 1); }
      }
      window.addEventListener('keydown', onKey, true);
      document.addEventListener('keydown', onKey, true);
      if (prev) prev.addEventListener('click', function () { go(idx - 1); });
      if (next) next.addEventListener('click', function () { go(idx + 1); });
      document.addEventListener('click', function (e) {
        if (e.defaultPrevented) return;
        if (e.button !== undefined && e.button !== 0) return;
        if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
        var t = e.target;
        while (t && t !== document.body && t !== document.documentElement) {
          var tag = String(t.tagName || '').toUpperCase();
          if (
            tag === 'A' ||
            tag === 'BUTTON' ||
            tag === 'INPUT' ||
            tag === 'TEXTAREA' ||
            tag === 'SELECT' ||
            t.isContentEditable ||
            t.getAttribute('role') === 'button' ||
            t.getAttribute('role') === 'link'
          ) return;
          t = t.parentElement;
        }
        focusDeck();
        if (e.clientX < window.innerWidth / 2) go(idx - 1);
        else go(idx + 1);
      }, true);

      document.body.setAttribute('tabindex', '-1');
      document.body.style.outline = 'none';
      function focusDeck() { try { window.focus(); document.body.focus({ preventScroll: true }); } catch (_) {} }
      document.addEventListener('mousedown', focusDeck);
      window.addEventListener('load', focusDeck);

      try {
        var saved = parseInt(localStorage.getItem(STORE) || '0', 10);
        if (!isNaN(saved) && saved >= 0 && saved < slides.length) idx = saved;
      } catch (_) {}

      window.addEventListener('resize', fit);
      announceDeckProtocol();
      fit();
      paint();
      focusDeck();
    })();
  
