import { h, replaceContent, showDialog } from '../dom.js';
import { study, studyTimer } from '../study.js';
import { formatClock, formatStudyTime, MIN_SAVE_SECONDS } from '../studyTimer.js';
import { icon } from './icons.js';
import { toast } from './states.js';

export function GoalProgress(summary) {
  if (!summary) return h('p', { class: 'study-goal__text' }, 'Carregando seu tempo de hoje');
  const goal = summary.goal_minutes || 0;
  const seconds = summary.today_seconds || 0;
  const percent = goal ? Math.min(100, Math.round((seconds / (goal * 60)) * 100)) : 0;
  return h('div', { class: 'study-goal' },
    h('p', { class: 'study-goal__text' },
      h('strong', {}, formatStudyTime(seconds)), goal ? ` de ${goal}min hoje` : ' hoje'),
    goal > 0 && h('div', {
      class: `study-goal__bar${percent >= 100 ? ' is-met' : ''}`,
      role: 'progressbar',
      'aria-label': 'Meta de hoje',
      'aria-valuemin': 0,
      'aria-valuemax': 100,
      'aria-valuenow': percent,
    }, h('span', { style: { width: `${percent}%` } })));
}

export function StudyTimerControl({ signal } = {}) {
  const pillClock = h('span', { class: 'timer-pill__clock' });
  const pill = h('button', { class: 'timer-pill', type: 'button', onclick: () => openDialog() },
    icon('timer', 18), pillClock);
  const bigClock = h('p', { class: 'timer-dialog__clock', role: 'timer' });
  const contextLabel = h('p', { class: 'timer-dialog__context' });
  const actions = h('div', { class: 'timer-dialog__actions' });
  const goalSlot = h('div');
  const dialog = h('dialog', {
    class: 'timer-dialog',
    'aria-label': 'Cronômetro de estudo',
    onclick: (event) => {
      if (event.target === dialog) dialog.close();
    },
  },
  h('button', { class: 'icon-btn timer-dialog__close', type: 'button', 'aria-label': 'Fechar', onclick: () => dialog.close() }, icon('close', 18)),
  h('p', { class: 'eyebrow' }, 'Tempo de estudo'),
  bigClock,
  contextLabel,
  actions,
  goalSlot,
  h('a', { class: 'text-link', href: '#/progresso', onclick: () => dialog.close() }, 'Ver meu progresso'));

  const KIND_LABELS = { free: 'Estudo livre', song: 'Estudando uma música', changes: 'Treino de trocas', session: 'Sessão de prática' };

  async function finish() {
    try {
      const result = await study.finishTimer();
      if (result.tooShort) toast(`Tempos abaixo de ${MIN_SAVE_SECONDS}s não são salvos.`, 'info');
      else toast(`${formatStudyTime(result.seconds) === '0min' ? `${result.seconds}s` : formatStudyTime(result.seconds)} salvos no seu dia.`, 'ok');
    } catch (error) {
      toast(`Não foi possível salvar. O tempo continua guardado aqui. ${error.message}`, 'error');
    }
  }

  function discard() {
    if (window.confirm('Descartar este tempo sem salvar?')) studyTimer.discard();
  }

  function renderActions() {
    const { status } = studyTimer;
    const button = (label, iconName, onclick, variant = 'ghost') =>
      h('button', { class: `btn btn--${variant}`, type: 'button', onclick }, icon(iconName, 17), label);
    if (status === 'idle') {
      replaceContent(actions, button('Iniciar', 'play', () => studyTimer.start(), 'primary'));
    } else {
      replaceContent(actions,
        status === 'running'
          ? button('Pausar', 'pause', () => studyTimer.pause())
          : button('Continuar', 'play', () => studyTimer.resume()),
        button('Concluir', 'check', finish, 'primary'),
        button('Descartar', 'trash', discard, 'quiet'));
    }
  }

  function tick() {
    const seconds = studyTimer.elapsedSeconds();
    const { status } = studyTimer;
    const clock = formatClock(seconds);
    pillClock.textContent = status === 'idle' ? 'Estudar' : clock;
    bigClock.textContent = clock;
    pill.dataset.status = status;
    pill.setAttribute('aria-label', status === 'idle'
      ? 'Abrir cronômetro de estudo'
      : `Cronômetro ${status === 'running' ? 'rodando' : 'pausado'}: ${clock}`);
    contextLabel.textContent = status === 'idle' ? 'Inicie quando pegar o violão' : KIND_LABELS[studyTimer.kind] || KIND_LABELS.free;
  }

  function onTimerChange() {
    tick();
    renderActions();
    // The action buttons were rebuilt: keep the keyboard inside the dialog.
    if (dialog.open && !dialog.contains(document.activeElement)) actions.querySelector('button')?.focus();
  }

  const renderGoal = () => replaceContent(goalSlot, GoalProgress(study.summary));

  function openDialog() {
    renderGoal();
    onTimerChange();
    if (!dialog.isConnected) document.body.append(dialog);
    showDialog(dialog, { focus: () => actions.querySelector('button') });
    study.refresh().catch(() => {});
  }

  const unsubscribeTimer = studyTimer.subscribe(onTimerChange);
  const unsubscribeStudy = study.subscribe(renderGoal);
  const interval = setInterval(tick, 1000);
  document.addEventListener('visibilitychange', tick);

  signal?.addEventListener('abort', () => {
    unsubscribeTimer();
    unsubscribeStudy();
    clearInterval(interval);
    document.removeEventListener('visibilitychange', tick);
    if (dialog.open) dialog.close();
    dialog.remove();
  });

  onTimerChange();
  renderGoal();
  return pill;
}
