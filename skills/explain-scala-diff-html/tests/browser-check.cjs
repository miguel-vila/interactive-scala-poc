const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
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
      if (await page.locator('#live-provenance').count()) {
        assert.equal(await page.locator('.live-panel').count(), cells);
        assert((await page.locator('.live-panel').first().textContent()).includes('Run python3'));
      }
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
    if (!requestedPage && fs.existsSync(path.join(output, 'preflight.json'))) {
      const script = path.join(__dirname, '../scripts/kernel.py');
      const fixtureBin = path.join(__dirname, 'fixtures/bin');
      const child = spawn('python3', [script, '--page', path.join(output, files[0]),
        '--preflight', path.join(output, 'preflight.json'), '--temp-dir', output,
        '--idle-minutes', '2'], {env: {...process.env, PATH: fixtureBin + path.delimiter + process.env.PATH}});
      let stderr = '';
      child.stderr.on('data', chunk => stderr += chunk);
      try {
        const started = await new Promise((resolve, reject) => {
          let buffer = '';
          const timer = setTimeout(() => reject(new Error('Kernel did not start: ' + stderr)), 10000);
          child.stdout.on('data', chunk => {
            buffer += chunk;
            if (!buffer.includes('\n')) return;
            clearTimeout(timer);
            try { resolve(JSON.parse(buffer.split('\n')[0])); }
            catch (error) { reject(error); }
          });
          child.on('exit', code => { clearTimeout(timer); reject(new Error('Kernel exited ' + code + ': ' + stderr)); });
        });
        assert(started.ok, started.diagnostic);
        const liveContext = await browser.newContext();
        const live = await liveContext.newPage();
        const liveErrors = [];
        live.on('pageerror', error => liveErrors.push(error.message));
        await live.goto(started.url);
        const first = live.locator('.live-panel').first();
        await first.locator('summary').first().click();
        await first.locator('textarea').waitFor();
        assert.equal(await live.locator('.scala-cell').count(),
          (await live.locator('.scala-cell[data-cell]').count()) + 1);
        assert.equal(await live.evaluate(() => location.hash), '');
        await first.locator('textarea').fill('PRINT_HELLO');
        await first.locator('textarea').press('Meta+Enter');
        await first.getByText('Live results differ between revisions.').waitFor();
        assert.equal(await first.locator('.live-results > div').count(), 2);
        assert((await first.textContent()).includes('hello from fake JVM'));
        assert(!(await first.textContent()).includes('Generated live driver'));
        await first.locator('textarea').fill('val n = 1\nBAD_COMPILE');
        await first.getByRole('button', {name: 'Run', exact: true}).click();
        await first.getByRole('button', {name: 'Editor line 2'}).first().waitFor();
        assert((await first.textContent()).includes('fake compiler rejection'));
        await live.setViewportSize({width: 375, height: 900});
        assert.equal(await live.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        const unauthorizedContext = await browser.newContext();
        const unauthorized = await unauthorizedContext.newPage();
        await unauthorized.goto(started.url.replace(/#k=.*/, '#k=wrong'));
        await unauthorized.locator('.live-panel summary').first().click();
        await unauthorized.getByText('Open the URL printed by the kernel to reconnect.').first().waitFor();
        assert.deepEqual(liveErrors, []);
        console.log(JSON.stringify({ok: true, liveColumns: 2, compilerLine: 2, unauthorized: true}));
        await unauthorizedContext.close();
        await liveContext.close();
      } finally {
        child.kill('SIGTERM');
        if (child.exitCode === null) await new Promise(resolve => child.once('exit', resolve));
      }
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
