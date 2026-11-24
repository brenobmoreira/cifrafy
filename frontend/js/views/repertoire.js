import { api } from '../api.js';
import { h, plural, replaceContent } from '../dom.js';
import { REPERTOIRE_LABELS, repertoireStatus, SongGrid } from '../components/SongCard.js';
import { EmptyState, ErrorState, LinkButton, Skeleton, toast } from '../components/states.js';

const STATUSES = Object.keys(REPERTOIRE_LABELS);

const EMPTY_TEXT = {
  want: 'Abra uma música e marque como Quero aprender.',
  practicing: 'Marque aqui o que está no seu treino da semana.',
  mastered: 'As músicas que você já toca de cabo a rabo aparecem aqui.',
};

const normalize = (song) => ({
  ...song,
  repertoire: song.repertoire ?? (song.status ? { status: song.status } : null),
});

export function RepertoireView({ signal }) {
  let songs = [];
  let active = 'practicing';
  const body = h('div', {}, Skeleton({ count: 4 }));

  const tabs = STATUSES.map((status) =>
    h('button', {
      type: 'button',
      class: 'segment',
      dataset: { value: status },
      'aria-pressed': status === active,
      onclick: () => {
        active = status;
        render();
      },
    }),
  );

  async function move(song, status) {
    const songId = song.id ?? song.song_id;
    try {
      if (status === 'remove') {
        await api.removeRepertoire(songId);
        songs = songs.filter((item) => (item.id ?? item.song_id) !== songId);
      } else {
        await api.saveRepertoire(songId, {
          status,
          preferred_semitones: song.repertoire?.preferred_semitones ?? 0,
          preferred_capo: song.repertoire?.preferred_capo ?? 0,
        });
        songs = songs.map((item) => ((item.id ?? item.song_id) === songId
          ? { ...item, repertoire: { ...item.repertoire, status } }
          : item));
      }
      render();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  function MoveSelect(song) {
    return h('label', { class: 'move' },
      h('span', {}, 'Mover para'),
      h('select', {
        class: 'select select--small',
        onchange: (event) => move(song, event.target.value),
      },
      STATUSES.map((status) =>
        h('option', { value: status, selected: status === repertoireStatus(song) }, REPERTOIRE_LABELS[status])),
      h('option', { value: 'remove' }, 'Remover')));
  }

  function render() {
    tabs.forEach((tab) => {
      const status = tab.dataset.value;
      const total = songs.filter((song) => repertoireStatus(song) === status).length;
      tab.setAttribute('aria-pressed', String(status === active));
      tab.textContent = `${REPERTOIRE_LABELS[status]} (${total})`;
    });
    const visible = songs.filter((song) => repertoireStatus(song) === active);
    replaceContent(body, visible.length
      ? SongGrid(visible, (song) => ({ footer: MoveSelect(song) }))
      : EmptyState({
          title: `Nada em ${REPERTOIRE_LABELS[active]}`,
          text: EMPTY_TEXT[active],
          action: LinkButton({ href: '#/hoje', label: 'Escolher músicas', variant: 'ghost' }),
        }));
  }

  async function load() {
    replaceContent(body, Skeleton({ count: 4 }));
    try {
      songs = (await api.repertoire(null, signal)).map(normalize);
      if (!songs.some((song) => repertoireStatus(song) === active) && songs.length) {
        active = repertoireStatus(songs[0]) || active;
      }
      summary.textContent = plural(songs.length, 'música');
      render();
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(body, ErrorState({ error, onRetry: load }));
    }
  }

  const summary = h('p', { class: 'page-sub' });
  STATUSES.forEach((status, index) => {
    tabs[index].textContent = REPERTOIRE_LABELS[status];
  });
  load();

  return h(
    'section',
    { class: 'view view--repertoire' },
    h('header', { class: 'page-head' },
      h('div', {}, h('p', { class: 'eyebrow' }, 'Seu caminho'), h('h1', { class: 'page-title' }, 'Repertório'), summary)),
    h('div', { class: 'segmented segmented--scroll', role: 'group', 'aria-label': 'Status' }, tabs),
    body,
  );
}
