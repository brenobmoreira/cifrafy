import { h } from '../dom.js';
import { store } from '../store.js';
import { toast } from '../components/states.js';

const KEYS = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'];
const SPEEDS = [[3, 'Devagar'], [5, 'Média'], [7, 'Rápida']];

export function SettingsView() {
  const keySelect = h('select', {
    id: 'settings-key', class: 'select',
    onchange: (event) => {
      store.setPrefs({ preferredKey: event.target.value });
      toast(`Tom do violão: ${event.target.value}.`, 'ok');
    },
  }, KEYS.map((key) => h('option', { value: key, selected: key === store.prefs.preferredKey }, key)));

  const speedSelect = h('select', {
    id: 'settings-speed', class: 'select',
    onchange: (event) => store.setPrefs({ scrollSpeed: Number(event.target.value) }),
  }, SPEEDS.map(([value, label]) => h('option', { value, selected: value === store.prefs.scrollSpeed }, label)));
  if (!SPEEDS.some(([value]) => value === store.prefs.scrollSpeed)) {
    speedSelect.append(h('option', { value: store.prefs.scrollSpeed, selected: true }, `Personalizada (${store.prefs.scrollSpeed})`));
  }

  return h('section', { class: 'view view--settings' },
    h('header', { class: 'page-head' },
      h('div', {}, h('p', { class: 'eyebrow' }, 'Do seu jeito'), h('h1', { class: 'page-title' }, 'Ajustes'))),
    h('div', { class: 'panel settings' },
      h('div', { class: 'field' },
        h('label', { for: 'settings-key' }, 'Tom do violão'),
        keySelect,
        h('p', { class: 'field__hint' }, 'O tom em que você prefere tocar. Na música aparece o botão para mudar para ele. Músicas em tom menor usam o relativo (G vira Em).')),
      h('div', { class: 'field' },
        h('label', { for: 'settings-speed' }, 'Velocidade da rolagem'),
        speedSelect,
        h('p', { class: 'field__hint' }, 'Quanto a cifra desce quando você aperta o play. Dá para ajustar também na tela da música.'))));
}
