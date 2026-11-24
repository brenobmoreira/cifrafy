import { h, s } from '../dom.js';
import { chordShape } from '../chords.js';

const STRING_GAP = 18;
const FRET_GAP = 24;
const LEFT = 20;
const TOP = 26;
const FRETS = 5;

const stringX = (string) => LEFT + string * STRING_GAP;

function diagramSvg(name, shape) {
  const { frets, barre, baseFret } = shape;
  const right = stringX(5);
  const bottom = TOP + FRETS * FRET_GAP;
  const fretY = (fret) => TOP + (fret - baseFret + 0.5) * FRET_GAP;
  const parts = [];

  for (let fret = 0; fret <= FRETS; fret += 1) {
    const y = TOP + fret * FRET_GAP;
    const isNut = fret === 0 && baseFret === 1;
    parts.push(s('line', { class: isNut ? 'dg-nut' : 'dg-fret', x1: LEFT, x2: right, y1: y, y2: y }));
  }
  for (let string = 0; string < 6; string += 1) {
    parts.push(s('line', {
      class: 'dg-string',
      x1: stringX(string), x2: stringX(string), y1: TOP, y2: bottom,
      style: { strokeWidth: 1.5 - string * 0.14 },
    }));
  }

  frets.forEach((fret, string) => {
    if (fret === -1) {
      parts.push(s('text', { class: 'dg-mark', x: stringX(string), y: TOP - 8 }, '×'));
    } else if (fret === 0) {
      parts.push(s('circle', { class: 'dg-open', cx: stringX(string), cy: TOP - 12, r: 4 }));
    }
  });

  if (baseFret > 1) {
    parts.push(s('text', { class: 'dg-base', x: right + 8, y: TOP + FRET_GAP * 0.68 }, `${baseFret}ª`));
  }

  let order = 0;
  if (barre) {
    parts.push(s('rect', {
      class: 'dg-barre',
      x: stringX(barre.from) - 7,
      y: fretY(barre.fret) - 7,
      width: stringX(barre.to) - stringX(barre.from) + 14,
      height: 14,
      rx: 7,
      style: { '--i': order },
    }));
    order += 1;
  }
  frets.forEach((fret, string) => {
    if (fret <= 0 || (barre && fret === barre.fret)) return;
    parts.push(s('circle', {
      class: 'dg-dot',
      cx: stringX(string),
      cy: fretY(fret),
      r: 7,
      style: { '--i': order },
    }));
    order += 1;
  });

  const spoken = frets.map((fret) => (fret === -1 ? 'x' : fret)).join(' ');
  return s(
    'svg',
    { class: 'diagram__svg', viewBox: '0 0 136 158', role: 'img', 'aria-label': `Diagrama de ${name}: ${spoken}` },
    parts,
  );
}

export function ChordDiagram(name, { size = 'md' } = {}) {
  const shape = chordShape(name);
  return h(
    'figure',
    { class: `diagram diagram--${size}` },
    h('figcaption', { class: 'diagram__name' }, name),
    shape
      ? diagramSvg(name, shape)
      : h('div', { class: 'diagram__none' }, 'Sem diagrama confiável para este acorde'),
    shape?.note && h('p', { class: 'diagram__note' }, shape.note),
  );
}
