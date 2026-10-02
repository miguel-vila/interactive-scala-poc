const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const output = path.join(process.cwd(), '.validation');
const playwrightPath = process.argv[2] ? path.resolve(process.argv[2]) : path.join(output, 'browser/node_modules/playwright');
const {chromium} = require(playwrightPath);

(async () => {
  const executablePath = process.argv[3];
  const browser = await chromium.launch(executablePath ? {executablePath} : {});
  try {
    const context = await browser.newContext({offline: true});
    const page = await context.newPage();
    const errors = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (/^https?:/.test(request.url())) requests.push(request.url()); });
    let combinations = 0;
    const files = ['2026-10-01-explanation-scala-diff.html', 'head-only.html', 'no-diff.html'];
    const realPage = '2026-10-01-explanation-optional-source-regions.html';
    if (fs.existsSync(path.join(output, realPage))) files.push(realPage);
    for (const filename of files) {
      await page.goto(pathToFileURL(path.join(output, filename)).href);
      const cells = await page.locator('.scala-cell').count();
      assert(cells > 0);
      for (let ci = 0; ci < cells; ci++) {
        const root = page.locator('.scala-cell').nth(ci);
        const id = await root.getAttribute('data-cell');
        const data = JSON.parse(await page.locator('#cell-' + id).textContent());
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
        for (const revision of Object.keys(data.driverSource)) {
          assert((await root.textContent()).includes(data.driverSource[revision]));
        }
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
    await page.goto(pathToFileURL(path.join(output, '2026-10-01-explanation-scala-diff.html')).href);
    await page.screenshot({path: path.join(output, 'mobile.png'), fullPage: true});
    await page.setViewportSize({width: 1200, height: 900});
    await page.screenshot({path: path.join(output, 'desktop.png'), fullPage: true});
    await page.locator('.scala-cell').first().screenshot({path: path.join(output, 'console.png')});
    assert.deepEqual(errors, []);
    assert.deepEqual(requests, []);
    console.log(JSON.stringify({ok: true, offlineCombinations: combinations, pages: files.length, quizAnswers: files.length * 5, externalRequests: 0, pageErrors: 0}));
    await context.close();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
