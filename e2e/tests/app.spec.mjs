import { expect, test } from '@playwright/test';
import { API, noHorizontalOverflow, open, songRoute } from './helpers.mjs';

const ARTIST = 'Banda Teste';

test.afterEach(async ({ request }) => {
  await request.put(`${API}/api/profile`, { data: { preferred_key: 'G' } });
});

test('every page opens without errors or sideways scroll', async ({ page }) => {
  for (const route of ['/hoje', '/treino', '/progresso', '/playlists', '/repertorio', '/acordes', '/ajustes']) {
    const errors = await open(page, route);
    await expect(page.locator('main h1').first()).toBeVisible();
    await noHorizontalOverflow(page);
    expect(errors, route).toEqual([]);
  }
});

test('Hoje rates songs in the preferred key and offers the Cifra Club search', async ({ page }) => {
  await open(page, '/hoje');
  const card = page.locator('.song-card', { hasText: 'Musica em Si Bemol' });
  await expect(card).toContainText('Fácil tocando em G');
  await expect(page.getByText('Não achou? Digite o nome')).toBeVisible();
  await page.getByLabel('Buscar').fill('yesterday');
  await expect(page.getByRole('button', { name: 'Procurar “yesterday” no Cifra Club' })).toBeVisible();
});

test('the preferred key set in Ajustes changes the ratings', async ({ page }) => {
  await open(page, '/ajustes');
  await page.getByLabel('Tom do violão').selectOption('Bb');
  await expect(page.getByText('Tom do violão: Bb')).toBeVisible();
  await open(page, '/hoje');
  await expect(page.locator('.song-card', { hasText: 'Musica em Si Bemol' })).not.toContainText('tocando em');
});

test('song page moves to G using the key the chords are written in', async ({ page }) => {
  // The site states Eb, but the chords are written in E.
  await open(page, songRoute(ARTIST, 'Afinada Meio Tom'));
  await expect(page.getByText('Tom original: E', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Mudar para G' }).click();
  await expect(page.locator('.dock .stepper__value')).toHaveText('G');
  await expect(page.locator('.chord-strip')).toContainText('Em');
  await expect(page.locator('.chord-strip')).not.toContainText('G#m');
  await expect(page.getByRole('button', { name: 'Mudar para G' })).toHaveCount(0);
});

test('capo option keeps the original sound with G shapes', async ({ page }) => {
  await open(page, songRoute(ARTIST, 'Musica em Si Bemol'));
  await page.getByRole('button', { name: 'G com capotraste na 3ª' }).click();
  await expect(page.locator('.dock .stepper__value')).toHaveText('G');
  await expect(page.locator('.sound-label')).toContainText('capotraste na 3ª casa');
  await expect(page.locator('.sound-label')).toContainText('soa no tom original');
});

test('play scrolls the whole sheet in one column', async ({ page }) => {
  await open(page, songRoute(ARTIST, 'Facil em Sol'));
  await expect(page.locator('.sheet-line').first()).toBeVisible();
  const columns = await page.locator('.sheet').evaluate((el) => getComputedStyle(el).columnCount);
  expect(columns).toBe('auto');
  const before = await page.evaluate(() => window.scrollY);
  await page.getByRole('button', { name: 'Rolar a cifra' }).click();
  await page.waitForTimeout(2500);
  const after = await page.evaluate(() => window.scrollY);
  expect(after).toBeGreaterThan(before);
  await page.getByRole('button', { name: 'Pausar rolagem' }).click();
  const paused = await page.evaluate(() => window.scrollY);
  await page.waitForTimeout(800);
  expect(await page.evaluate(() => window.scrollY)).toBe(paused);
});

test('session blocks can be swapped and start with the warmup', async ({ page }) => {
  await open(page, '/treino');
  await page.getByRole('button', { name: 'Montar sessão' }).click();
  const songChoice = page.getByLabel('Trocar música').first();
  await expect(songChoice).toBeVisible();
  await songChoice.selectOption({ label: 'Facil em Sol · Banda Teste' });
  await expect(songChoice).toHaveValue(/\d+/);
  await page.getByRole('button', { name: /^Começar/ }).click();
  await expect(page.getByRole('heading', { name: 'Aquecimento dos dedos' })).toBeVisible();
  await expect(page.getByText('Cromático 1-2-3-4')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Ligar metrônomo' })).toBeVisible();
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Encerrar' }).click();
  await expect(page.getByRole('button', { name: 'Montar sessão' })).toBeVisible();
});

test('hovering a chord to add shows its shape', async ({ page, isMobile }) => {
  test.skip(isMobile, 'Hover only exists with a mouse.');
  await open(page, '/acordes');
  await page.getByRole('button', { name: 'Adicionar F', exact: true }).hover();
  await expect(page.locator('.peek:hover .peek__card .diagram')).toBeVisible();
});

test('a song can be deleted from the library', async ({ page, isMobile }) => {
  test.skip(isMobile, 'Runs once: the deleted song is shared by both projects.');
  await open(page, songRoute(ARTIST, 'Para Apagar'));
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Excluir música' }).click();
  await expect(page).toHaveURL(/#\/hoje$/);
  await expect(page.locator('.song-card', { hasText: 'Para Apagar' })).toHaveCount(0);
});
