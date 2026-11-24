// Study stopwatch built on saved timestamps, so reloads and background tabs never lose time.
// Pure logic: storage and clock are injected and there is no DOM here.
const STORAGE_KEY = 'cifrafy.studyTimer';

export const MIN_SAVE_SECONDS = 10;
export const MAX_SESSION_SECONDS = 43200;

const IDLE = {
  status: 'idle',
  runningSince: null,
  accumulatedMs: 0,
  kind: 'free',
  songId: null,
  localDate: null,
};

export function localDate(date = new Date()) {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

export function formatClock(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
  const rest = String(seconds % 60).padStart(2, '0');
  return hours ? `${hours}:${minutes}:${rest}` : `${minutes}:${rest}`;
}

export function formatStudyTime(totalSeconds) {
  const minutes = Math.floor(Math.max(0, totalSeconds || 0) / 60);
  if (minutes < 60) return `${minutes}min`;
  const rest = minutes % 60;
  return rest ? `${Math.floor(minutes / 60)}h ${rest}min` : `${Math.floor(minutes / 60)}h`;
}

export function createStudyTimer({ storage = null, now = Date.now, today = () => new Date() } = {}) {
  const listeners = new Set();
  let state = read();

  function read() {
    try {
      const saved = JSON.parse(storage?.getItem(STORAGE_KEY) || 'null');
      return saved?.status ? { ...IDLE, ...saved } : { ...IDLE };
    } catch {
      return { ...IDLE };
    }
  }

  function commit(next) {
    state = next;
    try {
      if (state.status === 'idle') storage?.removeItem(STORAGE_KEY);
      else storage?.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // Private browsing can block storage. The timer still works during this visit.
    }
    listeners.forEach((listener) => listener(timer));
  }

  const timer = {
    get status() {
      return state.status;
    },
    get kind() {
      return state.kind;
    },
    get songId() {
      return state.songId;
    },

    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    elapsedSeconds() {
      const running = state.status === 'running' ? Math.max(0, now() - state.runningSince) : 0;
      return Math.min(MAX_SESSION_SECONDS, Math.floor((state.accumulatedMs + running) / 1000));
    },

    start({ kind = 'free', songId = null } = {}) {
      if (state.status !== 'idle') return;
      commit({ ...IDLE, status: 'running', runningSince: now(), kind, songId, localDate: localDate(today()) });
    },

    pause() {
      if (state.status !== 'running') return;
      commit({
        ...state,
        status: 'paused',
        accumulatedMs: state.accumulatedMs + Math.max(0, now() - state.runningSince),
        runningSince: null,
      });
    },

    resume() {
      if (state.status !== 'paused') return;
      commit({ ...state, status: 'running', runningSince: now() });
    },

    discard() {
      commit({ ...IDLE });
    },

    setKind(kind) {
      if (state.status !== 'idle') commit({ ...state, kind });
    },

    // A guided session or drill keeps its own kind. Only free study becomes song study.
    associateSong(songId) {
      if (state.status === 'idle' || !songId) return;
      if (state.kind === 'free' || state.kind === 'song') commit({ ...state, kind: 'song', songId });
    },

    payload() {
      const body = {
        duration_seconds: timer.elapsedSeconds(),
        local_date: state.localDate || localDate(today()),
        kind: state.kind,
      };
      if (state.songId) body.song_id = state.songId;
      return body;
    },

    // Picks up changes made by the same app in another tab.
    syncFromStorage() {
      state = read();
      listeners.forEach((listener) => listener(timer));
    },
  };
  return timer;
}

export const STUDY_TIMER_KEY = STORAGE_KEY;
