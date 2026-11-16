/* Старые телефоны (iOS 15 и ниже, Android WebView до 103): недостающие функции браузера.
   Без них приложение падало ещё до первого запроса (ревью 15.11.2026). Грузится первым. */
(() => {
  if (typeof AbortSignal !== 'undefined' && !AbortSignal.timeout) {
    AbortSignal.timeout = ms => {
      const controller = new AbortController();
      setTimeout(() => {
        let reason;
        try { reason = new DOMException('The operation timed out.', 'TimeoutError'); }
        catch (_) { reason = Object.assign(new Error('The operation timed out.'), {name: 'TimeoutError'}); }
        controller.abort(reason);
      }, ms);
      return controller.signal;
    };
  }
  const random = window.crypto;
  if (random && !random.randomUUID && random.getRandomValues) {
    random.randomUUID = () => {
      const bytes = random.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 15) | 64;
      bytes[8] = (bytes[8] & 63) | 128;
      const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    };
  }
})();
