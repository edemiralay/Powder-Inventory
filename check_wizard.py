"""Run UI logic against real local API endpoints using a lightweight mock DOM.
Requires Node.js; no browser rendering is claimed by this test.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from urllib.request import urlopen
import powder_inventory_app as app

if not shutil.which('node'):
    raise SystemExit('Install Node.js to run the optional wizard interaction check.')
server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    origin='http://127.0.0.1:'+str(server.server_address[1])
    for route in ('/', '/single', '/single/', '/single?view=calculation', '/scenarios', '/scenarios/', '/scenarios?view=analysis'):
        with urlopen(origin+route) as response:
            html=response.read().decode()
            assert response.status==200 and 'id="batch-workspace"' in html
            assert 'id="single-view"' not in html and 'id="batch-view"' not in html
            assert 'id="home-view"' in html
            assert 'App version 8.0' in html
            assert response.headers['Cache-Control']=='no-store, max-age=0'
            if route=='/':
                assert 'id="home-workspace" class="card"' in html
                assert 'id="single-workspace" class="hidden"' in html
                assert 'id="batch-workspace" class="hidden"' in html
            if route.startswith('/single'):
                assert 'id="home-workspace" class="card hidden"' in html
                assert 'id="single-workspace" class="hidden"' not in html
                assert 'id="batch-workspace" class="hidden"' in html
            if route.startswith('/scenarios'):
                assert 'id="single-workspace" class="hidden"' in html
                assert 'id="batch-workspace" class="hidden"' not in html
    with urlopen(origin+'/examples/scenarios_demo.json') as response:
        demo=json.load(response)
        assert len(demo['scenarios'])==6
        for scenario in demo['scenarios']:app.validate(scenario['inputs'])
    with tempfile.TemporaryDirectory() as tmp:
        html=app.HTML.replace('__DEFAULT__',json.dumps(app.DEFAULT))
        page=Path(tmp)/'page.html';page.write_text(html)
        script=Path(tmp)/'script.js';script.write_text(html.split('<script>')[1].split('</script>')[0])
        result=subprocess.run(['node',str(Path(__file__).with_suffix('.cjs')),str(page),str(script),
            'http://127.0.0.1:'+str(server.server_address[1])])
        raise SystemExit(result.returncode)
finally:
    server.shutdown();server.server_close()
