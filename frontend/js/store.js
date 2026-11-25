// Small shared state: server status, user profile and local UI preferences.
import { api } from './api.js';

const PREFS_KEY = 'cifrafy.prefs';
const DEFAULT_PREFS = {
  level: 'Fácil',
  source: 'all',
  withCapo: true,
  simplified: false,
  sheetSize: 15,
  scrollSpeed: 5,
};

function loadPrefs() {
  try {
    return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

const listeners = new Set();

export const store = {
  status: null,
  profile: { known_chords: [], learning_chords: [], avoid_barre: true, preferred_key: 'G' },
  prefs: loadPrefs(),

  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  notify() {
    listeners.forEach((listener) => listener(store));
  },

  setPrefs(patch) {
    store.prefs = { ...store.prefs, ...patch };
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(store.prefs));
    } catch {
      // Private browsing can block storage. The preference still applies to this visit.
    }
  },

  async refreshStatus(signal) {
    const status = await api.status(signal);
    store.status = status;
    if (status.profile) store.profile = status.profile;
    store.notify();
    return status;
  },

  async saveProfile(patch) {
    store.profile = await api.updateProfile(patch);
    if (store.status) store.status = { ...store.status, profile: store.profile };
    store.notify();
    return store.profile;
  },
};
