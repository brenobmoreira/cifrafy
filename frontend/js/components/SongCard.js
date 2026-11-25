import { h } from '../dom.js';
import { songPath } from '../router.js';
import { Strings } from './states.js';

const LEVEL_CLASS = { Fácil: 'easy', Médio: 'medium', Difícil: 'hard' };
const MAX_CHIPS = 8;

export const REPERTOIRE_LABELS = {
  want: 'Quero aprender',
  practicing: 'Praticando',
  mastered: 'Dominada',
};

export const repertoireStatus = (song) => song?.repertoire?.status ?? null;

export function LevelBadge(level) {
  return h(
    'span',
    { class: `level level--${LEVEL_CLASS[level] || 'unknown'}` },
    h('span', { class: 'level__dot', 'aria-hidden': 'true' }),
    level || 'Sem nota',
  );
}

export function ScoreMeter(score = 0) {
  const value = Math.max(0, Math.min(100, Math.round(score)));
  return h(
    'div',
    {
      class: 'meter',
      role: 'meter',
      'aria-label': 'Facilidade para o seu nível',
      'aria-valuemin': 0,
      'aria-valuemax': 100,
      'aria-valuenow': value,
      style: { '--score': value },
    },
    h('span', { class: 'meter__label', 'aria-hidden': 'true' }, 'Facilidade'),
    h('div', { class: 'meter__track' }, h('div', { class: 'meter__fill' })),
    h('span', { class: 'meter__value' }, value),
  );
}

export function chordChipClass(chord, song) {
  const known = (song.known_barre_chords || []).includes(chord);
  if ((song.barre_chords || []).includes(chord) && !known) return 'chip chip--barre';
  if ((song.unknown_chords || []).includes(chord)) return 'chip chip--unknown';
  return 'chip';
}

function ChordChips(song) {
  const chords = song.unique_chords || [];
  const visible = chords.slice(0, MAX_CHIPS);
  return h(
    'ul',
    { class: 'chips', 'aria-label': 'Acordes da música' },
    visible.map((chord) => h('li', { class: chordChipClass(chord, song) }, chord)),
    chords.length > MAX_CHIPS && h('li', { class: 'chip chip--more' }, `+${chords.length - MAX_CHIPS}`),
  );
}

function capoText(song) {
  const capo = song.capo_suggestion;
  if (!capo) return null;
  if (song.easy_with_capo || capo.makes_easy) return `Fácil com capotraste na ${capo.capo_fret}ª casa`;
  return `Capotraste na ${capo.capo_fret}ª casa reduz as pestanas`;
}

export function SongCard(song, { index = 0, footer = null } = {}) {
  const title = song.track_name || song.name;
  const capo = capoText(song);
  const status = repertoireStatus(song);
  const meta = [song.artist, song.key ? `Tom ${song.key}` : null].filter(Boolean).join(' · ');

  const card = h(
    'article',
    { class: 'song-card', style: { '--i': Math.min(index, 14) } },
    Strings(),
    h(
      'div',
      { class: 'song-card__head' },
      h(
        'div',
        { class: 'song-card__titles' },
        h(
          'h3',
          { class: 'song-card__title' },
          h('a', {
            href: songPath(song.artist, title),
            onclick: () => {
              card.style.viewTransitionName = 'song-hero';
            },
          }, title),
        ),
        h('p', { class: 'song-card__meta' }, meta),
      ),
      LevelBadge(song.level),
    ),
    ScoreMeter(song.score),
    ChordChips(song),
    capo && h('p', { class: 'song-card__capo' }, capo),
    status && h('p', { class: 'song-card__status' }, REPERTOIRE_LABELS[status]),
    footer && h('div', { class: 'song-card__footer' }, footer),
  );
  return card;
}

export function SongGrid(songs, options) {
  return h(
    'div',
    { class: 'song-grid' },
    songs.map((song, index) => SongCard(song, { index, ...(options?.(song) || {}) })),
  );
}
