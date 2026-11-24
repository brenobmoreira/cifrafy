// One-minute chord change drill. Hands stay on the guitar, so the count is entered at the end.
import { api } from '../api.js';
import { h, replaceContent } from '../dom.js';
import { createMetronome } from '../metronome.js';
import { store } from '../store.js';
import { ChordDiagram } from './ChordDiagram.js';
import { icon } from './icons.js';
import { toast } from './states.js';

const DURATIONS = [30, 60, 90];
const COUNT_IN_SECONDS = 3;
const MAX_COUNT = 300;
const MIN_BPM = 30;
const MAX_BPM = 240;

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export function openChangeDrill(change, { signal, onSaved }) {
  let seconds = store.prefs.drillSeconds ?? 60;
  let bpm = store.prefs.drillBpm ?? 60;
  let useClick = store.prefs.drillClick ?? true;
  let interval = null;
  let wakeLock = null;

  const metronome = createMetronome();
  const body = h('div', { class: 'drill__body' });

  function stopClock() {
    clearInterval(interval);
    interval = null;
    metronome.stop();
    wakeLock?.release().catch(() => {});
    wakeLock = null;
  }

  async function keepScreenOn() {
    try {
      wakeLock = await navigator.wakeLock?.request('screen');
    } catch {
      wakeLock = null;
    }
  }

  function Clock(label) {
    const value = h('span', { class: 'drill-clock__value' }, label);
    const clock = h('div', { class: 'drill-clock', role: 'timer', style: { '--p': 0 } }, value);
    return { clock, value };
  }

  function renderSetup() {
    const durationButtons = DURATIONS.map((option) =>
      h('button', {
        class: 'segment', type: 'button', 'aria-pressed': option === seconds,
        onclick: () => {
          seconds = option;
          store.setPrefs({ drillSeconds: option });
          renderSetup();
        },
      }, `${option}s`));

    const bpmValue = h('output', { class: 'drill-bpm__value' }, `${bpm} bpm`);
    const changeBpm = (delta) => {
      bpm = clamp(bpm + delta, MIN_BPM, MAX_BPM);
      store.setPrefs({ drillBpm: bpm });
      bpmValue.textContent = `${bpm} bpm`;
    };

    replaceContent(body,
      h('p', { class: 'drill__hint' },
        'Troque entre os dois acordes o máximo que conseguir, com som limpo. Conte as trocas e anote no final.'),
      h('div', { class: 'drill-options' },
        h('div', { class: 'segmented', role: 'group', 'aria-label': 'Duração' }, durationButtons),
        h('label', { class: 'switch' },
          h('input', {
            type: 'checkbox', class: 'switch__input', checked: useClick,
            onchange: (event) => {
              useClick = event.target.checked;
              store.setPrefs({ drillClick: useClick });
            },
          }),
          h('span', { class: 'switch__track', 'aria-hidden': 'true' }),
          h('span', {}, 'Metrônomo')),
        h('div', { class: 'drill-bpm' },
          h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Diminuir andamento', onclick: () => changeBpm(-5) }, icon('minus', 18)),
          bpmValue,
          h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Aumentar andamento', onclick: () => changeBpm(5) }, icon('plus', 18)))),
      h('button', { class: 'btn btn--primary drill__main', type: 'button', onclick: begin }, icon('play', 18), 'Começar'));
  }

  async function begin() {
    await metronome.prepare();
    keepScreenOn();
    const { clock, value } = Clock(String(COUNT_IN_SECONDS));
    replaceContent(body, clock, h('p', { class: 'drill__hint' }, 'Posicione a mão no primeiro acorde'),
      h('button', { class: 'btn btn--ghost drill__main', type: 'button', onclick: cancel }, 'Cancelar'));

    const startedAt = performance.now();
    interval = setInterval(() => {
      const left = COUNT_IN_SECONDS - (performance.now() - startedAt) / 1000;
      if (left <= 0) {
        clearInterval(interval);
        run();
      } else {
        value.textContent = String(Math.ceil(left));
      }
    }, 80);
  }

  function run() {
    const { clock, value } = Clock(String(seconds));
    clock.classList.add('is-running');
    replaceContent(body, clock, h('p', { class: 'drill__hint' }, `${change.from} e ${change.to}, sem parar`),
      h('button', { class: 'btn btn--ghost drill__main', type: 'button', onclick: cancel }, 'Cancelar'));

    if (useClick) metronome.start(bpm);
    const endsAt = performance.now() + seconds * 1000;
    interval = setInterval(() => {
      const left = (endsAt - performance.now()) / 1000;
      if (left <= 0) {
        stopClock();
        metronome.finishSignal();
        renderCount();
        return;
      }
      value.textContent = String(Math.ceil(left));
      clock.style.setProperty('--p', String(1 - left / seconds));
    }, 100);
  }

  function cancel() {
    stopClock();
    renderSetup();
  }

  function renderCount() {
    let count = clamp(Math.round(((change.last_per_minute ?? 0) * seconds) / 60), 0, MAX_COUNT);
    const input = h('input', {
      id: 'drill-count', class: 'input drill-count__input', type: 'number', inputMode: 'numeric',
      min: 0, max: MAX_COUNT, value: count,
      oninput: (event) => {
        count = clamp(Number.parseInt(event.target.value, 10) || 0, 0, MAX_COUNT);
      },
    });
    const adjust = (delta) => {
      count = clamp(count + delta, 0, MAX_COUNT);
      input.value = count;
    };
    const feedback = h('p', { class: 'form-feedback', role: 'status' });
    const save = h('button', { class: 'btn btn--primary drill__main', type: 'button' }, icon('check', 18), 'Salvar resultado');

    save.addEventListener('click', async () => {
      save.disabled = true;
      feedback.textContent = '';
      try {
        const result = await api.logPracticeChange({
          from: change.from,
          to: change.to,
          count,
          duration_seconds: seconds,
          bpm: useClick ? bpm : undefined,
        });
        onSaved?.();
        await renderResult(result);
      } catch (error) {
        save.disabled = false;
        feedback.textContent = error.message;
        feedback.classList.add('form-feedback--error');
      }
    });

    replaceContent(body,
      h('label', { class: 'drill-count__label', for: 'drill-count' }, 'Quantas trocas você fez?'),
      h('div', { class: 'drill-count' },
        h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Menos uma troca', onclick: () => adjust(-1) }, icon('minus', 20)),
        input,
        h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Mais uma troca', onclick: () => adjust(1) }, icon('plus', 20))),
      feedback,
      save);
    input.select();
  }

  async function renderResult(result) {
    const perMinute = result.session.per_minute;
    const target = change.target_per_minute || 30;
    let recent = [];
    try {
      const history = await api.practiceHistory(change.from, change.to, 6, signal);
      recent = history.sessions.map((session) => session.per_minute);
    } catch (error) {
      if (error.name !== 'AbortError') toast('Resultado salvo, mas o histórico não carregou.', 'info');
    }

    replaceContent(body,
      h('div', { class: `drill-result${result.improved ? ' is-record' : ''}` },
        h('p', { class: 'eyebrow' }, result.improved ? 'Novo recorde' : 'Resultado salvo'),
        h('p', { class: 'drill-result__value' }, formatRate(perMinute), h('small', {}, ' trocas/min')),
        h('p', { class: 'drill__hint' },
          perMinute >= target
            ? `Você bateu a meta de ${target}. Essa troca já está firme.`
            : `Faltam ${formatRate(target - perMinute)} para a meta de ${target}. Recorde: ${formatRate(result.best_per_minute)}.`),
        recent.length > 1 && h('p', { class: 'drill-result__history' },
          `Últimos treinos: ${recent.map(formatRate).join(', ')}`)),
      h('div', { class: 'drill__actions' },
        h('button', { class: 'btn btn--primary', type: 'button', onclick: renderSetup }, 'Treinar de novo'),
        h('button', { class: 'btn btn--ghost', type: 'button', onclick: () => dialog.close() }, 'Fechar')));
  }

  const dialog = h('dialog', { class: 'drill', 'aria-label': `Treino de troca entre ${change.from} e ${change.to}` },
    h('button', {
      class: 'icon-btn drill__close', type: 'button', 'aria-label': 'Fechar treino',
      onclick: () => dialog.close(),
    }, icon('close', 18)),
    h('p', { class: 'eyebrow' }, 'Treino de troca'),
    h('div', { class: 'drill__chords' },
      ChordDiagram(change.from, { size: 'sm' }),
      h('span', { class: 'drill__swap', 'aria-hidden': 'true' }),
      ChordDiagram(change.to, { size: 'sm' })),
    body);

  const close = () => dialog.close();
  dialog.addEventListener('close', () => {
    stopClock();
    metronome.close();
    signal.removeEventListener('abort', close);
    dialog.remove();
  });
  signal.addEventListener('abort', close);

  renderSetup();
  document.body.append(dialog);
  dialog.showModal();
}

export function formatRate(value) {
  if (value === null || value === undefined) return '-';
  return Number.isInteger(value) ? String(value) : Number(value).toFixed(1).replace('.', ',');
}
