// Chord parsing and guitar shapes. Pure logic with no DOM.
// Shapes run from the low E string to the high E string. -1 means muted.

const NOTE_INDEX = {
  C: 0, 'B#': 0, 'C#': 1, Db: 1, D: 2, 'D#': 3, Eb: 3, E: 4, Fb: 4, F: 5, 'E#': 5,
  'F#': 6, Gb: 6, G: 7, 'G#': 8, Ab: 8, A: 9, 'A#': 10, Bb: 10, B: 11, Cb: 11,
};
const NOTES_SHARP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const NOTES_FLAT = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B'];

const x = -1;
const OPEN_SHAPES = {
  C: [x, 3, 2, 0, 1, 0], Cmaj7: [x, 3, 2, 0, 0, 0], C7: [x, 3, 2, 3, 1, 0],
  Cadd9: [x, 3, 2, 0, 3, 0], Csus4: [x, 3, 3, 0, 1, 1],
  D: [x, x, 0, 2, 3, 2], Dm: [x, x, 0, 2, 3, 1], D7: [x, x, 0, 2, 1, 2], Dm7: [x, x, 0, 2, 1, 1],
  Dmaj7: [x, x, 0, 2, 2, 2], Dsus4: [x, x, 0, 2, 3, 3], Dsus2: [x, x, 0, 2, 3, 0],
  E: [0, 2, 2, 1, 0, 0], Em: [0, 2, 2, 0, 0, 0], E7: [0, 2, 0, 1, 0, 0], Em7: [0, 2, 0, 0, 0, 0],
  Emaj7: [0, 2, 1, 1, 0, 0], Esus4: [0, 2, 2, 2, 0, 0],
  Fmaj7: [x, x, 3, 2, 1, 0],
  G: [3, 2, 0, 0, 0, 3], G7: [3, 2, 0, 0, 0, 1], Gmaj7: [3, 2, 0, 0, 0, 2], G6: [3, 2, 0, 0, 0, 0],
  A: [x, 0, 2, 2, 2, 0], Am: [x, 0, 2, 2, 1, 0], A7: [x, 0, 2, 0, 2, 0], Am7: [x, 0, 2, 0, 1, 0],
  Amaj7: [x, 0, 2, 1, 2, 0], Asus4: [x, 0, 2, 2, 3, 0], Asus2: [x, 0, 2, 2, 0, 0],
  A6: [x, 0, 2, 2, 2, 2],
  B7: [x, 2, 1, 2, 0, 2],
};

const SLASH_SHAPES = {
  'C/E': [0, 3, 2, 0, 1, 0], 'C/G': [3, 3, 2, 0, 1, 0], 'D/F#': [2, x, 0, 2, 3, 2],
  'D/A': [x, 0, 0, 2, 3, 2], 'G/B': [x, 2, 0, 0, 0, 3], 'G/F': [1, x, 0, 0, 0, 3],
  'Am/G': [3, 0, 2, 2, 1, 0], 'F/A': [x, 0, 3, 2, 1, 1], 'A/E': [0, 0, 2, 2, 2, 0],
};

// Offsets from the root fret on the low E or A string.
// `_` is a muted string here, because -1 is a valid offset.
const _ = null;
const MOVABLE = {
  maj: { E: [0, 2, 2, 1, 0, 0], A: [_, 0, 2, 2, 2, 0], barre: true },
  m: { E: [0, 2, 2, 0, 0, 0], A: [_, 0, 2, 2, 1, 0], barre: true },
  7: { E: [0, 2, 0, 1, 0, 0], A: [_, 0, 2, 0, 2, 0], barre: true },
  m7: { E: [0, 2, 0, 0, 0, 0], A: [_, 0, 2, 0, 1, 0], barre: true },
  maj7: { A: [_, 0, 2, 1, 2, 0], barre: true },
  sus4: { E: [0, 2, 2, 2, 0, 0], A: [_, 0, 2, 2, 3, 0], barre: true },
  sus2: { A: [_, 0, 2, 2, 0, 0], barre: true },
  dim7: { E: [0, _, -1, 0, -1, _], A: [_, 0, 1, -1, 1, _], barre: false },
  m7b5: { E: [0, _, 0, 0, -1, _], A: [_, 0, 1, 0, 1, _], barre: false },
};

const CHORD_TOKEN =
  /^([A-G][#b]?)(min|m(?!aj)|dim|aug|°|º|\+)?((?:maj|add|sus|dim|aug|M|m|[0-9#b+\-°º]|\([^)]*\)|\/[#b]?\d+)*)(?:\/([A-G][#b]?))?$/;

export function parseChord(name) {
  const match = CHORD_TOKEN.exec(String(name).trim());
  if (!match) return null;
  const [, root, quality = '', extension = '', bass = null] = match;
  return { root, quality, extension, bass, rootIndex: NOTE_INDEX[root] };
}

// Separates surrounding notation from a sheet token: "(G" -> "(", "G", "".
export function splitToken(token) {
  let core = token;
  let lead = '';
  let trail = '';
  while (core && '(|'.includes(core[0])) {
    lead += core[0];
    core = core.slice(1);
  }
  const count = (text, char) => text.split(char).length - 1;
  while (core && ('|,;.'.includes(core.at(-1)) || (core.at(-1) === ')' && count(core, ')') > count(core, '(')))) {
    trail = core.at(-1) + trail;
    core = core.slice(0, -1);
  }
  return { lead, core, trail };
}

function classify({ quality, extension }) {
  const ext = extension.replace(/\s/g, '');
  if (quality === 'aug' || quality === '+' || ext.includes('aug')) return null;
  if (['dim', '°', 'º'].includes(quality) || /dim|°|º/.test(ext)) {
    return { type: 'dim7', exact: !ext || /^7?$/.test(ext) };
  }
  const minor = quality === 'm' || quality === 'min';
  if (minor && ext.includes('b5')) return { type: 'm7b5', exact: /^7?\(?b5\)?$/.test(ext) };
  if (ext.includes('b5') || ext.includes('#5')) return null;

  if (minor) {
    if (!ext) return { type: 'm', exact: true };
    if (ext === '7') return { type: 'm7', exact: true };
    return { type: /(^|[^b#])7/.test(ext) && !/7M|maj7/.test(ext) ? 'm7' : 'm', exact: false };
  }
  if (!ext) return { type: 'maj', exact: true };
  if (['7M', 'maj7', 'M7', '7+'].includes(ext)) return { type: 'maj7', exact: true };
  if (ext === '7') return { type: '7', exact: true };
  if (ext === 'sus4' || ext === '4') return { type: 'sus4', exact: true };
  if (ext === 'sus2' || ext === '2') return { type: 'sus2', exact: true };
  if (ext === 'add9' || ext === '6') return { type: ext, exact: true };
  if (/7M|maj7/.test(ext)) return { type: 'maj7', exact: false };
  if (/sus4|\(4\)/.test(ext) && !ext.includes('7')) return { type: 'sus4', exact: false };
  if (/(^|[^b#])7/.test(ext)) return { type: '7', exact: false };
  return { type: 'maj', exact: false };
}

function suffixFor(type) {
  return type === 'maj' ? '' : type;
}

function fromMovable(rootIndex, type) {
  const shape = MOVABLE[type];
  if (!shape) return null;
  const options = [];
  for (const [string, openNote] of [['E', 4], ['A', 9]]) {
    const offsets = shape[string];
    if (!offsets) continue;
    let fret = (rootIndex - openNote + 12) % 12;
    if (fret === 0) fret = 12;
    if (offsets.some((o) => o !== _ && fret + o < 0)) continue;
    options.push({ fret, offsets });
  }
  if (!options.length) return null;
  options.sort((a, b) => a.fret - b.fret);
  const { fret, offsets } = options[0];
  const frets = offsets.map((o) => (o === _ ? x : fret + o));
  const played = frets.map((f, index) => (f === fret ? index : -1)).filter((index) => index >= 0);
  const barre = shape.barre && played.length > 1
    ? { fret, from: played[0], to: played.at(-1) }
    : null;
  return { frets, barre };
}

function openShape(rootIndex, type) {
  const suffix = suffixFor(type);
  return OPEN_SHAPES[`${NOTES_SHARP[rootIndex]}${suffix}`] ||
    OPEN_SHAPES[`${NOTES_FLAT[rootIndex]}${suffix}`] || null;
}

function withWindow(shape) {
  const fretted = shape.frets.filter((f) => f > 0);
  const max = fretted.length ? Math.max(...fretted) : 0;
  const min = fretted.length ? Math.min(...fretted) : 1;
  return { ...shape, baseFret: max <= 5 ? 1 : min };
}

// Returns { frets, barre, baseFret, note } or null when no reliable shape exists.
export function chordShape(name) {
  const parsed = parseChord(name);
  if (!parsed) return null;

  if (parsed.bass) {
    const base = `${parsed.root}${parsed.quality}${parsed.extension}`;
    const slash = SLASH_SHAPES[`${base}/${parsed.bass}`];
    if (slash) return withWindow({ frets: slash, barre: null, note: null });
  }

  const kind = classify(parsed);
  if (!kind) return null;
  const open = openShape(parsed.rootIndex, kind.type);
  const shape = open ? { frets: open, barre: null } : fromMovable(parsed.rootIndex, kind.type);
  const notes = [];
  if (!shape) {
    if (kind.type !== 'add9' && kind.type !== '6') return null;
    const majorOpen = openShape(parsed.rootIndex, 'maj');
    const fallback = majorOpen ? { frets: majorOpen, barre: null } : fromMovable(parsed.rootIndex, 'maj');
    if (!fallback) return null;
    notes.push('Forma básica, sem a extensão');
    if (parsed.bass) notes.push(`Baixo em ${parsed.bass}`);
    return withWindow({ ...fallback, note: notes.join('. ') });
  }

  if (!kind.exact) notes.push('Forma básica, sem a extensão');
  if (parsed.bass) notes.push(`Baixo em ${parsed.bass}`);
  return withWindow({ ...shape, note: notes.join('. ') || null });
}

export function transposeKey(key, semitones) {
  if (!key) return null;
  const match = /^([A-G][#b]?)(.*)$/.exec(key);
  if (!match) return key;
  const index = (NOTE_INDEX[match[1]] + semitones + 120) % 12;
  const names = match[1].includes('b')
    ? NOTES_FLAT
    : ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'];
  return `${names[index]}${match[2]}`;
}

const MAJOR_DEGREES = [[0, ''], [2, 'm'], [4, 'm'], [5, ''], [7, ''], [9, 'm']];

// The key the sheet is written in. Sites sometimes state the recording key while the chords
// are written for another tuning (Eb stated, chords in E), so the stated key only breaks ties.
// `freqs` are the chords as shown, `shown` is how many semitones they were moved from the original.
export function writtenKey(statedKey, freqs, shown = 0) {
  const stated = /^([A-G][#b]?)(m?)/.exec(statedKey || '');
  if (!stated || !freqs) return statedKey || null;
  const minor = stated[2] === 'm';
  const parsed = Object.entries(freqs)
    .map(([name, count]) => ({ chord: parseChord(name), count }))
    .filter((item) => item.chord);
  if (!parsed.length) return statedKey;
  const fit = (shift) => {
    const tonic = (NOTE_INDEX[stated[1]] + shown + shift + (minor ? 3 : 0) + 120) % 12;
    return parsed.reduce((sum, { chord, count }) => {
      const quality = chord.quality === 'm' ? 'm' : chord.quality === '' ? '' : null;
      const degree = (chord.rootIndex - tonic + 12) % 12;
      return MAJOR_DEGREES.some(([d, q]) => d === degree && q === quality) ? sum + count : sum;
    }, 0);
  };
  let best = 0;
  let bestScore = fit(0);
  for (const shift of [1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6]) {
    const score = fit(shift);
    if (score > bestScore) {
      best = shift;
      bestScore = score;
    }
  }
  return best ? transposeKey(statedKey, best) : statedKey;
}

// How to reach the user's preferred key from a song key. Minor songs aim at the relative minor.
// `semitones` moves the shapes there; `capo` keeps the original sound while playing those shapes.
export function preferredKeyPlan(songKey, preferredKey) {
  const song = /^([A-G][#b]?)(m?)/.exec(songKey || '');
  const preferred = /^([A-G][#b]?)/.exec(preferredKey || '');
  if (!song || !preferred) return null;
  const minor = song[2] === 'm';
  const from = NOTE_INDEX[song[1]];
  const target = (NOTE_INDEX[preferred[1]] + (minor ? 9 : 0)) % 12;
  const up = (target - from + 12) % 12;
  const capo = (12 - up) % 12;
  return {
    target: transposeKey(`${preferred[1]}${minor ? 'm' : ''}`, minor ? 9 : 0) || preferred[1],
    semitones: normalizeSemitones(up),
    capo: capo >= 1 && capo <= 7 ? capo : null,
  };
}

export function normalizeSemitones(value) {
  const wrapped = ((value % 12) + 12) % 12;
  return wrapped > 6 ? wrapped - 12 : wrapped;
}

export const COMMON_CHORDS = {
  Abertos: ['C', 'D', 'E', 'G', 'A', 'Am', 'Dm', 'Em', 'C7', 'D7', 'E7', 'G7', 'A7', 'B7', 'Am7', 'Em7', 'Dm7'],
  'Com pestana': ['F', 'Bm', 'B', 'Bb', 'F#m', 'C#m', 'Fm', 'Gm', 'Cm', 'Eb', 'Ab', 'F#'],
};
