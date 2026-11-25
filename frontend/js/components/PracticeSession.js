// Ready-made practice session. Progress is saved so opening a song does not lose the session.
import { api } from '../api.js';
import { h, replaceContent } from '../dom.js';
import { createMetronome } from '../metronome.js';
import { store } from '../store.js';
import { songPath } from '../router.js';
import { study, studyTimer } from '../study.js';
import { formatClock, formatStudyTime } from '../studyTimer.js';
import { openChangeDrill } from './ChangeDrill.js';
import { ChordCheckButton } from './ChordCheck.js';
import { ChordDiagram } from './ChordDiagram.js';
import { COMMON_CHORDS } from '../chords.js';
import { icon } from './icons.js';
import { ErrorState, Skeleton, toast } from './states.js';

const RUN_KEY = 'cifrafy.practiceRun';
const DURATIONS = [10, 20, 30, 45];
const KIND_LABELS = { warmup: 'Aquecimento', change: 'Troca de acordes', mastered: 'Música que você domina', in_progress: 'Música em andamento', new_chord: 'Acorde novo' };

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
  let options = { songs: [], changes: [], chords: [] };
  let run = loadRun();
  let tick = null;
  let clicking = false;
  const metronome = createMetronome();
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
      const playlistId = getPlaylistId();
      const [built, songs, changes, trail] = await Promise.all([
        api.practiceSession({ minutes, playlistId }, signal),
        api.catalog({ limit: 80 }, signal).catch(() => []),
        api.practiceChanges({ playlistId, limit: 12 }, signal).catch(() => ({ changes: [] })),
        api.practiceTrail({ playlistId, limit: 6 }, signal).catch(() => ({ steps: [] })),
      ]);
      plan = built;
      options = buildOptions(songs, changes.changes, trail.steps);
      renderPlan();
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(root, Picker(), ErrorState({ error, onRetry: generate }));
    }
  }

  // What each block can be swapped for. The plan's own pick always stays in the list.
  function buildOptions(songs, changes, steps) {
    const known = new Set(store.profile.known_chords || []);
    const chords = steps.map((step) => ({ chord: step.chord, is_barre: step.is_barre }));
    Object.entries(COMMON_CHORDS).forEach(([group, names]) => names.forEach((chord) => {
      if (!known.has(chord) && !chords.some((item) => item.chord === chord)) {
        chords.push({ chord, is_barre: group === 'Com pestana' });
      }
    }));
    return {
      songs: songs.map((song) => ({ song_id: song.id, artist: song.artist, track_name: song.track_name, level: song.level })),
      changes: changes.map((change) => ({ from: change.from, to: change.to, target_per_minute: change.target_per_minute })),
      chords,
    };
  }

  const SONG_PREFIX = { mastered: 'Para soltar a mão: ', in_progress: 'Em andamento: ' };

  function BlockChoice(block) {
    const select = (label, current, items, onPick) => {
      const list = items.some((item) => item.value === current.value) ? items : [current, ...items];
      return h('select', {
        class: 'select select--compact session-block__select', 'aria-label': label,
        onchange: (event) => onPick(event.target.value),
      }, list.map((item) => h('option', { value: item.value, selected: item.value === current.value }, item.label)));
    };
    if (block.song) {
      const asItem = (song) => ({ value: String(song.song_id), label: `${song.track_name} · ${song.artist}`, song });
      return select('Trocar música', asItem(block.song), options.songs.map(asItem), (value) => {
        const song = options.songs.find((item) => String(item.song_id) === value);
        if (!song) return;
        block.song = song;
        block.title = `${SONG_PREFIX[block.kind] || ''}${song.track_name}`;
      });
    }
    if (block.change) {
      const asItem = (change) => ({ value: `${change.from}|${change.to}`, label: `${change.from} e ${change.to}`, change });
      return select('Trocar a troca de acordes', asItem(block.change), options.changes.map(asItem), (value) => {
        const change = options.changes.find((item) => `${item.from}|${item.to}` === value);
        if (!change) return;
        block.change = change;
        block.title = `Troca do dia: ${change.from} e ${change.to}`;
      });
    }
    if (block.chord) {
      const asItem = (chord) => ({ value: chord.chord, label: chord.is_barre ? `${chord.chord} (pestana)` : chord.chord, chord });
      return select('Trocar acorde', asItem(block.chord), options.chords.map(asItem), (value) => {
        const chord = options.chords.find((item) => item.chord === value);
        if (!chord) return;
        block.chord = chord;
        block.title = `Acorde novo: ${chord.chord}`;
      });
    }
    return null;
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

  function BlockRow(block, index, currentIndex = -1, editable = false) {
    const state = index < currentIndex ? ' is-done' : index === currentIndex ? ' is-current' : '';
    const choice = editable ? BlockChoice(block) : null;
    return h('li', { class: `session-block${state}`, style: { '--i': index } },
      h('span', { class: 'session-block__time' }, `${block.minutes} min`),
      h('div', { class: 'session-block__body' },
        h('p', { class: 'session-block__kind' }, KIND_LABELS[block.kind] || block.kind),
        choice || h('p', { class: 'session-block__title' }, block.title)),
      index < currentIndex && icon('check', 18));
  }

  function renderPlan() {
    if (!plan.blocks?.length) {
      replaceContent(root, Picker(), h('p', { class: 'notice' }, 'Ainda não há músicas suficientes. Analise algumas faixas de uma playlist.'));
      return;
    }
    const onlyWarmup = plan.blocks.every((block) => block.kind === 'warmup');
    replaceContent(root, Picker(),
      onlyWarmup && h('p', { class: 'notice' }, 'Por enquanto a sessão tem só o aquecimento. Analise músicas de uma playlist para incluir trocas, músicas e acordes novos.'),
      h('ol', { class: 'session__blocks' }, plan.blocks.map((block, index) => BlockRow(block, index, -1, true))),
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
    stopClick();
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

  function stopClick() {
    clicking = false;
    metronome.stop();
  }

  function WarmupAction(block) {
    let bpm = store.prefs.warmupBpm || block.bpm || 60;
    const bpmValue = h('span', { class: 'stepper__value warmup__bpm', 'aria-live': 'polite' }, `${bpm} bpm`);
    const toggle = h('button', { class: 'btn btn--ghost', type: 'button', 'aria-pressed': clicking, onclick: async () => {
      if (clicking) {
        stopClick();
      } else {
        await metronome.prepare();
        clicking = true;
        metronome.start(bpm);
      }
      toggle.setAttribute('aria-pressed', String(clicking));
      replaceContent(toggle, icon(clicking ? 'pause' : 'metronome', 16), clicking ? 'Parar metrônomo' : 'Ligar metrônomo');
    } }, icon(clicking ? 'pause' : 'metronome', 16), clicking ? 'Parar metrônomo' : 'Ligar metrônomo');

    const changeBpm = (delta) => {
      bpm = Math.min(160, Math.max(40, bpm + delta));
      store.setPrefs({ warmupBpm: bpm });
      bpmValue.textContent = `${bpm} bpm`;
      if (clicking) metronome.start(bpm);
    };

    return h('div', { class: 'warmup' },
      h('ol', { class: 'warmup__steps' }, block.exercises.map((exercise) =>
        h('li', {}, h('strong', {}, exercise.name), h('span', {}, exercise.text)))),
      h('div', { class: 'warmup__click' },
        toggle,
        h('div', { class: 'stepper__controls' },
          h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Diminuir andamento', onclick: () => changeBpm(-5) }, icon('minus', 18)),
          bpmValue,
          h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Aumentar andamento', onclick: () => changeBpm(5) }, icon('plus', 18)))),
      h('p', { class: 'warmup__tip' }, 'Saiu tudo limpo três vezes seguidas? Suba 5 bpm amanhã.'));
  }

  function BlockAction(block) {
    if (block.kind === 'warmup' && block.exercises) return WarmupAction(block);
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
    if (block.kind !== 'warmup') stopClick();
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

  signal.addEventListener('abort', () => {
    stopTick();
    metronome.close();
  });
  if (run) renderRun();
  else renderIdle();
  return root;
}
