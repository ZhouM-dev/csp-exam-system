"""Check template migration preserves submission/auth behavior and offline assets."""
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from csp_exam.web.tabler_theme import components
from csp_exam.web.ui import page
from csp_exam.web.server import Handler


class Frontend(unittest.TestCase):
    def test_submission_form_survives(self):
        source = ('<form id="upload" method="post" action="/upload?c=c1" enctype="multipart/form-data">'
                  '<input id="folder" type="file" name="files" webkitdirectory multiple>'
                  '<input name="c" type="hidden" value="c1">'
                  '<input type="file" name="zipfile" accept=".zip">'
                  '<button type="submit" data-modal-open="confirm">提交</button></form>')
        output = components(source)
        # Ignore class additions only; every remaining attribute and text is identical.
        from html.parser import HTMLParser
        class Shape(HTMLParser):
            def __init__(self): super().__init__(); self.events=[]
            def handle_starttag(self, tag, attrs): self.events.append((tag, [(k,v) for k,v in attrs if k != 'class']))
            def handle_data(self, text): self.events.append(text)
            def handle_endtag(self, tag): self.events.append('/' + tag)
        before, after = Shape(), Shape()
        before.feed(source); after.feed(output)
        self.assertEqual(before.events, after.events)

    def test_embedded_script_and_escaped_source_survive(self):
        js = '<script>let n="<div class=\\"card\\">$1 & 2</div>"; const c={deadline:123};</script>'
        code = '<textarea id="src" name="source">if (a &lt; b) cout &lt;&lt; "中文";</textarea>'
        output = components(js + code)
        self.assertIn(js, output)
        self.assertIn('if (a &lt; b) cout &lt;&lt; "中文";', output)
        rendered = page('考试 <x>', '<div data-deadline="123"></div>', math=True).decode()
        self.assertLess(rendered.index('window.initCountdown = function'), rendered.index('data-deadline="123"'))
        self.assertIn('考试 &lt;x&gt;', rendered)
        self.assertIn('/static/katex/katex.min.js', rendered)

    def test_attribute_content_is_never_rewritten_as_a_class(self):
        for source in ('<input value=\'class="原样"\' name="title">',
                       '<input value=\'class="原样"\' class="csp-name" name="title">'):
            self.assertIn('value=\'class="原样"\'', components(source))

    def test_student_and_admin_use_only_dollar_math_delimiters(self):
        import re
        import shutil
        from csp_exam.web.admin_pages import _math_js
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is required for the formula script regression')
        rendered = page('公式', '<div class="stmt"></div>', math=True, contest_js=False).decode()
        scripts = re.findall(r'<script>(.*?)</script>', rendered, re.S)
        javascript = '''const vm = require('node:vm');
let callback, calls=[];
const boxes=[{id:1},{id:2}];
const context={window:{renderMathInElement:true},
  document:{addEventListener:(event,fn)=>{callback=fn}, querySelectorAll:()=>boxes},
  renderMathInElement:(box,options)=>calls.push({box:box.id,options})};
vm.runInNewContext(INPUT.student,context); callback();
context.window.renderMathInElement=context.renderMathInElement;
vm.runInNewContext(INPUT.admin,context); context.cspRenderMath({id:3});
console.log(JSON.stringify(calls));'''.replace('INPUT', json.dumps({
            'student': '\n'.join(scripts),
            'admin': _math_js().removeprefix('<script>').removesuffix('</script>')}))
        result = subprocess.run([node,'-e',javascript], capture_output=True, text=True, check=True)
        calls = json.loads(result.stdout)
        self.assertEqual([c['box'] for c in calls], [1,2,3])
        expected = ['$$', '$']
        for call in calls:
            self.assertEqual([d['left'] for d in call['options']['delimiters']], expected)
            self.assertIn('code',call['options']['ignoredTags'])
        from csp_exam.tools.export_group import _HTML, SITE_CSS
        self.assertIn("left:'$$',right:'$$',display:true",_HTML)
        self.assertIn("left:'$',right:'$',display:false",_HTML)
        self.assertNotIn("left:'\\\\[",_HTML)
        self.assertIn('.stmt', SITE_CSS)

    def test_unmodified_vendor_assets_and_public_delivery(self):
        vendor = ROOT / 'csp_exam/web/static/vendor/tabler/1.6.1'
        manifest = json.loads((vendor / 'vendor-manifest.json').read_text(encoding='utf-8'))
        for name, sha in manifest['sha256'].items():
            self.assertEqual(hashlib.sha256((vendor / name).read_bytes()).hexdigest(), sha)
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            for asset in ('vendor/tabler/1.6.1/tabler.min.css', 'vendor/tabler/1.6.1/tabler.min.js',
                          'exam-components.css', 'scoreboard.css', 'katex/katex.min.js'):
                conn = http.client.HTTPConnection(*server.server_address)
                conn.request('GET', '/static/' + asset)
                response = conn.getresponse()
                self.assertEqual(response.status,200,asset)
                self.assertEqual(response.read(), (ROOT / 'csp_exam/web/static' / asset).read_bytes())
                conn.close()
            for asset in ('../config.py','vendor/tabler/1.6.1/vendor-manifest.json',
                          'vendor/tabler/1.6.1/LICENSE-Tabler.txt'):
                conn = http.client.HTTPConnection(*server.server_address)
                conn.request('GET','/static/' + asset)
                response = conn.getresponse(); response.read()
                self.assertEqual(response.status,404,asset)
                conn.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_problem_data_follows_configured_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, CSP_EXAM_DATA_DIR=directory, CSP_EXAM_LOG_DIR=directory)
            code = ('import os; from csp_exam.config import DATA_DIR; '
                    'from csp_exam.core.judgelocal import PROBLEMS_DIR; '
                    'assert PROBLEMS_DIR == os.path.join(DATA_DIR,"problems"); '
                    'assert DATA_DIR == os.path.abspath(os.environ["CSP_EXAM_DATA_DIR"])')
            subprocess.run([sys.executable,'-c',code], cwd=ROOT, env=env, check=True)

    def test_receipt_table_finishes_before_following_instructions(self):
        from csp_exam.core import store
        handler = object.__new__(Handler)
        responses = []
        handler._send = lambda data, *args, **kwargs: responses.append(data)
        exam = {'problems':[{'no':1,'pid':'A','name':'number','full':100}]}
        path = 'GD-J12345/number/number.cpp'
        source = b'freopen("number.in","r",stdin); freopen("number.out","w",stdout);'
        files = {path:source, 'GD-J12345/预览学生甲.TXT':b'preview'}
        with patch.object(store,'load_exam',return_value=exam), \
             patch.object(store,'student_name',return_value='预览学生甲'):
            handler._receipt_page('GD-J12345',{'rule':'CSP'},'c1',files,{1:path},[])
        rendered = responses[0].decode()
        self.assertEqual(rendered.count('<table'),rendered.count('</table>'))
        self.assertLess(rendered.index('</table>'),rendered.index('接下来'))
        self.assertIn('GD-J12345/预览学生甲.TXT',rendered)

    def test_unified_upload_area_retains_exact_layout_and_one_submission(self):
        from csp_exam.core import contest_time
        handler = object.__new__(Handler)
        exam = {'problems':[{'no':1,'name':'number'}]}
        with patch.object(contest_time,'phase',return_value='running'):
            rendered = handler._submit_block(exam, {'rule':'CSP'}, 'c1', 'GD-J12345', '学生甲')
        self.assertEqual(rendered.count('data-unified-upload'),1)
        self.assertEqual(rendered.count('type="submit"'),1)
        self.assertIn('webkitdirectory directory multiple hidden',rendered)
        self.assertIn('accept=".zip" hidden',rendered)
        self.assertIn('GD-J12345/',rendered)
        self.assertIn('number.cpp',rendered)
        self.assertIn('学生甲.txt',rendered)
        self.assertIn('/static/submission.js',rendered)
        with patch.object(contest_time,'phase',return_value='ended'):
            closed = handler._submit_block(exam, {'rule':'CSP'}, 'c1', 'GD-J12345', '学生甲')
        self.assertNotIn('<form',closed)


if __name__ == '__main__': unittest.main()
