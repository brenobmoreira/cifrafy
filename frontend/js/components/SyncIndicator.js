import { h, replaceContent } from '../dom.js';
import { formatClock } from '../studyTimer.js';
import { syncQueue } from '../syncQueue.js';
import { icon } from './icons.js';

export function SyncIndicator({ signal } = {}) {
  const label = h('span', { class: 'sync-pill__label' });
  const link = h('a', { class: 'sync-pill', href: '#/playlists', hidden: true }, icon('list', 16), label);
  const live = h('span', { class: 'sr-only', role: 'status' });
  let timer = null;
  let lastMessage = '';

  function announce(message) {
    if (message !== lastMessage) {
      lastMessage = message;
      live.textContent = message;
    }
  }

  function render() {
    const { batch, active } = syncQueue;
    const remaining = syncQueue.cooldownRemaining();
    link.href = batch ? `#/playlists/${encodeURIComponent(batch.playlistId)}` : '#/playlists';
    const name = batch?.playlistName || 'playlist';

    clearInterval(timer);
    timer = null;

    if (active) {
      const { done, total, current } = syncQueue.progress();
      const position = Math.min(done + 1, total);
      link.hidden = false;
      link.dataset.state = 'running';
      label.textContent = `${position}/${total}`;
      link.setAttribute('aria-label', `Analisando ${position} de ${total} em ${name}. Voltar à playlist`);
      announce(`Analisando ${position} de ${total}${current ? `: ${current.track.name}` : ''}`);
    } else if (remaining > 0) {
      link.hidden = false;
      link.dataset.state = 'cooldown';
      const tick = () => {
        const clock = formatClock(Math.ceil(syncQueue.cooldownRemaining()));
        label.textContent = clock;
        link.setAttribute('aria-label', `Cifra Club em pausa por ${clock}. Voltar à playlist`);
      };
      tick();
      timer = setInterval(tick, 1000);
      announce('O Cifra Club pediu uma pausa. As músicas restantes continuam pendentes.');
    } else if (syncQueue.pausedWithoutTime) {
      link.hidden = false;
      link.dataset.state = 'cooldown';
      replaceContent(label, 'Pausa');
      link.setAttribute('aria-label', 'Cifra Club em pausa. Voltar à playlist');
      announce('O Cifra Club pediu uma pausa. Tente de novo em alguns minutos.');
    } else {
      link.hidden = true;
      if (batch) announce('Análise concluída.');
    }
  }

  const unsubscribe = syncQueue.subscribe(render);
  signal?.addEventListener('abort', () => {
    unsubscribe();
    clearInterval(timer);
  });
  render();
  return h('span', { class: 'sync-indicator' }, link, live);
}
