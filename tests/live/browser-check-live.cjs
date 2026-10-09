const assert = require('node:assert/strict');
const fs = require('node:fs');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
const {spawn} = require('node:child_process');
const output = path.resolve(process.argv[4] || '.validation');
const {chromium} = require(path.resolve(process.argv[2] || '.validation/browser/node_modules/playwright'));

(async () => {
  const executablePath = process.argv[3];
  const browser = await chromium.launch(executablePath ? {executablePath} : {});
  try {
      const script = path.join(__dirname, '../../skills/explain-scala-diff-live/scripts/kernel.py');
      const fixtureBin = path.join(__dirname, '../fixtures/bin');
      const child = spawn('python3', [script, '--page', path.join(output, '2026-10-01-explanation-scala-diff.html'),
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
        const pageResponse = await live.goto(started.url);
        const servedHtml = await pageResponse.text();
        const first = live.locator('.live-panel').first();
        await first.locator('summary').first().click();
        await first.locator('textarea').waitFor();
        assert.equal(await live.locator('.scala-cell').count(),
          (await live.locator('.scala-cell[data-cell]').count()) + 1);
        assert.equal(await live.evaluate(() => location.hash), '');
        const expectedEditor = await first.evaluate(panel => {
          const root = panel.closest('.scala-cell');
          const data = JSON.parse(document.getElementById('cell-' + root.dataset.cell).textContent);
          const imports = data.imports.map(value => value.startsWith('import ') ? value : 'import ' + value).join('\n');
          const shown = root.querySelector(':scope > pre').textContent;
          const bindings = shown.endsWith(data.call) ? shown.slice(0, -data.call.length) : shown;
          return [imports, bindings, data.setup, data.call].filter(Boolean).join('\n').trim();
        });
        assert.equal(await first.locator('textarea').inputValue(), expectedEditor);
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
        const saved = path.join(output, 'saved-served.html');
        fs.writeFileSync(saved, servedHtml);
        const savedContext = await browser.newContext({offline: true});
        const savedPage = await savedContext.newPage();
        const savedRequests = [];
        savedPage.on('request', request => { if (/^https?:/.test(request.url())) savedRequests.push(request.url()); });
        await savedPage.goto(pathToFileURL(saved).href);
        assert.equal(await savedPage.locator('.live-panel').count(), 0);
        assert.deepEqual(savedRequests, []);
        await savedContext.close();
        child.kill('SIGTERM');
        if (child.exitCode === null) await new Promise(resolve => child.once('exit', resolve));
        await first.locator('textarea').fill('1');
        await first.getByRole('button', {name: 'Run', exact: true}).click();
        await first.getByText('Kernel unavailable. Restart it with:', {exact: false}).waitFor();
        assert((await first.textContent()).includes(script));
        assert((await first.textContent()).includes('--preflight'));
        assert.deepEqual(liveErrors, []);
        console.log(JSON.stringify({ok: true, liveColumns: 2, compilerLine: 2, unauthorized: true, savedPageInert: true, restartCommand: true}));
        await unauthorizedContext.close();
        await liveContext.close();
      } finally {
        child.kill('SIGTERM');
        if (child.exitCode === null) await new Promise(resolve => child.once('exit', resolve));
      }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
