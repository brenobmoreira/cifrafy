import { h, plural } from '../dom.js';
import { ChordDiagram } from './ChordDiagram.js';

export function benefitText(item) {
  if (item.songs_unlocked_easy > 0) {
    return `Deixa ${plural(item.songs_unlocked_easy, 'música')} no nível Fácil`;
  }
  return `Facilita ${plural(item.songs_improved, 'música')}`;
}

export function NextChordSpotlight(item) {
  const examples = (item.examples || []).slice(0, 2).map((e) => e.track_name).join(', ');
  return h(
    'aside',
    { class: 'spotlight' },
    ChordDiagram(item.chord, { size: 'sm' }),
    h(
      'div',
      { class: 'spotlight__body' },
      h('p', { class: 'eyebrow' }, 'Próximo acorde para aprender'),
      h('h2', { class: 'spotlight__title' }, item.chord),
      h('p', { class: 'spotlight__text' }, benefitText(item)),
      examples && h('p', { class: 'spotlight__examples' }, `Como ${examples}`),
      h('a', { class: 'text-link', href: '#/acordes' }, 'Ver todos os acordes'),
    ),
  );
}
