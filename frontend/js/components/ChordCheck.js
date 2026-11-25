// Experimental microphone check. Audio stays in the browser: nothing is recorded or sent.
import { h, replaceContent, showDialog } from '../dom.js';
import { detectChord } from '../audio/chordDetector.js';
import { ChordDiagram } from './ChordDiagram.js';
import { icon } from './icons.js';

const FFT_SIZE = 16384;
const READ_INTERVAL_MS = 220;
const MATCHES_TO_CONFIRM = 3;

function openChordCheck(chord) {
  let stream = null;
  let context = null;
  let timer = null;
  let streak = 0;

  const status = h('p', { class: 'mic__status', role: 'status' }, 'Clique em ouvir e toque o acorde.');
  const level = h('div', { class: 'mic__level', 'aria-hidden': 'true' }, h('span'));
  const actions = h('div', { class: 'mic__actions' });

  function stop() {
    clearInterval(timer);
    timer = null;
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    context?.close().catch(() => {});
    context = null;
    level.firstChild.style.width = '0%';
  }

  function renderActions(listening) {
    replaceContent(actions, listening
      ? h('button', { class: 'btn btn--ghost', type: 'button', onclick: () => { stop(); status.textContent = 'Parado.'; renderActions(false); } }, 'Parar')
      : h('button', { class: 'btn btn--primary', type: 'button', onclick: listen }, icon('mic', 17), 'Ouvir'));
    if (dialog?.open) actions.querySelector('button')?.focus();
  }

  function setStatus(text, tone = '') {
    status.textContent = text;
    status.className = `mic__status${tone ? ` mic__status--${tone}` : ''}`;
  }

  async function listen() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus('Este navegador não permite usar o microfone nesta página.', 'bad');
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
    } catch {
      setStatus('Sem acesso ao microfone. Libere a permissão no navegador e tente de novo.', 'bad');
      return;
    }
    if (!dialog.open) {
      stop();
      return;
    }
    const AudioEngine = window.AudioContext || window.webkitAudioContext;
    context = new AudioEngine();
    await context.resume();
    const analyser = context.createAnalyser();
    analyser.fftSize = FFT_SIZE;
    analyser.smoothingTimeConstant = 0.55;
    context.createMediaStreamSource(stream).connect(analyser);
    const spectrum = new Float32Array(analyser.frequencyBinCount);

    streak = 0;
    renderActions(true);
    setStatus('Ouvindo. Toque o acorde e deixe soar.');
    timer = setInterval(() => {
      analyser.getFloatFrequencyData(spectrum);
      const result = detectChord(spectrum, { sampleRate: context.sampleRate, fftSize: FFT_SIZE, expected: chord });
      level.firstChild.style.width = `${Math.round(result.confidence * 100)}%`;
      if (result.silent) {
        streak = 0;
        setStatus('Ouvindo. Toque o acorde e deixe soar.');
      } else if (result.match) {
        streak += 1;
        if (streak >= MATCHES_TO_CONFIRM) {
          stop();
          renderActions(false);
          setStatus(`Soou como ${chord}.`, 'ok');
        }
      } else {
        streak = 0;
        setStatus(`Ainda não. Parece mais com ${result.detected}.`, 'bad');
      }
    }, READ_INTERVAL_MS);
  }

  const dialog = h('dialog', {
    class: 'mic',
    'aria-label': `Verificar acorde ${chord}`,
    onclick: (event) => {
      if (event.target === dialog) dialog.close();
    },
  },
  h('button', { class: 'icon-btn mic__close', type: 'button', 'aria-label': 'Fechar', onclick: () => dialog.close() }, icon('close', 18)),
  h('p', { class: 'eyebrow' }, 'Verificar acorde ', h('span', { class: 'tag' }, 'Experimental')),
  ChordDiagram(chord, { size: 'md' }),
  level,
  status,
  actions,
  h('p', { class: 'mic__note' }, 'Pode errar, principalmente com ruído ou violão desafinado. O áudio fica no seu navegador: nada é gravado ou enviado.'));

  dialog.addEventListener('close', () => {
    stop();
    dialog.remove();
  });
  renderActions(false);
  document.body.append(dialog);
  showDialog(dialog, { focus: () => actions.querySelector('button') });
}

export function ChordCheckButton(chord, { label = 'Verificar acorde' } = {}) {
  return h('button', {
    class: 'btn btn--ghost btn--small chord-check',
    type: 'button',
    'aria-label': `${label} ${chord} pelo microfone, experimental`,
    onclick: () => openChordCheck(chord),
  }, icon('mic', 15), label);
}
