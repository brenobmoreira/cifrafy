// Chord check from an audio spectrum. Pure logic with no DOM or audio APIs.
import { parseChord } from '../chords.js';

const MIN_HZ = 70;
const MAX_HZ = 1600;
const SILENCE_DB = -62;
const MATCH_THRESHOLD = 0.78;
const MATCH_MARGIN = 0.04;
const NOTE_NAMES = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'];

const NOTE_INDEX = {
  C: 0, 'B#': 0, 'C#': 1, Db: 1, D: 2, 'D#': 3, Eb: 3, E: 4, Fb: 4, F: 5, 'E#': 5,
  'F#': 6, Gb: 6, G: 7, 'G#': 8, Ab: 8, A: 9, 'A#': 10, Bb: 10, B: 11, Cb: 11,
};

// Pitch classes of a chord, or null when the name is not a chord.
export function chordNotes(name) {
  const chord = parseChord(name);
  if (!chord) return null;
  const { quality, extension: ext, bass, rootIndex } = chord;
  let third = 4;
  let fifth = 7;
  if (quality === 'm' || quality === 'min') third = 3;
  if (['dim', '°', 'º'].includes(quality)) {
    third = 3;
    fifth = 6;
  }
  if (quality === 'aug' || quality === '+') fifth = 8;
  if (/b5|-5/.test(ext)) fifth = 6;
  if (/#5/.test(ext)) fifth = 8;
  if (/sus2/.test(ext)) third = 2;
  else if (/sus4?|^4|\(4\)/.test(ext)) third = 5;

  const intervals = new Set([0, third, fifth]);
  if (/7M|maj7|M7|7\+/.test(ext)) intervals.add(11);
  else if (/7/.test(ext)) intervals.add(['dim', '°', 'º'].includes(quality) ? 9 : 10);
  if (/(^|[^0-9])6/.test(ext)) intervals.add(9);
  if (/b9/.test(ext)) intervals.add(1);
  else if (/9/.test(ext)) intervals.add(2);

  const notes = new Set([...intervals].map((interval) => (rootIndex + interval) % 12));
  if (bass && NOTE_INDEX[bass] !== undefined) notes.add(NOTE_INDEX[bass]);
  return [...notes];
}

// Folds spectrum peaks into 12 pitch classes. `spectrum` holds decibels, as AnalyserNode gives.
export function spectrumToChroma(spectrum, { sampleRate, fftSize }) {
  const binHz = sampleRate / fftSize;
  const first = Math.max(1, Math.ceil(MIN_HZ / binHz));
  const last = Math.min(spectrum.length - 2, Math.floor(MAX_HZ / binHz));
  const chroma = new Array(12).fill(0);
  let loudest = -Infinity;

  for (let bin = first; bin <= last; bin += 1) {
    const db = spectrum[bin];
    if (!Number.isFinite(db)) continue;
    if (db > loudest) loudest = db;
    if (db < SILENCE_DB || db < spectrum[bin - 1] || db < spectrum[bin + 1]) continue;
    const hz = bin * binHz;
    const midi = 69 + 12 * Math.log2(hz / 440);
    const pitchClass = ((Math.round(midi) % 12) + 12) % 12;
    // High partials are mostly overtones, so they count less than the notes played.
    const weight = hz < 700 ? 1 : 0.5;
    chroma[pitchClass] += weight * 10 ** (db / 20);
  }

  const norm = Math.hypot(...chroma);
  return { chroma: norm ? chroma.map((value) => value / norm) : chroma, loudest };
}

function similarity(chroma, notes) {
  const hit = notes.reduce((sum, note) => sum + chroma[note], 0);
  return hit / Math.sqrt(notes.length);
}

const COMMON_TEMPLATES = NOTE_NAMES.flatMap((root) => [root, `${root}m`])
  .map((name) => ({ name, notes: chordNotes(name) }));

export function matchChroma(chroma, expected) {
  const expectedNotes = chordNotes(expected);
  if (!expectedNotes) return { detected: null, confidence: 0, match: false };
  const expectedScore = similarity(chroma, expectedNotes);
  let best = { name: expected, score: expectedScore };
  for (const template of COMMON_TEMPLATES) {
    const score = similarity(chroma, template.notes);
    if (score > best.score + 1e-9) best = { name: template.name, score };
  }
  const match = expectedScore >= MATCH_THRESHOLD && expectedScore >= best.score - MATCH_MARGIN;
  return {
    detected: match ? expected : best.name,
    confidence: Math.round(Math.min(1, match ? expectedScore : best.score) * 100) / 100,
    match,
  };
}

export function detectChord(spectrum, { sampleRate, fftSize, expected }) {
  const { chroma, loudest } = spectrumToChroma(spectrum, { sampleRate, fftSize });
  if (loudest < SILENCE_DB || !chroma.some(Boolean)) {
    return { detected: null, confidence: 0, match: false, silent: true };
  }
  return { ...matchChroma(chroma, expected), silent: false };
}
