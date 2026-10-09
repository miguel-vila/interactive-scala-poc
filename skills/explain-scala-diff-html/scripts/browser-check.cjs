const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const fixtureOutput = path.join(process.cwd(), '.validation');
const args = process.argv.slice(2);
const pageFlag = args.indexOf('--page');
let requestedPage;
if (pageFlag !== -1) {
  requestedPage = args[pageFlag + 1];
  if (!requestedPage || requestedPage.startsWith('--')) throw new Error('--page needs an HTML file path');
  requestedPage = path.resolve(requestedPage);
  args.splice(pageFlag, 2);
}
if (args.length > 2) throw new Error('Usage: browser-check.cjs [playwright-module-path] [chromium-executable] [--page page.html]');
const output = requestedPage ? path.dirname(requestedPage) : fixtureOutput;
const screenshotPath = kind => requestedPage
  ? path.join(output, `${path.parse(requestedPage).name}-${kind}.png`)
  : path.join(output, `${kind}.png`);
const playwrightPath = args[0] ? path.resolve(args[0]) : path.join(fixtureOutput, 'browser/node_modules/playwright');
let chromium;
try {
  ({chromium} = require(playwrightPath));
} catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND') throw error;
  throw new Error(`Playwright is missing at ${playwrightPath}. Install it in a disposable directory as described in tests/README.md, then pass that directory's node_modules/playwright path.`);
}

(async () => {
  const executablePath = args[1];
  const browser = await chromium.launch(executablePath ? {executablePath} : {});
  try {
    if (!requestedPage) fs.mkdirSync(output, {recursive: true});
    const context = await browser.newContext({offline: true});
    const page = await context.newPage();
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (/^https?:/.test(request.url())) requests.push(request.url()); });
    let combinations = 0;
    const files = requestedPage ? [path.resolve(requestedPage)] :
      ['2026-10-01-explanation-scala-diff.html', 'head-only.html', 'no-diff.html'];
    const realPage = '2026-10-01-explanation-optional-source-regions.html';
    if (!requestedPage && fs.existsSync(path.join(output, realPage))) files.push(realPage);
    for (const filename of files) {
      const filePath = path.isAbsolute(filename) ? filename : path.join(output, filename);
      if (!fs.existsSync(filePath)) throw new Error(`HTML page is missing: ${filePath}`);
      await page.goto(pathToFileURL(filePath).href);
      const cells = await page.locator('.scala-cell').count();
      assert(cells > 0);
      assert.equal(await page.locator('.live-panel').count(), 0);
      assert.equal(await page.locator('#live-provenance').count(), 0);
      assert.equal(await page.locator('#page-provenance').count(), 1);
      for (let ci = 0; ci < cells; ci++) {
        const root = page.locator('.scala-cell').nth(ci);
        const id = await root.getAttribute('data-cell');
        const data = JSON.parse(await page.locator('#cell-' + id).textContent());
        assert.equal(data.driverSource, undefined);
        for (const row of data.rows) {
          for (let pi = 0; pi < data.params.length; pi++) {
            const index = data.params[pi].values.findIndex(v => JSON.stringify(v) === JSON.stringify(row.values[pi]));
            assert(index >= 0);
            await root.locator('select').nth(pi).selectOption(String(index));
          }
          const result = data.results[row.key];
          const columns = root.locator('.scala-results > div > pre');
          assert.equal(await columns.count(), result.base ? 2 : 1);
          const expected = result.base ? [result.base, result.head] : [result.head];
          for (let ri = 0; ri < expected.length; ri++) assert.equal(await columns.nth(ri).textContent(), expected[ri].render);
          assert.equal(await root.locator('.scala-status.changed').count(), result.differs ? 1 : 0);
          combinations++;
        }
        assert(!(await root.textContent()).includes('Generated driver'));
        if (filename === 'head-only.html') assert((await root.textContent()).includes('Base revision did not build'));
      }
      assert.equal(await page.locator('#quiz-list .q').count(), 5);
      const correctButtons = await page.evaluate(() => {
        return Array.from(document.querySelectorAll('#quiz-list .q')).map((card, qi) => {
          const correct = QUIZ[qi].options.find(opt => opt.correct).text;
          return Array.from(card.querySelectorAll('button')).findIndex(button => button.textContent === correct);
        });
      });
      for (let qi = 0; qi < 5; qi++) await page.locator('#quiz-list .q').nth(qi).locator('button').nth(correctButtons[qi]).click();
      assert.equal(await page.locator('#score').textContent(), '5 correct out of 5 answered.');
      await page.setViewportSize({width: 375, height: 900});
      for (const details of await page.locator('.scala-cell details').all()) await details.evaluate(el => el.open = true);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      assert.equal(overflow, false, 'Page must not scroll sideways at phone width');
    }
    const screenshotPage = path.isAbsolute(files[0]) ? files[0] : path.join(output, files[0]);
    await page.goto(pathToFileURL(screenshotPage).href);
    await page.screenshot({path: screenshotPath('mobile'), fullPage: false});
    await page.setViewportSize({width: 1200, height: 900});
    await page.screenshot({path: screenshotPath('desktop'), fullPage: false});
    await page.locator('.scala-cell').first().screenshot({path: screenshotPath('console')});
    assert.deepEqual(errors, []);
    assert.deepEqual(requests, []);
    console.log(JSON.stringify({ok: true, offlineCombinations: combinations, pages: files.length, quizAnswers: files.length * 5, externalRequests: 0, pageErrors: 0}));
    await context.close();

  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
