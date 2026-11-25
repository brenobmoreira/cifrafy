import { api } from '../api.js';
import { h, plural, replaceContent } from '../dom.js';
import { store } from '../store.js';
import { openChangeDrill, formatRate } from '../components/ChangeDrill.js';
import { ChordCheckButton } from '../components/ChordCheck.js';
import { ChordDiagram } from '../components/ChordDiagram.js';
import { PracticeSession } from '../components/PracticeSession.js';
import { PracticeTabs, ShowMore } from '../components/PracticeTabs.js';
import { icon } from '../components/icons.js';
import { EmptyState, ErrorState, LinkButton, Skeleton, toast } from '../components/states.js';

function stepBenefit(step) {
  if (step.songs_unlocked_easy > 0) return `Libera ${plural(step.songs_unlocked_easy, 'música')} no Fácil`;
  return `Facilita ${plural(step.songs_improved, 'música')}`;
}

function TrailStep(step, index, onLearn) {
  const isNext = index === 0;
  return h('li', { class: `trail-step${isNext ? ' is-next' : ''}`, style: { '--i': index } },
    h('span', { class: 'trail-step__node', 'aria-hidden': 'true' }, step.step),
    h('div', { class: 'trail-step__card' },
      ChordDiagram(step.chord, { size: 'sm' }),
      h('div', { class: 'trail-step__body' },
        isNext && h('p', { class: 'eyebrow' }, 'Comece por aqui'),
        h('h3', { class: 'trail-step__chord' }, step.chord,
          step.is_barre && h('span', { class: 'tag' }, 'pestana')),
        h('p', { class: 'trail-step__benefit' }, stepBenefit(step)),
        h('div', { class: 'rank__actions' },
          isNext && h('button', {
            class: 'btn btn--primary btn--small', type: 'button', onclick: () => onLearn(step.chord),
          }, icon('check', 16), 'Já sei tocar'),
          ChordCheckButton(step.chord)))));
}

function ChangeRow(change, index, onTrain) {
  const target = change.target_per_minute || 30;
  const best = change.best_per_minute ?? 0;
  const progress = Math.min(100, Math.round((best / target) * 100));
  return h('li', { class: 'change', style: { '--i': index } },
    h('div', { class: 'change__pair' },
      h('span', { class: 'change__chord' }, change.from),
      h('span', { class: 'change__link', 'aria-hidden': 'true' }),
      h('span', { class: 'change__chord' }, change.to)),
    h('div', { class: 'change__body' },
      change.involves_unknown && h('p', { class: 'change__meta' }, h('span', { class: 'tag' }, 'acorde novo')),
      h('div', { class: 'change__meter', role: 'img', 'aria-label': `Recorde ${formatRate(change.best_per_minute)} de ${target} trocas por minuto` },
        h('span', { style: { width: `${progress}%` } })),
      h('p', { class: 'change__rate' },
        change.best_per_minute === null || change.best_per_minute === undefined
          ? `Ainda sem treino. Meta: ${target}/min`
          : `Recorde ${formatRate(change.best_per_minute)}/min. Meta: ${target}/min`)),
    h('button', { class: 'btn btn--ghost change__train', type: 'button', onclick: () => onTrain(change) },
      icon('play', 16), 'Treinar'));
}

export function PracticeView({ signal }) {
  let playlistId = '';
  const trail = h('div', {}, Skeleton({ count: 3, variant: 'row' }));
  const changes = h('div', {}, Skeleton({ count: 4, variant: 'row' }));
  const summary = h('p', { class: 'page-sub' });
  const source = h('select', {
    id: 'practice-source',
    class: 'select',
    onchange: (event) => {
      playlistId = event.target.value;
      loadAll();
    },
  }, h('option', { value: '' }, 'Toda a biblioteca'));

  async function learn(chord) {
    try {
      await store.saveProfile({
        known_chords: [...store.profile.known_chords, chord],
        learning_chords: (store.profile.learning_chords || []).filter((item) => item !== chord),
      });
      toast(`${chord} adicionado aos seus acordes.`, 'ok');
      loadAll();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function loadTrail() {
    replaceContent(trail, Skeleton({ count: 3, variant: 'row' }));
    try {
      const data = await api.practiceTrail({ playlistId, limit: 5 }, signal);
      summary.textContent = data.total_songs
        ? `Hoje você tem ${data.current_easy} de ${plural(data.total_songs, 'música')} no nível Fácil.`
        : '';
      if (!data.steps.length) {
        replaceContent(trail, EmptyState({
          title: data.total_songs ? 'Nenhum acorde novo por aqui' : 'Sua trilha começa nas suas músicas',
          text: data.total_songs
            ? 'Com os acordes que você sabe, não há outro que deixe essas músicas mais fáceis.'
            : 'Sincronize algumas faixas de uma playlist para montar a trilha.',
          action: data.total_songs ? null : LinkButton({ href: '#/playlists', label: 'Ir para playlists', iconName: 'list' }),
        }));
        return;
      }
      const steps = data.steps.map((step, index) => TrailStep(step, index, learn));
      replaceContent(trail, h('ol', { class: 'trail' }, steps), ShowMore(steps, 2, 'Ver próximos acordes'));
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(trail, ErrorState({ error, onRetry: loadTrail }));
    }
  }

  async function loadChanges({ quiet = false } = {}) {
    if (!quiet) replaceContent(changes, Skeleton({ count: 4, variant: 'row' }));
    try {
      const data = await api.practiceChanges({ playlistId, limit: 8 }, signal);
      const missing = data.songs_without_sequence > 0 && h('p', { class: 'notice practice-notice' },
        `${plural(data.songs_without_sequence, 'música')} ainda sem a cifra salva. Abra para incluir nas trocas. Cada uma baixa uma única vez.`);
      if (!data.changes.length) {
        replaceContent(changes, missing || null, EmptyState({
          title: 'Sem trocas para treinar ainda',
          text: 'Abra a cifra de uma música para o app descobrir quais trocas ela usa.',
          action: LinkButton({ href: '#/hoje', label: 'Escolher música', iconName: 'today' }),
        }));
        return;
      }
      const onTrain = (change) => openChangeDrill(change, { signal, onSaved: () => loadChanges({ quiet: true }) });
      const rows = data.changes.map((change, index) => ChangeRow(change, index, onTrain));
      replaceContent(changes, missing || null, h('ol', { class: 'change-list' }, rows), ShowMore(rows, 4, 'Ver mais trocas'));
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(changes, ErrorState({ error, onRetry: loadChanges }));
    }
  }

  function loadAll() {
    loadTrail();
    loadChanges();
  }

  api.playlists(signal)
    .then((playlists) => {
      source.append(...playlists.map((playlist) => h('option', { value: playlist.id }, playlist.name)));
    })
    .catch(() => {});
  loadAll();

  return h('section', { class: 'view view--practice' },
    PracticeTabs('treino'),
    h('header', { class: 'page-head' },
      h('div', {},
        h('p', { class: 'eyebrow' }, 'Sua evolução'),
        h('h1', { class: 'page-title' }, 'Treino'),
        summary),
      h('div', { class: 'field practice-source' },
        h('label', { for: 'practice-source' }, 'Treinar com'),
        source)),
    h('div', { class: 'panel' },
      h('h2', { class: 'section-title' }, 'Sessão pronta'),
      PracticeSession({ signal, getPlaylistId: () => playlistId })),
    h('details', { class: 'practice-parts' },
      h('summary', { class: 'practice-divider' }, 'Ou treine por partes'),
      h('div', { class: 'practice-layout' },
        h('div', { class: 'panel panel--plain' },
          h('h2', { class: 'section-title' }, 'Próximos acordes'),
          trail),
        h('div', { class: 'panel' },
          h('h2', { class: 'section-title' }, 'Trocas de acordes'),
          changes))));
}
