// Ready-made practice session. Progress is saved so opening a song does not lose the session.
import { api } from '../api.js';
import { h, replaceContent } from '../dom.js';
import { songPath } from '../router.js';
import { study, studyTimer } from '../study.js';
import { formatClock, formatStudyTime } from '../studyTimer.js';
import { openChangeDrill } from './ChangeDrill.js';
import { ChordCheckButton } from './ChordCheck.js';
import { ChordDiagram } from './ChordDiagram.js';
import { icon } from './icons.js';
import { ErrorState, Skeleton, toast } from './states.js';

const RUN_KEY = 'cifrafy.practiceRun';
const DURATIONS = [10, 20, 30, 45];
const KIND_LABELS = { warmup: 'Aquecimento', mastered: 'Música que você domina', in_progress: 'Música em andamento', new_chord: 'Acorde novo' };

function loadRun() {
  try {
    const run = JSON.parse(localStorage.getItem(RUN_KEY) || 'null');
    return run?.plan?.blocks?.length ? run : null;
  } catch {
    return null;
  }
}

function saveRun(run) {
  try {
    if (run) localStorage.setItem(RUN_KEY, JSON.stringify(run));
    else localStorage.removeItem(RUN_KEY);
  } catch {
    // Without storage the session still works until the page changes.
  }
}

const remainingMs = (run) =>
  Math.max(0, run.remainingMs - (run.runningSince ? Date.now() - run.runningSince : 0));

export function PracticeSession({ signal, getPlaylistId }) {
  let minutes = 20;
  let plan = null;
  let run = loadRun();
  let tick = null;
  const root = h('div', { class: 'session' });

  const stopTick = () => {
    clearInterval(tick);
    tick = null;
  };

  function ensureStudyTimer() {
    if (studyTimer.status === 'idle') {
      studyTimer.start({ kind: 'session' });
      return;
    }
    if (studyTimer.status === 'paused') studyTimer.resume();
    studyTimer.setKind('session');
  }

  async function generate() {
    replaceContent(root, Picker(), Skeleton({ count: 3, variant: 'row' }));
    try {
      plan = await api.practiceSession({ minutes, playlistId: getPlaylistId() }, signal);
      renderPlan();
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(root, Picker(), ErrorState({ error, onRetry: generate }));
    }
  }

  function Picker() {
    return h('div', { class: 'session__picker' },
      h('div', { class: 'segmented', role: 'group', 'aria-label': 'Tempo disponível' },
        DURATIONS.map((value) => h('button', {
          class: 'segment', type: 'button', 'aria-pressed': value === minutes,
          onclick: () => {
            minutes = value;
            plan = null;
            renderIdle();
          },
        }, `${value} min`))),
      h('button', { class: 'btn btn--primary', type: 'button', onclick: generate }, plan ? 'Montar de novo' : 'Montar sessão'));
  }

  function renderIdle() {
    replaceContent(root, Picker());
  }

  function BlockRow(block, index, currentIndex = -1) {
    const state = index < currentIndex ? ' is-done' : index === currentIndex ? ' is-current' : '';
    return h('li', { class: `session-block${state}`, style: { '--i': index } },
      h('span', { class: 'session-block__time' }, `${block.minutes} min`),
      h('div', { class: 'session-block__body' },
        h('p', { class: 'session-block__kind' }, KIND_LABELS[block.kind] || block.kind),
        h('p', { class: 'session-block__title' }, block.title)),
      index < currentIndex && icon('check', 18));
  }

  function renderPlan() {
    if (!plan.blocks?.length) {
      replaceContent(root, Picker(), h('p', { class: 'notice' }, 'Ainda não há músicas suficientes. Analise algumas faixas de uma playlist.'));
      return;
    }
    replaceContent(root, Picker(),
      h('ol', { class: 'session__blocks' }, plan.blocks.map((block, index) => BlockRow(block, index))),
      h('button', { class: 'btn btn--primary session__start', type: 'button', onclick: begin }, icon('play', 17), `Começar ${plan.minutes} minutos`));
  }

  function begin() {
    run = { plan, index: 0, remainingMs: plan.blocks[0].minutes * 60000, runningSince: Date.now() };
    saveRun(run);
    ensureStudyTimer();
    renderRun();
  }

  function pauseBlock() {
    run.remainingMs = remainingMs(run);
    run.runningSince = null;
    saveRun(run);
    renderRun();
  }

  function resumeBlock() {
    run.runningSince = Date.now();
    saveRun(run);
    ensureStudyTimer();
    renderRun();
  }

  function nextBlock() {
    const next = run.index + 1;
    if (next >= run.plan.blocks.length) {
      finish();
      return;
    }
    run = { ...run, index: next, remainingMs: run.plan.blocks[next].minutes * 60000, runningSince: Date.now() };
    saveRun(run);
    ensureStudyTimer();
    renderRun();
  }

  async function finish() {
    stopTick();
    run = null;
    saveRun(null);
    plan = null;
    if (studyTimer.status !== 'idle' && studyTimer.kind === 'session') {
      try {
        const result = await study.finishTimer();
        if (result.saved) toast(`Sessão concluída: ${formatStudyTime(result.seconds)} no seu dia.`, 'ok');
      } catch (error) {
        toast(`Sessão concluída, mas o tempo não foi salvo. Conclua pelo cronômetro. ${error.message}`, 'error');
      }
    } else {
      toast('Sessão concluída.', 'ok');
    }
    renderIdle();
  }

  function leave() {
    if (!window.confirm('Encerrar a sessão agora? O tempo estudado será salvo.')) return;
    finish();
  }

  function BlockAction(block) {
    if (block.change) {
      return h('button', {
        class: 'btn btn--primary', type: 'button',
        onclick: () => openChangeDrill({ target_per_minute: 30, ...block.change }, { signal }),
      }, icon('play', 16), `Treinar ${block.change.from} e ${block.change.to}`);
    }
    if (block.song) {
      return h('a', { class: 'btn btn--primary', href: songPath(block.song.artist, block.song.track_name) },
        icon('today', 16), `Tocar ${block.song.track_name}`);
    }
    if (block.chord) {
      return h('div', { class: 'session-current__chord' },
        ChordDiagram(block.chord.chord, { size: 'sm' }),
        h('div', {}, h('p', { class: 'session-current__name' }, block.chord.chord,
          block.chord.is_barre && h('span', { class: 'tag' }, 'pestana')),
        ChordCheckButton(block.chord.chord)));
    }
    return null;
  }

  function renderRun() {
    stopTick();
    const block = run.plan.blocks[run.index];
    const clock = h('p', { class: 'session-current__clock', role: 'timer' });
    const isLast = run.index === run.plan.blocks.length - 1;
    const playing = Boolean(run.runningSince);

    replaceContent(root,
      h('div', { class: 'session-current' },
        h('p', { class: 'eyebrow' }, `Bloco ${run.index + 1} de ${run.plan.blocks.length}: ${KIND_LABELS[block.kind] || block.kind}`),
        h('h3', { class: 'session-current__title' }, block.title),
        clock,
        block.instruction && h('p', { class: 'session-current__text' }, block.instruction),
        BlockAction(block),
        h('div', { class: 'session-current__controls' },
          h('button', { class: 'btn btn--ghost', type: 'button', onclick: playing ? pauseBlock : resumeBlock },
            icon(playing ? 'pause' : 'play', 16), playing ? 'Pausar' : 'Continuar'),
          h('button', { class: 'btn btn--primary', type: 'button', onclick: nextBlock }, isLast ? 'Concluir sessão' : 'Próximo bloco'),
          h('button', { class: 'btn btn--quiet', type: 'button', onclick: leave }, 'Encerrar'))),
      h('ol', { class: 'session__blocks' }, run.plan.blocks.map((item, index) => BlockRow(item, index, run.index))));

    const update = () => {
      const left = remainingMs(run);
      clock.textContent = formatClock(Math.ceil(left / 1000));
      if (left <= 0 && run.runningSince) {
        run.remainingMs = 0;
        run.runningSince = null;
        saveRun(run);
        stopTick();
        toast(isLast ? 'Último bloco concluído.' : 'Bloco concluído. Siga para o próximo.', 'ok');
        renderRun();
      }
    };
    update();
    if (run?.runningSince) tick = setInterval(update, 500);
  }

  signal.addEventListener('abort', stopTick);
  if (run) renderRun();
  else renderIdle();
  return root;
}
