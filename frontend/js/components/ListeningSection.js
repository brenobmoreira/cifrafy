// "What you listen to": Spotify top and recent tracks joined with the local index.
import { api } from '../api.js';
import { h, replaceContent } from '../dom.js';
import { songPath } from '../router.js';
import { store } from '../store.js';
import { icon } from './icons.js';
import { LevelBadge } from './SongCard.js';
import { EmptyState, ErrorState, Skeleton, toast } from './states.js';

const SOURCES = [{ value: 'top', label: 'Mais ouvidas' }, { value: 'recent', label: 'Recentes' }];
const RANGES = [
  { value: 'short_term', label: '4 semanas' },
  { value: 'medium_term', label: '6 meses' },
  { value: 'long_term', label: 'Tudo' },
];

async function connectSpotify() {
  try {
    const { auth_url: url } = await api.spotifyAuthUrl();
    if (url) window.location.href = url;
    else toast('Configure o Spotify no arquivo .env para conectar.', 'info');
  } catch (error) {
    toast(error.message, 'error');
  }
}

export function ListeningSection({ signal }) {
  let source = 'top';
  let range = 'short_term';
  let controller = null;
  const list = h('div', {});
  const tabsSlot = h('div', { class: 'listening__controls' });

  const segmented = (label, items, current, onPick) =>
    h('div', { class: 'segmented segmented--compact', role: 'group', 'aria-label': label },
      items.map((item) => h('button', {
        class: 'segment', type: 'button', 'aria-pressed': item.value === current,
        onclick: () => onPick(item.value),
      }, item.label)));

  function renderTabs() {
    replaceContent(tabsSlot,
      segmented('Origem', SOURCES, source, (value) => {
        source = value;
        renderTabs();
        load();
      }),
      source === 'top' && segmented('Período', RANGES, range, (value) => {
        range = value;
        renderTabs();
        load();
      }));
  }

  async function syncTrack(track, button) {
    button.disabled = true;
    button.textContent = 'Analisando';
    try {
      const { results } = await api.syncTracks(
        [{ track_id: track.track_id, artist: track.artist, name: track.name }],
        { simplified: store.prefs.simplified, avoidBarre: store.profile.avoid_barre },
      );
      const result = results[0];
      if (result?.found) {
        toast(`${track.name} analisada.`, 'ok');
        store.refreshStatus().catch(() => {});
        load();
        return;
      }
      const messages = {
        not_found: 'Cifra não encontrada para esta música.',
        rate_limited: 'O Cifra Club pediu uma pausa. Tente mais tarde.',
      };
      toast(messages[result?.status] || result?.error || 'Não foi possível analisar.', 'error');
    } catch (error) {
      toast(error.message, 'error');
    }
    button.disabled = false;
    button.textContent = 'Analisar';
  }

  function Row(track, index) {
    const titles = h('div', { class: 'listen-row__titles' },
      track.is_synced
        ? h('a', { class: 'listen-row__name', href: songPath(track.artist, track.name) }, track.name)
        : h('p', { class: 'listen-row__name' }, track.name),
      h('p', { class: 'listen-row__artist' }, track.artist));
    const button = !track.is_synced && h('button', {
      class: 'btn btn--ghost btn--small', type: 'button',
      onclick: (event) => syncTrack(track, event.currentTarget),
    }, 'Analisar');
    return h('li', { class: 'listen-row', style: { '--i': Math.min(index, 12) } },
      titles,
      track.is_synced ? LevelBadge(track.easy_with_capo && track.level !== 'Fácil' ? 'Fácil' : track.level) : button,
      track.is_synced && track.easy_with_capo && track.level !== 'Fácil' &&
        h('p', { class: 'listen-row__capo' }, `Com capotraste na ${track.capo_suggestion?.capo_fret}ª casa`));
  }

  async function load() {
    controller?.abort();
    controller = new AbortController();
    const requestSignal = AbortSignal.any([signal, controller.signal]);
    replaceContent(list, Skeleton({ count: 3, variant: 'row' }));
    try {
      const data = await api.listening({ source, timeRange: source === 'top' ? range : null, limit: 12 }, requestSignal);
      if (!data.connected || data.needs_reconnect) {
        replaceContent(list, EmptyState({
          title: data.needs_reconnect ? 'Reconecte o Spotify' : 'Conecte seu Spotify',
          text: data.needs_reconnect
            ? 'Precisamos de uma nova permissão para ler o que você mais ouve.'
            : 'Veja quais das músicas que você mais ouve já estão no seu nível.',
          action: h('button', { class: 'btn btn--primary', type: 'button', onclick: connectSpotify },
            icon('link', 16), data.needs_reconnect ? 'Reconectar' : 'Conectar Spotify'),
        }));
        return;
      }
      if (!data.tracks?.length) {
        replaceContent(list, EmptyState({ title: 'Nada por aqui ainda', text: 'O Spotify não retornou músicas para este período.' }));
        return;
      }
      replaceContent(list, h('ul', { class: 'listen-list' }, data.tracks.map(Row)));
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(list, ErrorState({ error, onRetry: load }));
    }
  }

  signal.addEventListener('abort', () => controller?.abort());
  renderTabs();
  load();

  return h('section', { class: 'listening panel', 'aria-label': 'O que você ouve' },
    h('div', {},
      h('h2', { class: 'section-title' }, 'O que você ouve'),
      h('p', { class: 'section-sub' }, 'Suas músicas do Spotify, com as mais próximas do seu nível primeiro.')),
    tabsSlot,
    list);
}
