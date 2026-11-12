import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from render_app import create_app

HOST = 'powder.example.test'
ORIGIN = 'https://' + HOST
SETTINGS = {'POWDER_DOMAIN': HOST, 'POWDER_USERNAME': 'test-user',
            'POWDER_PASSWORD': 'test-fixture-password-only',
            'POWDER_SECRET_KEY': 'a-test-fixture-secret-not-for-production'}
ROOT = Path(__file__).resolve().parent


class HostedAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(SETTINGS | {'RENDER_EXTERNAL_HOSTNAME': 'test-powder.onrender.com'})

    def setUp(self):
        self.client = self.app.test_client()

    def get(self, path):
        return self.client.get(path, base_url=ORIGIN)

    def post(self, path, *, origin=ORIGIN, **kwargs):
        return self.client.post(path, base_url=ORIGIN, headers={'Origin': origin}, **kwargs)

    def token(self, response):
        return re.search(rb'name="csrf" value="([^"]+)"', response.data).group(1).decode()

    def sign_in(self):
        data = {'csrf': self.token(self.get('/login')), 'username': SETTINGS['POWDER_USERNAME'],
                'password': SETTINGS['POWDER_PASSWORD']}
        response = self.post('/login', data=data)
        self.assertEqual(response.status_code, 303)
        self.assertIn('__Host-powder_session=', response.headers['Set-Cookie'])
        for attribute in ('Secure', 'HttpOnly', 'SameSite=Strict'):
            self.assertIn(attribute, response.headers['Set-Cookie'])

    def test_private_routes_and_health(self):
        self.assertEqual(self.get('/healthz').json, {'status': 'ok'})
        self.assertEqual(self.client.get('/healthz', base_url='http://test-powder.onrender.com').status_code, 200)
        self.assertEqual(self.client.get('/healthz', base_url='https://unknown.example').status_code, 400)
        for path in ('/', '/single', '/scenarios'):
            self.assertEqual(self.get(path).location, '/login')
        self.assertEqual(self.get('/examples/scenarios_demo.json').status_code, 401)
        for path in ('/solve', '/classify', '/validate', '/import_excel'):
            self.assertEqual(self.post(path, json={}).status_code, 401)
        self.assertEqual(self.client.get('/login', base_url='http://' + HOST).status_code, 308)

    def test_login_and_logout(self):
        self.assertEqual(self.post('/login', data={'csrf': 'wrong'}).status_code, 403)
        bad = {'csrf': self.token(self.get('/login')), 'username': 'wrong', 'password': 'wrong'}
        self.assertEqual(self.post('/login', data=bad).status_code, 401)
        self.sign_in()
        page = self.get('/scenarios')
        nonce = re.search(rb'<script nonce="([^"]+)">', page.data).group(1).decode()
        self.assertIn(f"'nonce-{nonce}'", page.headers['Content-Security-Policy'])
        self.assertIn(b'Sign out', page.data)
        self.assertEqual(self.post('/logout', data={'csrf': 'wrong'}).status_code, 403)
        self.assertEqual(self.post('/logout', data={'csrf': self.token(page)}).location, '/login')
        self.assertEqual(self.get('/').location, '/login')

    def test_authorized_calculations(self):
        self.sign_in()
        self.assertEqual(self.get('/examples/scenarios_demo.json').status_code, 200)
        self.assertEqual(self.post('/solve', origin='https://unknown.example', json={}).status_code, 403)
        self.assertEqual(self.post('/solve', data='{"x":NaN}', content_type='application/json').status_code, 400)
        for name in ('known', 'rop', 'outl'):
            with self.subTest(name=name):
                payload = json.loads((ROOT / f'examples/{name}.json').read_text())
                self.assertEqual(self.post('/validate', json=payload).json, {'valid': True})
                if name != 'known':
                    self.assertEqual(self.post('/classify', json=payload).status_code, 200)
                result = self.post('/solve', json=payload)
                self.assertEqual(result.status_code, 200)
                self.assertIsNotNone(result.json['best'])

    def test_calculation_lock_releases_on_error(self):
        self.sign_in()
        with patch.dict('render_app.OPERATIONS', solve=lambda data: (_ for _ in ()).throw(ValueError('Bad input'))):
            self.assertEqual(self.post('/solve', json={}).status_code, 400)
        with patch.dict('render_app.OPERATIONS', solve=lambda data: {'ok': True}):
            self.assertEqual(self.post('/solve', json={}).json, {'ok': True})
