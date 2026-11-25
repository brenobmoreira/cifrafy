// Browser tests against a throwaway backend (seeded SQLite, ports 8791/8792). Nothing touches the
// developer's database or Cifra Club. Locally: BACKEND_PYTHON=../backend/.venv/bin/python npm test
import { defineConfig, devices } from '@playwright/test';

const API = 'http://127.0.0.1:8791';
const WEB = 'http://127.0.0.1:8792';
const python = process.env.BACKEND_PYTHON || 'python';
const env = {
  DATABASE_PATH: `${process.cwd()}/.data/e2e.db`,
  SPOTIFY_CACHE_PATH: `${process.cwd()}/.data/token.json`,
  SPOTIFY_CLIENT_ID: '',
  SPOTIFY_CLIENT_SECRET: '',
  ALLOWED_ORIGINS: WEB,
  ALLOWED_HOSTS: '127.0.0.1,localhost',
};

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: WEB, trace: 'retain-on-failure' },
  projects: [
    { name: 'mobile', use: { ...devices['Pixel 7'], browserName: 'chromium' } },
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
  ],
  webServer: [
    {
      command: `${python} seed.py && cd ../backend && ${python} -m uvicorn src.api:app --host 127.0.0.1 --port 8791`,
      url: `${API}/health`,
      env,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `${python} -m http.server 8792 --bind 127.0.0.1 --directory ../frontend`,
      url: WEB,
      reuseExistingServer: false,
      stderr: 'ignore',
    },
  ],
});
