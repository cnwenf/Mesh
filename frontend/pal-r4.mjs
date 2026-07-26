import { chromium } from '@playwright/test';
const browser = await chromium.launch({ args: ['--disable-web-security'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('pageerror', e => console.log('PAGEERROR:', e.message.slice(0, 100)));
await page.goto('http://localhost:5173/');
await page.waitForTimeout(2000);
// search "settings"
await page.keyboard.press('Control+k');
const palette = page.getByRole('dialog', { name: 'Command palette' });
await palette.waitFor({ state: 'visible', timeout: 8000 });
await palette.getByRole('combobox').fill('settings');
await page.waitForTimeout(500);
let n = await page.getByRole('option').count();
console.log('search "settings" options:', n);
// clear and search "issues", press Enter -> navigate
await palette.getByRole('combobox').fill('issues');
await page.waitForTimeout(500);
n = await page.getByRole('option').count();
console.log('search "issues" options:', n);
await page.keyboard.press('Enter');
await page.waitForTimeout(1500);
console.log('URL after Enter:', page.url());
await page.screenshot({ path: '/tmp/accept-evidence-r4/palette-live.png' });
await browser.close();
