import { h, plural, replaceContent } from '../dom.js';
import { study } from '../study.js';
import { formatStudyTime, localDate } from '../studyTimer.js';
import { GoalProgress } from '../components/StudyTimer.js';
import { ErrorState, Skeleton, toast } from '../components/states.js';

const HISTORY_DAYS = 30;

function lastDays(days) {
  const byDate = new Map((days || []).map((day) => [day.date, day.seconds]));
  const result = [];
  const cursor = new Date();
  cursor.setDate(cursor.getDate() - (HISTORY_DAYS - 1));
  for (let index = 0; index < HISTORY_DAYS; index += 1) {
    const date = localDate(cursor);
    result.push({ date, label: new Date(cursor), seconds: byDate.get(date) || 0 });
    cursor.setDate(cursor.getDate() + 1);
  }
  return result;
}

function intensity(seconds, goalMinutes) {
  if (!seconds) return 0;
  const ratio = seconds / (Math.max(1, goalMinutes || 20) * 60);
  if (ratio >= 1) return 4;
  if (ratio >= 0.66) return 3;
  if (ratio >= 0.33) return 2;
  return 1;
}

function Stat(label, value) {
  return h('div', { class: 'stat' },
    h('p', { class: 'stat__value' }, value),
    h('p', { class: 'stat__label' }, label));
}

function History(summary) {
  const days = lastDays(summary.days);
  const format = new Intl.DateTimeFormat('pt-BR', { day: 'numeric', month: 'short' });
  const today = localDate();
  const studied = days.filter((day) => day.seconds > 0).length;
  return h('div', { class: 'history' },
    h('ul', { class: 'history__grid', 'aria-label': `Últimos ${HISTORY_DAYS} dias` }, days.map((day) => {
      const text = `${format.format(day.label)}: ${day.seconds ? formatStudyTime(day.seconds) : 'sem estudo'}`;
      return h('li', {
        class: `history__day history__day--${intensity(day.seconds, summary.goal_minutes)}${day.date === today ? ' is-today' : ''}`,
        title: text,
        'aria-label': text,
      }, day.label.getDate());
    })),
    h('p', { class: 'section-sub' }, `${plural(studied, 'dia')} com estudo nos últimos ${HISTORY_DAYS}. A cor fica mais forte perto da sua meta.`));
}

export function ProgressView({ signal }) {
  const content = h('div', {}, Skeleton({ count: 4, variant: 'row' }));
  const input = h('input', { id: 'study-goal', class: 'input', type: 'number', min: 1, max: 600, inputMode: 'numeric' });
  const feedback = h('p', { class: 'form-feedback', role: 'status' });
  const goalForm = h('form', { class: 'goal-form', onsubmit: saveGoal },
    h('label', { for: 'study-goal' }, 'Meta diária em minutos'),
    h('div', { class: 'import-form__row' }, input,
      h('button', { class: 'btn btn--primary', type: 'submit' }, 'Salvar')),
    feedback);

  async function saveGoal(event) {
    event.preventDefault();
    const minutes = Number.parseInt(input.value, 10);
    feedback.textContent = '';
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 600) {
      feedback.textContent = 'Use um valor entre 1 e 600 minutos.';
      return;
    }
    try {
      await study.setGoal(minutes);
      toast(`Meta de ${minutes} minutos salva.`, 'ok');
    } catch (error) {
      feedback.textContent = error.message;
    }
  }

  function render(summary) {
    if (!summary) return;
    if (document.activeElement !== input) input.value = summary.goal_minutes || '';
    replaceContent(content,
      h('div', { class: 'panel' }, h('h2', { class: 'section-title' }, 'Hoje'), GoalProgress(summary),
        summary.goal_met_today && h('p', { class: 'study-goal__done' }, 'Meta de hoje concluída.')),
      h('div', { class: 'stats' },
        Stat('Últimos 7 dias', formatStudyTime(summary.week_seconds)),
        Stat('Últimos 30 dias', formatStudyTime(summary.month_seconds)),
        Stat('No total', formatStudyTime(summary.total_seconds)),
        Stat('Sequência', plural(summary.streak_days || 0, 'dia'))),
      h('div', { class: 'panel' }, h('h2', { class: 'section-title' }, 'Últimos 30 dias'), History(summary)));
  }

  async function load() {
    try {
      render(await study.refresh(signal));
    } catch (error) {
      if (error.name !== 'AbortError') replaceContent(content, ErrorState({ error, onRetry: load }));
    }
  }

  const unsubscribe = study.subscribe(render);
  signal.addEventListener('abort', unsubscribe);
  load();

  return h('section', { class: 'view view--progress' },
    h('header', { class: 'page-head' },
      h('div', {}, h('p', { class: 'eyebrow' }, 'Sua constância'), h('h1', { class: 'page-title' }, 'Progresso'))),
    h('div', { class: 'progress-layout' }, content, h('div', { class: 'panel' }, goalForm)));
}
