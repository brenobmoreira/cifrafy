import { h } from './dom.js';
import { createRouter } from './router.js';
import { store } from './store.js';
import { study } from './study.js';
import { icon } from './components/icons.js';
import { StudyTimerControl } from './components/StudyTimer.js';
import { toast } from './components/states.js';
import { ChordsView } from './views/chords.js';
import { PlayerView } from './views/player.js';
import { PlaylistDetailView, PlaylistsView } from './views/playlists.js';
import { PracticeView } from './views/practice.js';
import { ProgressView } from './views/progress.js';
import { RepertoireView } from './views/repertoire.js';
import { TodayView } from './views/today.js';

const NAV = [
  { key: 'hoje', href: '#/hoje', label: 'Hoje', iconName: 'today' },
  { key: 'treino', href: '#/treino', label: 'Treino', iconName: 'metronome' },
  { key: 'playlists', href: '#/playlists', label: 'Playlists', iconName: 'list' },
  { key: 'repertorio', href: '#/repertorio', label: 'Repertório', iconName: 'bookmark' },
  { key: 'acordes', href: '#/acordes', label: 'Acordes', iconName: 'grid' },
];

const routes = [
  { path: '/hoje', nav: 'hoje', title: 'Hoje', view: TodayView },
  { path: '/treino', nav: 'treino', title: 'Treino', view: PracticeView },
  { path: '/progresso', nav: 'treino', title: 'Progresso', view: ProgressView },
  { path: '/playlists', nav: 'playlists', title: 'Playlists', view: PlaylistsView },
  { path: '/playlists/:id', nav: 'playlists', title: 'Playlist', view: PlaylistDetailView },
  { path: '/tocar/:artist/:track', nav: 'tocar', title: 'Tocar', view: PlayerView },
  { path: '/repertorio', nav: 'repertorio', title: 'Repertório', view: RepertoireView },
  { path: '/acordes', nav: 'acordes', title: 'Acordes', view: ChordsView },
];

function Brand() {
  return h('a', { class: 'brand', href: '#/hoje', 'aria-label': 'Cifrafy, início' },
    h('span', { class: 'brand__mark', 'aria-hidden': 'true' },
      Array.from({ length: 4 }, () => h('i'))),
    h('span', { class: 'brand__name' }, 'Cifrafy'));
}

function handleSpotifyReturn() {
  const url = new URL(window.location.href);
  const result = url.searchParams.get('spotify');
  if (!result) return;
  const messages = {
    connected: ['Spotify conectado.', 'ok'],
    denied: ['Conexão com o Spotify cancelada.', 'info'],
    error: ['Não foi possível conectar ao Spotify. Tente de novo.', 'error'],
  };
  const [message, tone] = messages[result] || messages.error;
  toast(message, tone);
  url.searchParams.delete('spotify');
  window.history.replaceState(null, '', url);
}

function start() {
  const navLinks = NAV.map((item) =>
    h('a', { class: 'nav__link', href: item.href, dataset: { key: item.key } },
      icon(item.iconName), h('span', {}, item.label)));
  const indexed = h('span', { class: 'header-stat' });
  const outlet = h('main', { id: 'main', class: 'outlet', tabIndex: -1 });

  document.getElementById('app').replaceChildren(
    h('a', { class: 'skip-link', href: '#main', onclick: (event) => {
      event.preventDefault();
      outlet.focus();
    } }, 'Ir para o conteúdo'),
    h('header', { class: 'app-header' },
      Brand(),
      h('nav', { class: 'nav', 'aria-label': 'Principal' }, navLinks),
      h('div', { class: 'header-tools' }, StudyTimerControl(), indexed)),
    outlet,
  );

  store.subscribe(() => {
    const total = store.status?.indexed_songs_count;
    indexed.textContent = total === undefined ? '' : `${total} ${total === 1 ? 'cifra salva' : 'cifras salvas'}`;
  });

  const router = createRouter({
    routes,
    outlet,
    notFound: '#/hoje',
    onRoute: (route) => {
      document.title = `${route.title} | Cifrafy`;
      document.body.dataset.route = route.nav;
      navLinks.forEach((link) => {
        if (link.dataset.key === route.nav) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
      });
    },
  });

  handleSpotifyReturn();
  study.refresh().catch(() => {});
  store.refreshStatus()
    .catch(() => {})
    .finally(() => router.start());
}

start();
