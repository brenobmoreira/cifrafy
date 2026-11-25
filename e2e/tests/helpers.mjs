import { expect } from '@playwright/test';

export const API = 'http://127.0.0.1:8791';

// The page reads the API address from ?api=, so every route goes through here.
export async function open(page, route) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`/?api=${encodeURIComponent(API)}#${route}`);
  return errors;
}

export const songRoute = (artist, track) => `/tocar/${encodeURIComponent(artist)}/${encodeURIComponent(track)}`;

export async function noHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}
