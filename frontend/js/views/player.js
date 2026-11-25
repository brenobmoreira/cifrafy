import { api } from '../api.js';
import { formatDuration, h, replaceContent, showDialog } from '../dom.js';
import { normalizeSemitones, parseChord, splitToken, transposeKey } from '../chords.js';
import { store } from '../store.js';
import { studyTimer } from '../study.js';
import { ChordCheckButton } from '../components/ChordCheck.js';
import { SyncIndicator } from '../components/SyncIndicator.js';
import { ChordDiagram } from '../components/ChordDiagram.js';
import { StudyTimerControl } from '../components/StudyTimer.js';
import { chordChipClass, LevelBadge, REPERTOIRE_LABELS } from '../components/SongCard.js';
import { icon } from '../components/icons.js';
import { ErrorState, Skeleton, toast } from '../components/states.js';

const CHAR_RATIO = 0.602;
const MIN_FONT = 11;

// Groups a chord line with its lyric, so both are read and highlighted as one step.
export function groupSteps(lines) {
  const steps = [];
  let current = null;
  for (const line of lines) {
    if (line.type === 'blank') {
      current = null;
      steps.push({ blank: true, lines: [line] });
    } else if (line.type === 'chords' || line.type === 'section') {
      current = { lines: [line] };
      steps.push(current);
    } else if (line.type === 'tab') {
      if (current?.tab) current.lines.push(line);
      else {
        current = { tab: true, lines: [line] };
        steps.push(current);
      }
    } else if (current && !current.tab && current.lines.at(-1).type !== 'lyric') {
      current.lines.push(line);
    } else {
      current = { lines: [line] };
      steps.push(current);
    }
  }
  return steps;
}

function renderChordText(text, onChord) {
  const parts = [];
  for (const piece of text.match(/\[[^\]]*\]|\s+|[^\s[]+|\[/g) || []) {
    if (/^\s/.test(piece) || piece.startsWith('[')) {
      parts.push(piece.startsWith('[') ? h('span', { class: 'sheet-label' }, piece) : piece);
      continue;
    }
    const { lead, core, trail } = splitToken(piece);
    if (core && parseChord(core)) {
      parts.push(lead, h('button', {
        type: 'button',
        class: 'sheet-chord',
        'aria-label': `Ver acorde ${core}`,
        onclick: (event) => {
          event.stopPropagation();
          onChord(core);
        },
      }, core), trail);
    } else {
      parts.push(piece);
    }
  }
  return parts;
}

function SheetLine(line, onChord) {
  const hasChords = line.type === 'chords' || line.type === 'section';
  return h(
    'div',
    { class: `sheet-line sheet-line--${line.type}` },
    line.type === 'blank' ? ' ' : hasChords ? renderChordText(line.text, onChord) : line.text,
  );
}

function fallbackLines(text) {
  return String(text || '').split('\n').map((line) => ({ type: line.trim() ? 'lyric' : 'blank', text: line }));
}

export function PlayerView({ params, signal }) {
  const { artist, track } = params;
  const state = {
    semitones: 0,
    capo: 0,
    simplified: store.prefs.simplified,
    data: null,
    firstLoad: true,
    playing: false,
    speed: store.prefs.scrollSpeed,
    size: store.prefs.sheetSize,
    wake: true,
    selectedChord: null,
    maxChars: 0,
    mode: 'original',
    // Key the sheet is written in, before any transposition. May differ from the site's stated key.
    key: null,
    arrangement: null,
  };
  let loadController = null;
  let scrollFrame = 0;
  let lastFrameAt = 0;
  let scrollCarry = 0;
  let wakeLock = null;
  let holdScrollUntil = 0;
  let saveTimer = null;
  let cooldownTimer = null;

  const heroTitle = h('h1', { class: 'player-title' }, track);
  const heroMeta = h('p', { class: 'player-meta' }, artist);
  const heroLevel = h('div', { class: 'player-level' });
  const externalSlot = h('div');
  const repertoireGroup = h('select', {
    class: 'select select--compact player-status',
    'aria-label': 'Status no repertório',
    hidden: true,
    onchange: (event) => setRepertoire(event.target.value),
  },
  h('option', { value: '' }, 'Fora do repertório'),
  Object.entries(REPERTOIRE_LABELS).map(([value, label]) => h('option', { value }, label)));
  // One choice for what is on the stand: the site's two versions, or the arrangement adapted to the user.
  const currentMode = () => (state.mode === 'mine' ? 'mine' : state.simplified ? 'simple' : 'original');
  const modeButtons = [['original', 'Original'], ['simple', 'Simplificada'], ['mine', 'Minha versão']].map(([value, label]) =>
    h('button', {
      type: 'button', class: 'segment', dataset: { value }, 'aria-pressed': value === currentMode(),
      onclick: () => setMode(value),
    }, label));
  const modeGroup = h('div', { class: 'segmented segmented--compact player-mode', role: 'group', 'aria-label': 'Versão para tocar' }, modeButtons);
  const arrangementPanel = h('div', {});
  const chordStrip = h('ul', { class: 'chord-strip', 'aria-label': 'Acordes da música' });
  const callouts = h('div', { class: 'player-callouts' });
  const sheet = h('div', { class: 'sheet' }, Skeleton({ count: 12, variant: 'line' }));
  const railDiagram = h('div', { class: 'rail-diagram' });
  const dialogBody = h('div', { class: 'chord-dialog__body' });
  const dialog = h('dialog', {
    class: 'chord-dialog',
    'aria-label': 'Diagrama do acorde',
    onclick: (event) => {
      if (event.target === dialog) dialog.close();
    },
  },
  h('button', { class: 'icon-btn chord-dialog__close', type: 'button', 'aria-label': 'Fechar', onclick: () => dialog.close() }, icon('close')),
  dialogBody);

  const toneLabel = h('output', { class: 'stepper__value' }, '0');
  const capoLabel = h('output', { class: 'stepper__value' }, '0');
  const soundLabel = h('p', { class: 'sound-label' });
  const playButton = h('button', { class: 'play-btn', type: 'button', 'aria-label': 'Rolar a cifra', onclick: togglePlay }, icon('play', 26));
  const moreButton = h('button', { class: 'icon-btn dock-more', type: 'button', 'aria-label': 'Mais ajustes', 'aria-expanded': false, onclick: toggleMore }, icon('sliders'));
  const wakeInput = h('input', {
    type: 'checkbox', class: 'switch__input', checked: state.wake,
    onchange: (event) => {
      state.wake = event.target.checked;
      if (state.wake) requestWake();
      else releaseWake();
    },
  });
  const sizeLabel = h('output', { class: 'stepper__value' }, state.size);
  const speedInput = h('input', {
    type: 'range', class: 'range', min: 1, max: 10, step: 1, value: state.speed,
    'aria-label': 'Velocidade da rolagem',
    oninput: (event) => {
      state.speed = Number(event.target.value);
      store.setPrefs({ scrollSpeed: state.speed });
    },
  });

  const deleteButton = h('button', { class: 'btn btn--quiet btn--small btn--danger', type: 'button', hidden: true, onclick: deleteSong },
    icon('trash', 16), 'Excluir música');

  const Stepper = (label, value, onMinus, onPlus) => h('div', { class: 'stepper', dataset: { pitch: label === 'Letra' ? '' : 'true' } },
    h('span', { class: 'stepper__label' }, label),
    h('div', { class: 'stepper__controls' },
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Diminuir ${label}`, onclick: onMinus }, icon('minus')),
      value,
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Aumentar ${label}`, onclick: onPlus }, icon('plus'))));

  const morePanel = h('div', { class: 'controls-more' },
    Stepper('Capotraste', capoLabel, () => changeCapo(-1), () => changeCapo(1)),
    h('div', { class: 'control' }, h('span', { class: 'stepper__label' }, 'Velocidade'), speedInput),
    Stepper('Letra', sizeLabel, () => changeSize(-1), () => changeSize(1)),
    h('label', { class: 'switch' }, wakeInput, h('span', { class: 'switch__track', 'aria-hidden': 'true' }), h('span', {}, 'Manter tela acesa')),
    deleteButton,
  );

  const controls = h('div', { class: 'player-controls' },
    morePanel,
    h('div', { class: 'dock' },
      Stepper('Tom', toneLabel, () => changeTone(-1), () => changeTone(1)),
      playButton,
      moreButton),
    soundLabel);

  function toggleMore() {
    const open = morePanel.classList.toggle('is-open');
    moreButton.setAttribute('aria-expanded', String(open));
  }

  const offsetText = () => (state.semitones > 0 ? `+${state.semitones}` : String(state.semitones));

  function updateLabels() {
    const shapes = state.key ? transposeKey(state.key, state.semitones) : null;
    toneLabel.textContent = shapes || offsetText();
    capoLabel.textContent = state.capo ? `${state.capo}ª` : '0';
    sizeLabel.textContent = String(state.size);
    if (state.mode === 'mine') {
      const fret = state.arrangement?.capo_fret;
      soundLabel.textContent = `${fret ? `Capotraste na ${fret}ª casa. ` : ''}Tom e capotraste ficam travados na Minha versão. Volte para Original ou Simplificada para alterar.`;
      return;
    }
    const key = state.key;
    const sounding = key ? transposeKey(key, state.semitones + state.capo) : null;
    const parts = [];
    if (key) parts.push(`Tom original ${key}`);
    if (state.capo) parts.push(`capotraste na ${state.capo}ª casa`);
    if (sounding && sounding !== key) parts.push(`soa em ${sounding}`);
    else if (sounding && state.capo) parts.push('soa no tom original');
    soundLabel.textContent = parts.join(', ');
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    const data = state.data;
    const status = data?.repertoire?.status;
    if (!data?.song_id || !status) return;
    saveTimer = setTimeout(async () => {
      try {
        await api.saveRepertoire(data.song_id, {
          status,
          preferred_semitones: state.semitones,
          preferred_capo: state.capo,
        });
        data.repertoire = { ...data.repertoire, preferred_semitones: state.semitones, preferred_capo: state.capo };
      } catch (error) {
        toast(error.message, 'error');
      }
    }, 500);
  }

  function setMode(choice) {
    if (choice === currentMode()) return;
    if (choice === 'mine') {
      state.mode = 'mine';
    } else {
      state.mode = 'original';
      state.simplified = choice === 'simple';
      store.setPrefs({ simplified: state.simplified });
    }
    modeButtons.forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.value === choice)));
    const locked = state.mode === 'mine';
    controls.querySelectorAll('.stepper[data-pitch="true"]').forEach((stepper) => {
      stepper.classList.toggle('is-locked', locked);
      stepper.querySelectorAll('button').forEach((button) => {
        button.disabled = locked;
      });
    });
    if (!locked) {
      state.arrangement = null;
      replaceContent(arrangementPanel);
    }
    updateLabels();
    load();
  }

  function changeTone(delta) {
    if (state.mode === 'mine') return;
    state.semitones = normalizeSemitones(state.semitones + delta);
    updateLabels();
    scheduleSave();
    load();
  }

  // Moving the capo keeps the sound: the shapes shift the opposite way.
  function changeCapo(delta) {
    if (state.mode === 'mine') return;
    const next = Math.max(0, Math.min(11, state.capo + delta));
    if (next === state.capo) return;
    state.semitones = normalizeSemitones(state.semitones - (next - state.capo));
    state.capo = next;
    updateLabels();
    scheduleSave();
    load();
  }

  function applyCapo(fret) {
    state.semitones = normalizeSemitones(state.semitones - fret);
    state.capo = Math.min(11, state.capo + fret);
    updateLabels();
    scheduleSave();
    load();
  }

  function changeSize(delta) {
    state.size = Math.max(11, Math.min(24, state.size + delta));
    store.setPrefs({ sheetSize: state.size });
    updateLabels();
    fitSheet();
  }

  async function setRepertoire(status) {
    const data = state.data;
    if (!data?.song_id) return;
    if ((data.repertoire?.status || '') === status) return;
    try {
      if (!status) {
        const removed = data.repertoire.status;
        const previous = data.repertoire;
        await api.removeRepertoire(data.song_id);
        data.repertoire = null;
        toast(`Removida de ${REPERTOIRE_LABELS[removed]}.`, 'info', {
          label: 'Desfazer',
          onClick: async () => {
            try {
              await api.saveRepertoire(data.song_id, previous);
              data.repertoire = previous;
              syncRepertoire();
            } catch (error) {
              toast(error.message, 'error');
            }
          },
        });
      } else {
        const body = { status, preferred_semitones: state.semitones, preferred_capo: state.capo };
        await api.saveRepertoire(data.song_id, body);
        data.repertoire = body;
      }
    } catch (error) {
      toast(error.message, 'error');
    }
    syncRepertoire();
  }

  async function deleteSong() {
    const data = state.data;
    if (!data?.song_id) return;
    if (!window.confirm(`Excluir "${data.track_name || track}" do app? A cifra salva e o status no repertório somem.`)) return;
    try {
      await api.deleteSong(data.song_id);
      toast('Música excluída.', 'ok');
      window.location.hash = '#/hoje';
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  function syncRepertoire() {
    const data = state.data;
    deleteButton.hidden = !data?.song_id;
    repertoireGroup.hidden = !data?.song_id;
    repertoireGroup.value = data?.repertoire?.status || '';
  }

  function showChord(name) {
    state.selectedChord = name;
    replaceContent(railDiagram, ChordDiagram(name, { size: 'lg' }), h('div', { class: 'rail-check' }, ChordCheckButton(name)));
    if (!window.matchMedia('(min-width: 1024px)').matches) {
      replaceContent(dialogBody, ChordDiagram(name, { size: 'lg' }), ChordCheckButton(name));
      if (!dialog.open) showDialog(dialog, { focus: () => dialogBody.querySelector('button') });
    }
  }

  function renderStrip(data) {
    const song = { ...data.evaluation, unique_chords: data.unique_chords };
    replaceContent(chordStrip, (data.unique_chords || []).map((chord) =>
      h('li', {}, h('button', {
        type: 'button',
        class: chordChipClass(chord, song),
        onclick: () => showChord(chord),
      }, chord))));
    const first = state.selectedChord && data.unique_chords?.includes(state.selectedChord)
      ? state.selectedChord
      : data.unique_chords?.[0];
    state.selectedChord = first || null;
    replaceContent(railDiagram, first ? [ChordDiagram(first, { size: 'lg' }), h('div', { class: 'rail-check' }, ChordCheckButton(first))] : null);
  }

  function CooldownNotice(seconds) {
    clearInterval(cooldownTimer);
    let remaining = seconds || 0;
    const label = h('strong', {}, formatDuration(remaining));
    if (remaining > 0) {
      cooldownTimer = setInterval(() => {
        remaining -= 1;
        label.textContent = formatDuration(remaining);
        if (remaining <= 0) {
          clearInterval(cooldownTimer);
          load();
        }
      }, 1000);
    }
    return h('div', { class: 'banner banner--wait', role: 'status' },
      h('b', {}, 'O Cifra Club pediu uma pausa. '),
      remaining > 0 ? h('span', {}, 'Tentando de novo em ', label) : 'Tente novamente em instantes.');
  }

  function applyPlan(semitones, capo) {
    state.semitones = normalizeSemitones(semitones);
    state.capo = capo;
    updateLabels();
    scheduleSave();
    load();
  }

  // Offer the key set in Ajustes. It replaces the capo tip: those shapes are usually open already.
  function PreferredKeyCallout() {
    const plan = state.data?.preferred_key_plan;
    if (!plan || transposeKey(state.key, state.semitones) === plan.key) return null;
    return h('div', { class: 'callout callout--key' },
      h('p', {}, `Tom original: ${state.key}`),
      h('div', { class: 'callout__actions' },
        h('button', { class: 'btn btn--small btn--primary', type: 'button', onclick: () => applyPlan(plan.semitones, 0) },
          `Mudar para ${plan.key}`),
        plan.capo && h('button', {
          class: 'btn btn--small btn--ghost', type: 'button', title: 'Soa igual à gravação',
          onclick: () => applyPlan(-plan.capo, plan.capo),
        }, `${plan.key} com capotraste na ${plan.capo}ª`)));
  }

  function renderCallouts(data) {
    const items = [];
    const keyCallout = state.key ? PreferredKeyCallout() : null;
    if (keyCallout) items.push(keyCallout);
    const capo = data.evaluation?.capo_suggestion;
    if (capo && !state.capo && !keyCallout) {
      items.push(h('div', { class: 'callout callout--capo' },
        h('p', {}, capo.message),
        h('button', { class: 'btn btn--small btn--primary', type: 'button', onclick: () => applyCapo(capo.capo_fret) },
          `Usar ${capo.capo_fret}ª casa`)));
    }
    if (state.simplified && !data.is_simplified && data.found) {
      items.push(h('p', { class: 'callout' }, 'Esta música não tem versão simplificada. Mostrando a normal.'));
    }
    if (data.status === 'rate_limited') items.push(CooldownNotice(data.retry_after_seconds));
    replaceContent(callouts, items);
  }

  // One column only: the sheet is read top to bottom while it scrolls. Long lines shrink the font.
  function fitSheet() {
    if (!state.maxChars) return;
    const width = sheet.clientWidth - 2;
    const fitted = width / (state.maxChars * CHAR_RATIO);
    const size = Math.max(MIN_FONT, Math.min(state.size, Math.floor(fitted * 10) / 10));
    sheet.style.setProperty('--sheet-size', `${size}px`);
  }

  function renderSheet(data) {
    const lines = data.sheet_lines?.length ? data.sheet_lines : fallbackLines(data.raw_chord_sheet);
    const groups = groupSteps(lines).map((step) => h('div', {
      class: `sheet-step${step.blank ? ' sheet-step--blank' : ''}${step.tab ? ' sheet-step--tab' : ''}`,
    }, step.lines.map((line) => SheetLine(line, showChord))));
    state.maxChars = Math.max(1, ...lines.filter((line) => line.type !== 'tab').map((line) => line.text.length));
    replaceContent(sheet, groups);
    fitSheet();
  }

  function renderUnavailable(data) {
    const messages = {
      not_found: ['Cifra não encontrada', data.error || 'O Cifra Club não tem esta música com esse nome.'],
      rate_limited: ['O Cifra Club pediu uma pausa', 'A cifra será baixada quando o acesso for liberado.'],
      error: ['Não foi possível baixar a cifra', data.error || 'Tente novamente em instantes.'],
    };
    const [title, text] = messages[data.status] || ['Cifra indisponível', data.error || 'Sem texto salvo para esta música.'];
    state.maxChars = 0;
    stopPlaying();
    replaceContent(sheet, h('div', { class: 'state' },
      h('h3', { class: 'state__title' }, title),
      h('p', { class: 'state__text' }, text),
      h('button', { class: 'btn', type: 'button', onclick: () => load() }, 'Tentar de novo')));
  }

  function ArrangementSummary(data) {
    const subs = data.substitutions || [];
    const hard = data.remaining_hard || [];
    return h('section', { class: 'arrangement', 'aria-label': 'Resumo da minha versão' },
      h('div', { class: 'arrangement__facts' },
        h('p', { class: 'arrangement__fact' }, h('strong', {}, `${Math.round(data.fidelity_pct ?? 0)}%`), h('span', {}, 'fiel à original')),
        h('p', { class: 'arrangement__fact' },
          h('strong', {}, data.capo_fret ? `${data.capo_fret}ª casa` : 'Sem'), h('span', {}, 'capotraste')),
        h('div', { class: 'arrangement__fact' },
          h('div', { class: 'arrangement__level' }, LevelBadge(data.level_before), h('span', { 'aria-hidden': 'true' }, '>'), LevelBadge(data.level_after)),
          h('span', {}, 'antes e depois'))),
      subs.length > 0 && h('ul', { class: 'arrangement__subs', 'aria-label': 'Acordes trocados' }, subs.map((item) =>
        h('li', { class: 'arrangement__sub' },
          h('span', { class: 'arrangement__pair' }, `${item.from} > ${item.to}`),
          h('span', { class: 'arrangement__reason' }, item.reason || `${item.occurrences} vezes`)))),
      !subs.length && h('p', { class: 'arrangement__note' }, 'Nenhum acorde precisou ser trocado.'),
      hard.length > 0 && h('p', { class: 'arrangement__note' }, `Ainda difíceis: ${hard.join(', ')}`),
      data.note && h('p', { class: 'arrangement__note' }, data.note));
  }

  function renderArrangement(data) {
    state.arrangement = data;
    updateLabels();
    if (!data.sheet_lines?.length) {
      replaceContent(arrangementPanel);
      renderUnavailable({ ...data, status: data.status || 'error' });
      return;
    }
    replaceContent(heroLevel, LevelBadge(data.level_after));
    replaceContent(arrangementPanel, ArrangementSummary(data));
    replaceContent(callouts);
    renderStrip({ unique_chords: data.chords || [], evaluation: {} });
    renderSheet(data);
  }

  async function loadArrangement() {
    loadController?.abort();
    loadController = new AbortController();
    const requestSignal = AbortSignal.any([signal, loadController.signal]);
    clearInterval(cooldownTimer);
    sheet.classList.add('is-loading');
    try {
      renderArrangement(await api.arrangement({ artist, trackName: track, simplified: state.simplified }, requestSignal));
    } catch (error) {
      if (error.name === 'AbortError') return;
      stopPlaying();
      replaceContent(arrangementPanel);
      replaceContent(sheet, ErrorState({ error, onRetry: load }));
    } finally {
      if (!requestSignal.aborted) sheet.classList.remove('is-loading');
    }
  }

  function renderSong(data) {
    state.data = data;
    state.key = data.written_key || data.key || null;
    if (data.song_id) studyTimer.associateSong(data.song_id);
    heroTitle.textContent = data.track_name || track;
    heroMeta.textContent = data.artist || artist;
    const evaluation = data.evaluation;
    replaceContent(heroLevel, evaluation ? LevelBadge(evaluation.level) : null);
    replaceContent(externalSlot, data.url
      ? h('a', { class: 'text-link text-link--icon', href: data.url, target: '_blank', rel: 'noopener noreferrer' },
          'Ver no Cifra Club', icon('external', 15))
      : null);
    renderStrip(data);
    renderCallouts(data);
    syncRepertoire();
    updateLabels();
    if (data.sheet_available && (data.raw_chord_sheet || data.sheet_lines?.length)) renderSheet(data);
    else renderUnavailable(data);
  }

  async function load() {
    if (state.mode === 'mine') {
      await loadArrangement();
      return;
    }
    loadController?.abort();
    loadController = new AbortController();
    const requestSignal = AbortSignal.any([signal, loadController.signal]);
    clearInterval(cooldownTimer);
    sheet.classList.add('is-loading');
    try {
      const data = await api.chords({
        artist, trackName: track, semitones: state.semitones, simplified: state.simplified,
      }, requestSignal);

      if (state.firstLoad) {
        state.firstLoad = false;
        const saved = data.repertoire;
        if (saved && (saved.preferred_semitones || saved.preferred_capo)) {
          state.semitones = normalizeSemitones(saved.preferred_semitones || 0);
          state.capo = saved.preferred_capo || 0;
          updateLabels();
          load();
          return;
        }
      }
      renderSong(data);
    } catch (error) {
      if (error.name === 'AbortError') return;
      stopPlaying();
      replaceContent(sheet, ErrorState({ error, onRetry: load }));
    } finally {
      if (!requestSignal.aborted) sheet.classList.remove('is-loading');
    }
  }

  // Auto-scroll: the whole sheet stays on screen and the page slides down at a steady pace.
  const pixelsPerSecond = () => 6 + (state.speed - 1) * 5;
  const atBottom = () => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;

  function scrollStep(now) {
    const elapsed = lastFrameAt ? Math.min(0.1, (now - lastFrameAt) / 1000) : 0;
    lastFrameAt = now;
    if (Date.now() >= holdScrollUntil) {
      scrollCarry += pixelsPerSecond() * elapsed;
      const whole = Math.floor(scrollCarry);
      if (whole >= 1) {
        window.scrollBy(0, whole);
        scrollCarry -= whole;
      }
      if (atBottom()) {
        stopPlaying();
        return;
      }
    }
    scrollFrame = requestAnimationFrame(scrollStep);
  }

  function startPlaying() {
    if (!state.maxChars) return;
    // Starting above the sheet would spend the first seconds on the header.
    const sheetTop = sheet.getBoundingClientRect().top;
    if (sheetTop > window.innerHeight * 0.35) window.scrollBy(0, sheetTop - 16);
    state.playing = true;
    replaceContent(playButton, icon('pause', 26));
    playButton.setAttribute('aria-label', 'Pausar rolagem');
    holdScrollUntil = 0;
    lastFrameAt = 0;
    scrollCarry = 0;
    cancelAnimationFrame(scrollFrame);
    scrollFrame = requestAnimationFrame(scrollStep);
    if (state.wake) requestWake();
  }

  function stopPlaying() {
    state.playing = false;
    cancelAnimationFrame(scrollFrame);
    scrollFrame = 0;
    replaceContent(playButton, icon('play', 26));
    playButton.setAttribute('aria-label', 'Rolar a cifra');
  }

  function togglePlay() {
    if (state.playing) stopPlaying();
    else startPlaying();
  }

  // Scrolling by hand pauses the slide for a moment, then it continues from the new spot.
  const holdScroll = () => {
    if (state.playing) holdScrollUntil = Date.now() + 2000;
  };

  async function requestWake() {
    if (!('wakeLock' in navigator) || wakeLock || document.visibilityState !== 'visible') return;
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => {
        wakeLock = null;
      });
      if (signal.aborted || !state.wake) releaseWake();
    } catch {
      // The browser may refuse, for example on low battery. The page keeps working.
    }
  }

  function releaseWake() {
    wakeLock?.release().catch(() => {});
    wakeLock = null;
  }

  const onVisibility = () => {
    if (document.visibilityState === 'visible' && state.wake) requestWake();
  };

  const resizeObserver = new ResizeObserver(() => requestAnimationFrame(fitSheet));
  resizeObserver.observe(sheet);
  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('wheel', holdScroll, { passive: true });
  window.addEventListener('touchmove', holdScroll, { passive: true });

  signal.addEventListener('abort', () => {
    loadController?.abort();
    stopPlaying();
    releaseWake();
    clearTimeout(saveTimer);
    clearInterval(cooldownTimer);
    resizeObserver.disconnect();
    document.removeEventListener('visibilitychange', onVisibility);
    window.removeEventListener('wheel', holdScroll);
    window.removeEventListener('touchmove', holdScroll);
    if (dialog.open) dialog.close();
  });

  updateLabels();
  requestWake();
  load();

  return h(
    'section',
    { class: 'view view--player' },
    h('header', { class: 'player-head' },
      h('button', {
        class: 'back-link', type: 'button',
        onclick: () => {
          if (window.history.length > 1) window.history.back();
          else window.location.hash = '#/hoje';
        },
      }, icon('back', 18), 'Voltar'),
      h('div', { class: 'player-head__main' },
        h('div', { class: 'player-head__titles' }, heroTitle, heroMeta),
        heroLevel),
      h('div', { class: 'player-head__tools' }, SyncIndicator({ signal }), StudyTimerControl({ signal }), repertoireGroup, externalSlot)),
    modeGroup,
    arrangementPanel,
    chordStrip,
    callouts,
    h('div', { class: 'player-body' },
      sheet,
      h('aside', { class: 'player-rail' }, railDiagram, controls)),
    dialog,
  );
}
