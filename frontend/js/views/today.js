import { api } from '../api.js';
import { h, plural, replaceContent } from '../dom.js';
import { songPath } from '../router.js';
import { store } from '../store.js';
import { ListeningSection } from '../components/ListeningSection.js';
import { NextChordSpotlight } from '../components/NextChord.js';
import { SongGrid } from '../components/SongCard.js';
import { icon } from '../components/icons.js';
import { EmptyState, ErrorState, LinkButton, Skeleton, Strings } from '../components/states.js';

const LEVELS = [
  { value: 'Fácil', label: 'Fácil' },
  { value: 'Médio', label: 'Médio' },
  { value: 'Difícil', label: 'Difícil' },
  { value: '', label: 'Todas' },
];

function matchesLevel(song, level, withCapo) {
  if (!level) return true;
  if (song.level === level) return true;
  return level === 'Fácil' && withCapo && Boolean(song.easy_with_capo);
}

export function TodayView({ signal }) {
  let loadController = null;
  let searchTimer = null;
  let search = '';

  const results = h('div', { class: 'results', 'aria-live': 'polite' });
  const spotlight = h('div', { class: 'today-spotlight' });
  const count = h('p', { class: 'results-count' });
  const notice = h('div');
  const webSearch = h('div', { class: 'web-search', 'aria-live': 'polite' });
  let webController = null;
  const sourceSelect = h('select', {
    id: 'today-source',
    class: 'select',
    onchange: (event) => {
      store.setPrefs({ source: event.target.value });
      load();
    },
  }, h('option', { value: 'all' }, 'Toda a biblioteca'));

  const levelButtons = LEVELS.map((level) =>
    h('button', {
      type: 'button',
      class: 'segment',
      dataset: { value: level.value },
      'aria-pressed': store.prefs.level === level.value,
      onclick: () => {
        store.setPrefs({ level: level.value });
        syncControls();
        load();
      },
    }, level.label),
  );

  const capoInput = h('input', {
    type: 'checkbox',
    class: 'switch__input',
    checked: store.prefs.withCapo,
    onchange: (event) => {
      store.setPrefs({ withCapo: event.target.checked });
      load();
    },
  });
  const capoField = h('label', { class: 'switch' }, capoInput,
    h('span', { class: 'switch__track', 'aria-hidden': 'true' }),
    h('span', {}, 'Incluir fáceis com capotraste'));

  const searchInput = h('input', {
    id: 'today-search',
    class: 'input',
    type: 'search',
    placeholder: 'Nas suas músicas ou no Cifra Club',
    oninput: (event) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        search = event.target.value.trim();
        renderWebSearch();
        load();
      }, 250);
    },
  });

  function syncControls() {
    levelButtons.forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.value === store.prefs.level));
    });
    capoField.hidden = store.prefs.level !== 'Fácil';
  }

  async function fetchSongs(requestSignal) {
    const { level, withCapo, source } = store.prefs;
    if (source === 'all') {
      const songs = await api.catalog(
        { level, with_capo: level === 'Fácil' ? withCapo : null, search },
        requestSignal,
      );
      return { songs, pending: 0 };
    }
    const tracks = await api.playlistTracks(source, {
      simplified: store.prefs.simplified,
      signal: requestSignal,
    });
    const term = search.toLocaleLowerCase('pt-BR');
    const synced = tracks.filter((track) => track.is_synced);
    const songs = synced
      .filter((track) => matchesLevel(track, level, withCapo))
      .filter((track) => !term || `${track.name} ${track.artist}`.toLocaleLowerCase('pt-BR').includes(term))
      .map((track) => ({ ...track, id: track.song_id, track_name: track.name }))
      .sort((a, b) => (b.score || 0) - (a.score || 0));
    return { songs, pending: tracks.length - synced.length };
  }

  function emptyResults() {
    const indexed = store.status?.indexed_songs_count ?? 0;
    if (!indexed) {
      return EmptyState({
        title: 'Sua biblioteca ainda está vazia',
        text: 'Importe uma playlist e escolha as músicas para analisar.',
        action: LinkButton({ href: '#/playlists', label: 'Importar playlist', iconName: 'plus' }),
      });
    }
    return EmptyState({
      title: 'Nenhuma música nesse filtro',
      text: 'Tente outro nível ou analise mais músicas de uma playlist.',
      action: LinkButton({ href: '#/playlists', label: 'Ver playlists', variant: 'ghost' }),
    });
  }

  async function load() {
    loadController?.abort();
    loadController = new AbortController();
    const requestSignal = AbortSignal.any([signal, loadController.signal]);
    replaceContent(results, Skeleton({ count: 6 }));
    replaceContent(count);
    replaceContent(notice);
    loadSpotlight(requestSignal);

    try {
      const { songs, pending } = await fetchSongs(requestSignal);
      replaceContent(count, plural(songs.length, 'música'));
      if (pending > 0) {
        replaceContent(notice, h('p', { class: 'notice' },
          `${plural(pending, 'faixa')} desta playlist ainda sem análise. `,
          h('a', { class: 'text-link', href: `#/playlists/${encodeURIComponent(store.prefs.source)}` }, 'Escolher quais analisar')));
      }
      replaceContent(results, songs.length ? SongGrid(songs) : emptyResults());
    } catch (error) {
      if (error.name === 'AbortError') return;
      replaceContent(results, ErrorState({ error, onRetry: load }));
    }
  }

  // Songs outside the playlists come from Cifra Club, only on request, to spare its rate limit.
  function renderWebSearch() {
    webController?.abort();
    if (search.length < 2) {
      replaceContent(webSearch, h('p', { class: 'web-search__hint' }, 'Não achou? Digite o nome e procure também no Cifra Club.'));
      return;
    }
    replaceContent(webSearch,
      h('button', { class: 'btn btn--ghost', type: 'button', onclick: runWebSearch },
        icon('search', 16), `Procurar “${search}” no Cifra Club`));
  }

  async function runWebSearch() {
    webController?.abort();
    webController = new AbortController();
    const term = search;
    replaceContent(webSearch, Skeleton({ count: 3, variant: 'row' }));
    try {
      const songs = await api.searchCifraClub(term, AbortSignal.any([signal, webController.signal]));
      const title = h('h2', { class: 'section-title' }, 'No Cifra Club');
      if (!songs.length) {
        replaceContent(webSearch, title, h('p', { class: 'notice' }, `Nada encontrado para “${term}”. Tente o nome da música e do artista.`));
        return;
      }
      replaceContent(webSearch, title,
        h('p', { class: 'section-sub' }, 'Abra uma música para ver a cifra. No player, dá para salvar no repertório.'),
        h('ul', { class: 'listen-list' }, songs.map((song, index) =>
          h('li', { class: 'listen-row', style: { '--i': Math.min(index, 12) } },
            h('div', { class: 'listen-row__titles' },
              h('a', { class: 'listen-row__name', href: songPath(song.artist, song.track_name) }, song.track_name),
              h('p', { class: 'listen-row__artist' }, song.artist))))));
    } catch (error) {
      if (error.name === 'AbortError') return;
      replaceContent(webSearch, ErrorState({ error, onRetry: runWebSearch }));
    }
  }

  async function loadSpotlight(requestSignal) {
    const { source } = store.prefs;
    try {
      const items = await api.nextChords(
        { playlistId: source === 'all' ? null : source, limit: 1 },
        requestSignal,
      );
      replaceContent(spotlight, items[0] ? NextChordSpotlight(items[0]) : null);
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(spotlight);
    }
  }

  async function loadPlaylists() {
    try {
      const playlists = await api.playlists(signal);
      const ids = new Set(playlists.map((playlist) => playlist.id));
      playlists.forEach((playlist) => {
        sourceSelect.append(h('option', { value: playlist.id }, playlist.name));
      });
      if (store.prefs.source !== 'all' && !ids.has(store.prefs.source)) {
        store.setPrefs({ source: 'all' });
      }
      sourceSelect.value = store.prefs.source;
    } catch (error) {
      if (error.name !== 'AbortError') store.setPrefs({ source: 'all' });
    }
  }

  signal.addEventListener('abort', () => {
    clearTimeout(searchTimer);
    loadController?.abort();
    webController?.abort();
  });

  syncControls();
  renderWebSearch();
  loadPlaylists().then(() => {
    if (!signal.aborted) load();
  });
  replaceContent(results, Skeleton({ count: 6 }));

  return h(
    'section',
    { class: 'view view--today' },
    h(
      'header',
      { class: 'hero' },
      Strings(),
      h('p', { class: 'eyebrow' }, 'Pegue o violão'),
      h('h1', { class: 'hero__title' }, 'O que tocar hoje'),
      h('p', { class: 'hero__text' }, 'Músicas das suas playlists, ordenadas pelo que você já consegue tocar.'),
    ),
    h(
      'div',
      { class: 'filters' },
      h('div', { class: 'segmented', role: 'group', 'aria-label': 'Dificuldade' }, levelButtons),
      h('div', { class: 'filters__row' },
        h('div', { class: 'field' }, h('label', { for: 'today-source' }, 'De onde'), sourceSelect),
        h('div', { class: 'field' }, h('label', { for: 'today-search' }, 'Buscar'), searchInput)),
      webSearch,
      capoField,
    ),
    spotlight,
    h('div', { class: 'results-head' }, h('h2', { class: 'section-title' }, 'Sua lista'), count),
    notice,
    results,
    ListeningSection({ signal }),
  );
}
