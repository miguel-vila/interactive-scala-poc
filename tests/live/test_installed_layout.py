"""Exercise skills copied side by side, without imports from the checkout."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from urllib.request import urlopen

from tests.support import HTML_SKILL, LIVE_SKILL, FIXTURES
from tests.live.test_kernel import page_grid


class InstalledLayout(unittest.TestCase):
    def test_copied_html_builds_offline_page_and_live_sibling_serves_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = root / 'skills'
            for skill in (HTML_SKILL,):
                shutil.copytree(skill, skills / skill.name, ignore=shutil.ignore_patterns('__pycache__'))
                self.assertEqual({p.name for p in (skills / skill.name).iterdir()},
                                 {'SKILL.md', 'scripts', 'references'})
            page = root / 'example.html'
            grid = root / 'grid.json'
            narrative = root / 'narrative.json'
            preflight = root / 'preflight.json'
            for name in ('head', 'base'):
                (root / (name + '.txt')).write_text('/fake/' + name + '.jar\n')
            report = {'ok': True, 'module': 'core', 'tempDir': str(root),
                      'toolchain': {'command': ['scala-cli']},
                      **{name: {'sha': name + '-sha', 'builds': True, 'scalaVersion': '3.5.0',
                                'catsEffect': {'present': False}, 'classpathFile': str(root / (name + '.txt'))}
                         for name in ('head', 'base')}}
            preflight.write_text(json.dumps(report))
            grid.write_text(json.dumps(page_grid(root)))
            narrative.write_text(json.dumps({'title': 'Copied skill', 'background': '<p>Context</p>',
                'intuition': '<p>Idea</p>', 'code': '<div class="scala-cell" data-cell="example"></div>'}))
            built = subprocess.run([sys.executable, str(skills / HTML_SKILL.name / 'scripts/build-page.py'),
                '--grid', str(grid), '--narrative', str(narrative), '--slug', 'copied',
                '--output', str(page), '--preflight', str(preflight)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            # Build with only the copied HTML skill present, then install its sibling.
            shutil.copytree(LIVE_SKILL, skills / LIVE_SKILL.name, ignore=shutil.ignore_patterns('__pycache__'))
            self.assertEqual({p.name for p in (skills / LIVE_SKILL.name).iterdir()},
                             {'SKILL.md', 'scripts', 'references'})
            disk = page.read_bytes()
            self.assertNotIn(b'.live-panel', disk)
            env = dict(os.environ, PATH=str(FIXTURES / 'bin') + os.pathsep + os.environ['PATH'])
            with subprocess.Popen([sys.executable, str(skills / LIVE_SKILL.name / 'scripts/kernel.py'),
                    '--page', str(page), '--temp-dir', str(root)], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                try:
                    started = json.loads(process.stdout.readline())
                    self.assertTrue(started['ok'], started)
                    with urlopen(started['url'].split('#')[0], timeout=5) as response:
                        served = response.read()
                    self.assertEqual(served.count(b'Live mode activates only'), 1)
                    self.assertEqual(page.read_bytes(), disk)
                finally:
                    if process.poll() is None: process.send_signal(signal.SIGTERM)
                    process.communicate(timeout=5)

    def test_live_only_installation_reports_missing_sibling(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / 'skills' / LIVE_SKILL.name
            shutil.copytree(LIVE_SKILL, skill, ignore=shutil.ignore_patterns('__pycache__'))
            for script in ('kernel.py', 'live_driver.py'):
                result = subprocess.run([sys.executable, str(skill / 'scripts' / script),
                    '--page', '/unused.html'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(result.stdout.splitlines()), 1)
                diagnostic = json.loads(result.stdout)['diagnostic']
                self.assertIn('explain-scala-diff-html is not installed next to this skill', diagnostic)
                self.assertIn(str(skill.parent / HTML_SKILL.name / 'scripts'), diagnostic)
                self.assertIn('npx skills add miguel-vila/interactive-scala-poc --skill explain-scala-diff-html', diagnostic)
                self.assertEqual(result.stderr, '')
