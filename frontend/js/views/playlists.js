import { api } from '../api.js';
import { formatDuration, h, plural, replaceContent } from '../dom.js';
import { songPath } from '../router.js';
import { store } from '../store.js';
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
  synced: 'Salva',
  cached: 'Já salva',
  not_found: 'Cifra não encontrada',
  rate_limited: 'Aguardando liberação',
  error: 'Falhou',
};

export function PlaylistDetailView({ params, signal }) {
  const playlistId = params.id;
  let tracks = [];
  let syncing = false;
  let cooldownTimer = null;
  let cooldownUntil = 0;
  const selected = new Set();
  const rowStatus = new Map();
  const rows = [];

  const body = h('div', {}, Skeleton({ count: 8, variant: 'row' }));
  const title = h('h1', { class: 'page-title' }, 'Faixas');
  const summary = h('p', { class: 'page-sub' });
  const bannerSlot = h('div');
  const progressFill = h('div', { class: 'sync-progress__fill' });
  const progressText = h('span', { class: 'sync-bar__text', role: 'status' });
  const syncButton = h('button', { class: 'btn btn--primary', type: 'button', onclick: runSync }, 'Analisar e salvar');
  const progress = h('div', { class: 'sync-progress', 'aria-hidden': 'true' }, progressFill);
  const bar = h('div', { class: 'sync-bar', hidden: true }, progress,
    h('div', { class: 'sync-bar__row' }, progressText, syncButton));

  const trackKey = (track, index) => track.track_id || `${track.artist}:${track.name}:${index}`;
  const inCooldown = () => cooldownUntil > Date.now();

  function updateBar() {
    bar.hidden = selected.size === 0 && !syncing;
    if (!syncing) {
      progressFill.style.width = '0%';
      progressText.textContent = `${plural(selected.size, 'selecionada')} (máximo ${MAX_BATCH})`;
    }
    syncButton.disabled = syncing || selected.size === 0 || inCooldown();
    syncButton.textContent = syncing ? 'Analisando' : 'Analisar e salvar';
  }

  function badgeFor(index) {
    const track = tracks[index];
    const state = rowStatus.get(trackKey(track, index));
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
    row.checkbox.disabled = Boolean(track.is_synced) || syncing;
    row.el.classList.toggle('track--selected', selected.has(index));
    row.el.classList.toggle('track--done', Boolean(track.is_synced));
    row.el.classList.toggle('track--running', rowStatus.get(trackKey(track, index)) === 'running');
    replaceContent(row.state, badgeFor(index));
    replaceContent(row.open, track.is_synced
      ? h('a', {
          class: 'icon-btn icon-btn--brass',
          href: songPath(track.artist, track.name),
          'aria-label': `Abrir ${track.name}`,
        }, icon('play', 18))
      : null);
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
    selected.clear();
    tracks.some((track, index) => {
      if (!track.is_synced) selected.add(index);
      return selected.size >= MAX_BATCH;
    });
    refreshAll();
  }

  function clearSelection() {
    selected.clear();
    refreshAll();
  }

  function startCooldown(seconds) {
    clearInterval(cooldownTimer);
    if (!seconds || seconds <= 0) {
      cooldownUntil = 0;
      replaceContent(bannerSlot, h('div', { class: 'banner banner--wait', role: 'status' },
        h('b', {}, 'O Cifra Club pediu uma pausa.'), ' Tente de novo em alguns minutos.'));
      updateBar();
      return;
    }
    cooldownUntil = Date.now() + seconds * 1000;
    const label = h('strong', { class: 'cooldown__time' });
    const tick = () => {
      const remaining = (cooldownUntil - Date.now()) / 1000;
      if (remaining <= 0) {
        clearInterval(cooldownTimer);
        cooldownUntil = 0;
        replaceContent(bannerSlot, h('div', { class: 'banner banner--ok', role: 'status' },
          'O Cifra Club está liberado. Você pode continuar de onde parou.'));
        updateBar();
        return;
      }
      label.textContent = formatDuration(remaining);
    };
    replaceContent(bannerSlot, h('div', { class: 'banner banner--wait', role: 'status' },
      h('div', {}, h('b', {}, 'O Cifra Club pediu uma pausa.'), ' As músicas restantes continuam selecionadas.'),
      h('p', { class: 'cooldown' }, 'Tente de novo em ', label)));
    tick();
    cooldownTimer = setInterval(tick, 1000);
    updateBar();
  }

  async function finishSync(counts) {
    if (counts.saved) toast(`${plural(counts.saved, 'música salva', 'músicas salvas')} no banco local.`, 'ok');
    if (counts.failed) toast(`${plural(counts.failed, 'música')} sem cifra disponível.`, 'error');
    store.refreshStatus(signal).catch(() => {});
    try {
      const fresh = await api.playlistTracks(playlistId, { simplified: store.prefs.simplified, signal });
      if (fresh.length === tracks.length && !syncing) {
        tracks = fresh;
        refreshAll();
      }
    } catch {
      // The rows already show the results of this sync.
    }
  }

  async function runSync() {
    if (syncing || !selected.size || inCooldown()) return;
    syncing = true;
    const queue = [...selected].sort((a, b) => a - b);
    queue.forEach((index) => rowStatus.set(trackKey(tracks[index], index), 'queued'));
    refreshAll();

    let done = 0;
    const counts = { saved: 0, failed: 0 };
    for (const [position, index] of queue.entries()) {
      if (signal.aborted) return;
      const track = tracks[index];
      const key = trackKey(track, index);
      rowStatus.set(key, 'running');
      refreshRow(index);
      progressText.textContent = `${position + 1} de ${queue.length}: ${track.name}`;
      rows[index]?.el.scrollIntoView({ block: 'nearest' });

      let response;
      try {
        response = await api.syncTracks(
          [{ track_id: track.track_id || null, artist: track.artist, name: track.name }],
          { simplified: store.prefs.simplified, avoidBarre: store.profile.avoid_barre },
        );
      } catch (error) {
        if (signal.aborted) return;
        rowStatus.set(key, 'error');
        queue.slice(position + 1).forEach((rest) => rowStatus.delete(trackKey(tracks[rest], rest)));
        toast(error.message, 'error');
        break;
      }
      if (signal.aborted) return;

      const result = response.results?.[0];
      const status = result?.status || 'error';
      rowStatus.set(key, status);

      if (status === 'rate_limited') {
        queue.slice(position + 1).forEach((rest) => rowStatus.delete(trackKey(tracks[rest], rest)));
        syncing = false;
        startCooldown(result.retry_after_seconds ?? response.retry_after_seconds);
        refreshAll();
        await finishSync(counts);
        return;
      }

      if (status === 'synced' || status === 'cached') {
        tracks[index] = { ...track, ...result, artist: track.artist, name: track.name };
        counts.saved += 1;
      } else {
        counts.failed += 1;
      }
      selected.delete(index);
      done += 1;
      progressFill.style.width = `${(done / queue.length) * 100}%`;
      refreshRow(index);
    }

    syncing = false;
    refreshAll();
    await finishSync(counts);
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
        if (syncing || store.prefs.simplified === value) return;
        store.setPrefs({ simplified: value });
        modeButtons.forEach((button, i) => button.setAttribute('aria-pressed', String((i === 1) === value)));
        rowStatus.clear();
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
      refreshAll();
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(body, ErrorState({ error, onRetry: load }));
    }
  }

  signal.addEventListener('abort', () => clearInterval(cooldownTimer));
  store.refreshStatus(signal)
    .then((status) => {
      if (status.cifraclub_cooldown_seconds > 0) startCooldown(status.cifraclub_cooldown_seconds);
    })
    .catch(() => {});
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
