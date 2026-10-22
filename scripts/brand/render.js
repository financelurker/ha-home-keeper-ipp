// Render the brand icon (icon.svg) to the PNGs Home Assistant loads from brand/.
// Usage: node render.js   (needs Playwright with Chromium)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, 'icon.svg');
const OUT = path.join(__dirname, '..', '..', 'custom_components', 'home_keeper_ipp', 'brand');

(async () => {
  const browser = await chromium.launch();
  const svg = fs.readFileSync(SRC, 'utf8');
  for (const [file, size] of [['icon.png', 256], ['icon@2x.png', 512], ['logo.png', 512]]) {
    const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
    const sized = svg.replace('width="256" height="256"', `width="${size}" height="${size}"`);
    await page.setContent(`<html><body style="margin:0;background:transparent">${sized}</body></html>`);
    await page.screenshot({ path: path.join(OUT, file), omitBackground: true });
    await page.close();
    console.log(file, size);
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
