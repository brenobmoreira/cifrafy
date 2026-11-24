// Shared study state: the running stopwatch and the summary saved on the server.
import { api } from './api.js';
import { createStudyTimer, localDate, MIN_SAVE_SECONDS, STUDY_TIMER_KEY } from './studyTimer.js';

function safeStorage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export const studyTimer = createStudyTimer({ storage: safeStorage() });

window.addEventListener('storage', (event) => {
  if (event.key === STUDY_TIMER_KEY) studyTimer.syncFromStorage();
});

const listeners = new Set();

export const study = {
  summary: null,

  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  setSummary(summary) {
    study.summary = summary;
    listeners.forEach((listener) => listener(summary));
    return summary;
  },

  async refresh(signal) {
    return study.setSummary(await api.studySummary({ today: localDate(), days: 30 }, signal));
  },

  async setGoal(minutes) {
    const { goal_minutes: goal } = await api.saveStudyGoal(minutes);
    if (study.summary) {
      const met = (study.summary.today_seconds || 0) >= goal * 60;
      study.setSummary({ ...study.summary, goal_minutes: goal, goal_met_today: met });
    }
    return goal;
  },

  // The timer is only cleared after the server confirms, so a failed save loses nothing.
  async finishTimer() {
    studyTimer.pause();
    const payload = studyTimer.payload();
    if (payload.duration_seconds < MIN_SAVE_SECONDS) {
      studyTimer.discard();
      return { saved: false, tooShort: true };
    }
    const result = await api.saveStudySession(payload);
    studyTimer.discard();
    if (result?.summary) study.setSummary(result.summary);
    else await study.refresh().catch(() => {});
    return { saved: true, seconds: payload.duration_seconds };
  },
};
