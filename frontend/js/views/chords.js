import { api } from '../api.js';
import { h, plural, replaceContent } from '../dom.js';
import { COMMON_CHORDS, parseChord } from '../chords.js';
import { store } from '../store.js';
import { ChordDiagram } from '../components/ChordDiagram.js';
import { benefitText } from '../components/NextChord.js';
import { icon } from '../components/icons.js';
import { EmptyState, ErrorState, Skeleton, toast } from '../components/states.js';

export function ChordsView({ signal }) {
  const knownList = h('ul', { class: 'known-list', 'aria-label': 'Acordes que você já toca' });
  const quickAdd = h('div', { class: 'quick-add' });
  const ranking = h('div', {}, Skeleton({ count: 3, variant: 'row' }));
  const preview = h('div', { class: 'chord-preview' });
  const input = h('input', {
    id: 'new-chord',
    class: 'input',
    placeholder: 'Ex.: F, Bm, C7M',
    autocomplete: 'off',
    autocapitalize: 'off',
    spellcheck: false,
  });
  const feedback = h('p', { class: 'form-feedback', role: 'status' });
  const barreInput = h('input', {
    type: 'checkbox',
    class: 'switch__input',
    checked: store.profile.avoid_barre,
    onchange: async (event) => {
      try {
        await store.saveProfile({ avoid_barre: event.target.checked });
        loadRanking();
      } catch (error) {
        event.target.checked = store.profile.avoid_barre;
        toast(error.message, 'error');
      }
    },
  });

  const known = () => store.profile.known_chords || [];
  const learning = () => store.profile.learning_chords || [];

  async function saveChords(patch) {
    try {
      await store.saveProfile(patch);
      renderProfile();
      loadRanking();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  const addKnown = (chord) => {
    if (known().includes(chord)) return Promise.resolve();
    return saveChords({
      known_chords: [...known(), chord],
      learning_chords: learning().filter((item) => item !== chord),
    });
  };
  const removeKnown = (chord) => saveChords({ known_chords: known().filter((item) => item !== chord) });
  const toggleLearning = (chord) => saveChords({
    learning_chords: learning().includes(chord)
      ? learning().filter((item) => item !== chord)
      : [...learning(), chord],
  });

  function renderProfile() {
    barreInput.checked = store.profile.avoid_barre;
    replaceContent(knownList, known().map((chord) =>
      h('li', { class: 'known-chip' },
        h('button', {
          class: 'known-chip__name', type: 'button',
          onclick: () => replaceContent(preview, ChordDiagram(chord, { size: 'md' })),
        }, chord),
        h('button', {
          class: 'known-chip__remove', type: 'button', 'aria-label': `Remover ${chord}`,
          onclick: () => removeKnown(chord),
        }, icon('close', 14)))));

    replaceContent(quickAdd, Object.entries(COMMON_CHORDS).map(([group, chords]) => {
      const missing = chords.filter((chord) => !known().includes(chord));
      if (!missing.length) return null;
      return h('div', { class: 'quick-add__group' },
        h('h3', { class: 'quick-add__title' }, group),
        h('div', { class: 'quick-add__chips' }, missing.map((chord) =>
          h('button', {
            class: 'chip chip--add', type: 'button', 'aria-label': `Adicionar ${chord}`,
            onclick: () => addKnown(chord),
          }, icon('plus', 13), chord))));
    }));
  }

  function RankRow(item, index) {
    const isLearning = learning().includes(item.chord);
    const examples = (item.examples || []).slice(0, 3).map((e) => e.track_name).join(', ');
    return h('li', { class: 'rank', style: { '--i': index } },
      ChordDiagram(item.chord, { size: 'sm' }),
      h('div', { class: 'rank__body' },
        h('p', { class: 'rank__position' }, `${index + 1}º`),
        h('h3', { class: 'rank__chord' }, item.chord,
          item.is_barre && h('span', { class: 'tag' }, 'pestana')),
        h('p', { class: 'rank__benefit' }, benefitText(item)),
        item.songs_improved > item.songs_unlocked_easy && item.songs_unlocked_easy > 0 &&
          h('p', { class: 'rank__extra' }, `Melhora ${plural(item.songs_improved, 'música')} no total`),
        examples && h('p', { class: 'rank__examples' }, examples),
        h('div', { class: 'rank__actions' },
          h('button', { class: 'btn btn--primary btn--small', type: 'button', onclick: () => addKnown(item.chord) }, 'Já sei tocar'),
          h('button', {
            class: 'btn btn--ghost btn--small', type: 'button', 'aria-pressed': isLearning,
            onclick: () => toggleLearning(item.chord),
          }, isLearning ? 'Praticando' : 'Quero praticar'))));
  }

  async function loadRanking() {
    replaceContent(ranking, Skeleton({ count: 3, variant: 'row' }));
    try {
      const items = await api.nextChords({ limit: 6 }, signal);
      replaceContent(ranking, items.length
        ? h('ol', { class: 'rank-list' }, items.map(RankRow))
        : EmptyState({
            title: 'Nada para sugerir ainda',
            text: 'Analise músicas de uma playlist para descobrir qual acorde vale mais a pena aprender.',
          }));
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(ranking, ErrorState({ error, onRetry: loadRanking }));
    }
  }

  function onSubmit(event) {
    event.preventDefault();
    const value = input.value.trim();
    feedback.textContent = '';
    if (!value) return;
    if (!parseChord(value)) {
      feedback.textContent = `"${value}" não parece um acorde. Tente algo como Bm ou F7M.`;
      return;
    }
    input.value = '';
    addKnown(value);
  }

  const unsubscribe = store.subscribe(renderProfile);
  signal.addEventListener('abort', unsubscribe);
  renderProfile();
  loadRanking();

  return h(
    'section',
    { class: 'view view--chords' },
    h('header', { class: 'page-head' },
      h('div', {}, h('p', { class: 'eyebrow' }, 'Sua mão esquerda'), h('h1', { class: 'page-title' }, 'Acordes'))),
    h('div', { class: 'chords-layout' },
      h('div', { class: 'panel' },
        h('h2', { class: 'section-title' }, 'O que você já toca'),
        knownList,
        preview,
        h('form', { class: 'add-chord', onsubmit: onSubmit },
          h('label', { for: 'new-chord' }, 'Adicionar acorde'),
          h('div', { class: 'import-form__row' }, input,
            h('button', { class: 'btn btn--primary', type: 'submit' }, 'Adicionar')),
          feedback),
        quickAdd,
        h('label', { class: 'switch switch--block' }, barreInput,
          h('span', { class: 'switch__track', 'aria-hidden': 'true' }),
          h('span', {}, 'Evitar pestana', h('small', {}, 'Músicas com pestana sobem de nível')))),
      h('div', { class: 'panel panel--plain' },
        h('h2', { class: 'section-title' }, 'Próximos para aprender'),
        h('p', { class: 'section-sub' }, 'Ordenados pelo que mais destrava as músicas da sua biblioteca.'),
        ranking)),
  );
}
