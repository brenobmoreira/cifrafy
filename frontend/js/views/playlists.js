import { api } from '../api.js';
import { formatDuration, h, plural, replaceContent } from '../dom.js';
import { songPath } from '../router.js';
import { store } from '../store.js';
import { syncQueue, trackKey } from '../syncQueue.js';
import { icon } from '../components/icons.js';
import { LevelBadge } from '../components/SongCard.js';
import { EmptyState, ErrorState, Skeleton, Strings, toast } from '../components/states.js';

function SpotifyConnect() {
  const status = store.status;
  if (status?.spotify_logged_in) {
    return h('p', { class: 'connected' }, icon('check', 16), 'Spotify conectado');
  }
  if (!status?.spotify_configured) return null;
  return h('button', {
    class: 'btn btn--ghost',
    type: 'button',
    onclick: async () => {
      try {
        const { auth_url: url } = await api.spotifyAuthUrl();
        if (url) window.location.assign(url);
      } catch (error) {
        toast(error.message, 'error');
      }
    },
  }, icon('link', 18), 'Conectar Spotify');
}

function PlaylistRow(playlist, onDelete) {
  return h(
    'li',
    { class: 'playlist-row' },
    h('a', { class: 'playlist-row__link', href: `#/playlists/${encodeURIComponent(playlist.id)}` },
      h('span', { class: 'playlist-row__name' }, playlist.name),
      h('span', { class: 'playlist-row__count' }, plural(playlist.total_tracks ?? 0, 'faixa'))),
    h('button', {
      class: 'icon-btn',
      type: 'button',
      'aria-label': `Remover ${playlist.name} do Cifrafy`,
      title: 'Remover do Cifrafy',
      onclick: () => onDelete(playlist),
    }, icon('trash', 18)),
  );
}

export function PlaylistsView({ signal }) {
  const list = h('div', {}, Skeleton({ count: 4, variant: 'row' }));
  const connectSlot = h('div', { class: 'connect-slot' });
  const input = h('input', {
    id: 'playlist-url',
    class: 'input',
    type: 'text',
    required: true,
    inputMode: 'url',
    placeholder: 'https://open.spotify.com/playlist/...',
    autocomplete: 'off',
  });
  const submit = h('button', { class: 'btn btn--primary', type: 'submit' }, 'Importar');
  const feedback = h('p', { class: 'form-feedback', role: 'status' });

  async function load() {
    replaceContent(list, Skeleton({ count: 4, variant: 'row' }));
    try {
      const playlists = await api.playlists(signal);
      replaceContent(list, playlists.length
        ? h('ul', { class: 'playlist-list' }, playlists.map((p) => PlaylistRow(p, removePlaylist)))
        : EmptyState({
            title: 'Nenhuma playlist por aqui',
            text: 'Cole o link de uma playlist pública do Spotify para começar.',
          }));
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(list, ErrorState({ error, onRetry: load }));
    }
  }

  async function removePlaylist(playlist) {
    if (!window.confirm(`Remover "${playlist.name}" do Cifrafy? As cifras salvas continuam no banco.`)) return;
    try {
      await api.deletePlaylist(playlist.id);
      toast('Playlist removida.');
      load();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function importPlaylist(event) {
    event.preventDefault();
    const value = input.value.trim();
    if (!value) return;
    submit.disabled = true;
    submit.textContent = 'Importando';
    feedback.textContent = '';
    feedback.classList.remove('form-feedback--error');
    try {
      const playlist = await api.addPlaylist(value);
      window.location.hash = `#/playlists/${encodeURIComponent(playlist.id)}`;
    } catch (error) {
      feedback.textContent = error.message;
      feedback.classList.add('form-feedback--error');
    } finally {
      submit.disabled = false;
      submit.textContent = 'Importar';
    }
  }

  const unsubscribe = store.subscribe(() => replaceContent(connectSlot, SpotifyConnect()));
  signal.addEventListener('abort', unsubscribe);
  replaceContent(connectSlot, SpotifyConnect());
  load();

  return h(
    'section',
    { class: 'view view--playlists' },
    h(
      'header',
      { class: 'page-head' },
      h('div', {}, h('p', { class: 'eyebrow' }, 'Sua coleção'), h('h1', { class: 'page-title' }, 'Playlists')),
      connectSlot,
    ),
    h(
      'form',
      { class: 'import-form', onsubmit: importPlaylist },
      h('label', { for: 'playlist-url' }, 'Importar por link'),
      h('div', { class: 'import-form__row' }, input, submit),
      feedback,
    ),
    list,
  );
}

const MAX_BATCH = 15;
const ROW_STATUS = {
  running: 'Analisando',
  queued: 'Na fila',
  pending: 'Pendente',
  synced: 'Salva',
  cached: 'Já salva',
  not_found: 'Cifra não encontrada',
  rate_limited: 'Aguardando liberação',
  error: 'Falhou',
};

export function PlaylistDetailView({ params, signal }) {
  const playlistId = params.id;
  let tracks = [];
  let cooldownTimer = null;
  let cooling = false;
  let lastRunningKey = null;
  const selected = new Set();
  const rows = [];

  const body = h('div', {}, Skeleton({ count: 8, variant: 'row' }));
  const title = h('h1', { class: 'page-title' }, 'Faixas');
  const summary = h('p', { class: 'page-sub' });
  const bannerSlot = h('div');
  const progressFill = h('div', { class: 'sync-progress__fill' });
  const progressText = h('span', { class: 'sync-bar__text' });
  const syncButton = h('button', { class: 'btn btn--primary', type: 'button', onclick: runSync }, 'Analisar e salvar');
  const progress = h('div', { class: 'sync-progress', 'aria-hidden': 'true' }, progressFill);
  const bar = h('div', { class: 'sync-bar', hidden: true }, progress,
    h('div', { class: 'sync-bar__row' }, progressText, syncButton));

  const rowState = (index) => syncQueue.statusFor(playlistId, trackKey(tracks[index], index));
  const running = () => syncQueue.isRunning(playlistId);
  const blocked = () => syncQueue.active || syncQueue.cooldownRemaining() > 0;

  function updateBar() {
    const isRunning = running();
    bar.hidden = selected.size === 0 && !isRunning;
    if (isRunning) {
      const { done, total, current } = syncQueue.progress();
      progressFill.style.width = `${total ? (done / total) * 100 : 0}%`;
      progressText.textContent = `${Math.min(done + 1, total)} de ${total}${current ? `: ${current.track.name}` : ''}`;
    } else {
      progressFill.style.width = '0%';
      progressText.textContent = syncQueue.active
        ? 'Outra playlist está sendo analisada'
        : `${plural(selected.size, 'selecionada')} (máximo ${MAX_BATCH})`;
    }
    // aria-disabled keeps keyboard focus on the button while the batch runs.
    syncButton.setAttribute('aria-disabled', String(blocked() || selected.size === 0));
    syncButton.textContent = isRunning ? 'Analisando' : 'Analisar e salvar';
  }

  function badgeFor(index) {
    const track = tracks[index];
    const state = rowState(index);
    if (state && state !== 'synced' && state !== 'cached') {
      return h('span', { class: `track-state track-state--${state}` }, ROW_STATUS[state]);
    }
    if (track.is_synced || state) {
      return track.level
        ? h('span', { class: 'track-rating' }, LevelBadge(track.level), track.score)
        : h('span', { class: 'track-state track-state--synced' }, ROW_STATUS[state || 'synced']);
    }
    return h('span', { class: 'track-state' }, 'Pendente');
  }

  function refreshRow(index) {
    const row = rows[index];
    if (!row) return;
    const track = tracks[index];
    row.checkbox.checked = selected.has(index);
    row.checkbox.disabled = Boolean(track.is_synced) || syncQueue.active;
    row.el.classList.toggle('track--selected', selected.has(index));
    row.el.classList.toggle('track--done', Boolean(track.is_synced));
    row.el.classList.toggle('track--running', rowState(index) === 'running');
    replaceContent(row.state, badgeFor(index));
    // Only rebuilt when it changes, so a focused link is not lost during the sync.
    if (Boolean(track.is_synced) !== (row.open.childElementCount > 0)) {
      replaceContent(row.open, track.is_synced
        ? h('a', {
            class: 'icon-btn icon-btn--brass',
            href: songPath(track.artist, track.name),
            'aria-label': `Abrir ${track.name}`,
          }, icon('play', 18))
        : null);
    }
  }

  function updateSummary() {
    const done = tracks.filter((track) => track.is_synced).length;
    summary.textContent = `${done} de ${plural(tracks.length, 'faixa')} já analisadas`;
  }

  function refreshAll() {
    tracks.forEach((_, index) => refreshRow(index));
    updateBar();
    updateSummary();
  }

  function toggle(index, checkbox) {
    if (checkbox.checked && selected.size >= MAX_BATCH) {
      checkbox.checked = false;
      toast(`Máximo de ${MAX_BATCH} músicas por vez, para não sobrecarregar o Cifra Club.`);
      return;
    }
    if (checkbox.checked) selected.add(index);
    else selected.delete(index);
    refreshRow(index);
    updateBar();
  }

  function selectNext() {
    if (syncQueue.active) return;
    selected.clear();
    tracks.some((track, index) => {
      if (!track.is_synced) selected.add(index);
      return selected.size >= MAX_BATCH;
    });
    refreshAll();
  }

  function clearSelection() {
    if (syncQueue.active) return;
    selected.clear();
    refreshAll();
  }

  // Rebuilds rows and selection from the queue, which outlives this view.
  function applyBatch() {
    const batch = syncQueue.batchFor(playlistId);
    if (!batch || !tracks.length) return;
    for (const item of batch.items) {
      let index = item.index;
      if (!tracks[index] || trackKey(tracks[index], index) !== item.key) {
        index = tracks.findIndex((track, position) => trackKey(track, position) === item.key);
      }
      if (index < 0) continue;
      if (item.result) tracks[index] = { ...tracks[index], ...item.result };
      const keepSelected = ['queued', 'running', 'pending', 'rate_limited'].includes(item.status) || item.retry;
      if (keepSelected) selected.add(index);
      else selected.delete(index);
    }
  }

  function renderCooldown() {
    const remaining = syncQueue.cooldownRemaining();
    clearInterval(cooldownTimer);
    if (remaining > 0) {
      cooling = true;
      const label = h('strong', { class: 'cooldown__time', role: 'timer' });
      const tick = () => { label.textContent = formatDuration(syncQueue.cooldownRemaining()); };
      replaceContent(bannerSlot, h('div', { class: 'banner banner--wait' },
        h('div', {}, h('b', {}, 'O Cifra Club pediu uma pausa.'), ' As músicas restantes continuam selecionadas.'),
        h('p', { class: 'cooldown' }, 'Tente de novo em ', label)));
      tick();
      cooldownTimer = setInterval(tick, 1000);
    } else if (syncQueue.pausedWithoutTime) {
      replaceContent(bannerSlot, h('div', { class: 'banner banner--wait' },
        h('b', {}, 'O Cifra Club pediu uma pausa.'), ' Tente de novo em alguns minutos.'));
    } else if (cooling) {
      cooling = false;
      replaceContent(bannerSlot, h('div', { class: 'banner banner--ok' },
        'O Cifra Club está liberado. Você pode continuar de onde parou.'));
    }
  }

  function onQueueChange() {
    applyBatch();
    refreshAll();
    renderCooldown();
    const current = running() ? syncQueue.progress().current : null;
    if (current && current.key !== lastRunningKey) {
      lastRunningKey = current.key;
      rows[current.index]?.el.scrollIntoView({ block: 'nearest' });
    }
  }

  async function reloadAfterBatch(_counts, batch) {
    if (batch?.playlistId !== playlistId) return;
    try {
      const fresh = await api.playlistTracks(playlistId, { simplified: store.prefs.simplified, signal });
      if (fresh.length === tracks.length && !syncQueue.active) {
        tracks = fresh;
        applyBatch();
        refreshAll();
      }
    } catch {
      // The rows already show the results of this sync.
    }
  }

  function runSync() {
    if (blocked() || !selected.size) return;
    syncQueue.start({
      playlistId,
      playlistName: title.textContent,
      simplified: store.prefs.simplified,
      avoidBarre: store.profile.avoid_barre,
      selections: [...selected].sort((a, b) => a - b).map((index) => ({ index, track: tracks[index] })),
    });
  }

  function TrackRow(track, index) {
    const checkbox = h('input', {
      type: 'checkbox',
      class: 'track__check',
      'aria-label': `Selecionar ${track.name}`,
      onchange: (event) => toggle(index, event.target),
    });
    const state = h('div', { class: 'track__state' });
    const open = h('div', { class: 'track__open' });
    const el = h('li', { class: 'track' },
      h('label', { class: 'track__label' },
        checkbox,
        h('span', { class: 'track__number', 'aria-hidden': 'true' }, index + 1),
        h('span', { class: 'track__titles' },
          h('span', { class: 'track__name' }, track.name),
          h('span', { class: 'track__artist' }, track.artist))),
      state,
      open);
    rows[index] = { el, checkbox, state, open };
    return el;
  }

  const modeButtons = [false, true].map((value) =>
    h('button', {
      type: 'button',
      class: 'segment',
      'aria-pressed': store.prefs.simplified === value,
      onclick: () => {
        if (syncQueue.active || store.prefs.simplified === value) return;
        store.setPrefs({ simplified: value });
        modeButtons.forEach((button, i) => button.setAttribute('aria-pressed', String((i === 1) === value)));
        syncQueue.clearFinished(playlistId);
        selected.clear();
        load();
      },
    }, value ? 'Simplificada' : 'Normal'),
  );

  async function load() {
    replaceContent(body, Skeleton({ count: 8, variant: 'row' }));
    try {
      const [loaded, playlists] = await Promise.all([
        api.playlistTracks(playlistId, { simplified: store.prefs.simplified, signal }),
        api.playlists(signal).catch(() => []),
      ]);
      const meta = playlists.find((playlist) => playlist.id === playlistId);
      if (meta) title.textContent = meta.name;
      tracks = loaded;
      rows.length = 0;
      if (!tracks.length) {
        replaceContent(body, EmptyState({ title: 'Playlist sem faixas', text: 'Não encontramos músicas nesta playlist.' }));
        updateSummary();
        return;
      }
      replaceContent(body,
        h('div', { class: 'selection-tools' },
          h('button', { class: 'btn btn--ghost btn--small', type: 'button', onclick: selectNext },
            `Próximas ${MAX_BATCH} pendentes`),
          h('button', { class: 'btn btn--quiet btn--small', type: 'button', onclick: clearSelection }, 'Limpar')),
        h('ol', { class: 'track-list' }, tracks.map(TrackRow)));
      applyBatch();
      refreshAll();
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(body, ErrorState({ error, onRetry: load }));
    }
  }

  const unsubscribeQueue = syncQueue.subscribe(onQueueChange);
  const unsubscribeFinish = syncQueue.onFinish(reloadAfterBatch);
  signal.addEventListener('abort', () => {
    clearInterval(cooldownTimer);
    unsubscribeQueue();
    unsubscribeFinish();
  });
  store.refreshStatus(signal)
    .then((status) => {
      if (status.cifraclub_cooldown_seconds > 0) syncQueue.setCooldown(status.cifraclub_cooldown_seconds);
    })
    .catch(() => {});
  renderCooldown();
  load();

  return h(
    'section',
    { class: 'view view--detail' },
    h('a', { class: 'back-link', href: '#/playlists' }, icon('back', 18), 'Playlists'),
    h('header', { class: 'page-head page-head--strings' }, Strings(), h('div', {}, title, summary)),
    h('div', { class: 'explain' },
      h('p', {}, 'Escolha até 15 músicas. Analisar salva os acordes e a cifra completa no seu banco local, e depois elas abrem sem acessar o Cifra Club.'),
      h('div', { class: 'segmented segmented--compact', role: 'group', 'aria-label': 'Versão da cifra' }, modeButtons)),
    bannerSlot,
    body,
    bar,
  );
}
