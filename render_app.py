"""Password-protected Render entry point for the Powder Inventory DSS."""
from collections import deque
from datetime import timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
from threading import Lock
import time

from flask import Flask, Response, abort, g, jsonify, redirect, render_template_string, request, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from powder_inventory_app import DEFAULT, HTML, VERSION, characterize_inputs, import_excel, solve, validate

ROOT = Path(__file__).resolve().parent
SESSION_SECONDS = 8 * 60 * 60
MAX_BODY = 15 * 1024 * 1024
OPERATIONS = {'solve': solve, 'classify': lambda d: characterize_inputs(d['demand'], d['settings']),
              'validate': lambda d: (validate(d), {'valid': True})[1],
              'import_excel': lambda d: import_excel(d['data'])}


def create_app(settings=None):
    settings = os.environ if settings is None else settings
    username = settings.get('POWDER_USERNAME', '')
    password = settings.get('POWDER_PASSWORD', '')
    secret = settings.get('POWDER_SECRET_KEY', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', username):
        raise RuntimeError('Set POWDER_USERNAME to 1–64 letters, digits, underscores or hyphens.')
    if not 16 <= len(password) <= 256:
        raise RuntimeError('Set POWDER_PASSWORD to a unique password of 16–256 characters.')
    if len(secret) < 32 or hmac.compare_digest(secret.encode(), password.encode()):
        raise RuntimeError('Set POWDER_SECRET_KEY to a separate random secret of at least 32 characters.')
    hosts = {settings.get('POWDER_DOMAIN', 'powder.demiralays.com').lower()}
    render_host = settings.get('RENDER_EXTERNAL_HOSTNAME', '').lower()
    if render_host:
        if not render_host.endswith('.onrender.com'):
            raise RuntimeError('Unexpected RENDER_EXTERNAL_HOSTNAME.')
        hosts.add(render_host)
    if not all(re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', host) for host in hosts):
        raise RuntimeError('Use hostnames without a scheme or path.')

    app = Flask(__name__, static_folder=None)
    app.config.update(SECRET_KEY=secret, TRUSTED_HOSTS=sorted(hosts), MAX_CONTENT_LENGTH=MAX_BODY,
        MAX_FORM_MEMORY_SIZE=8192, MAX_FORM_PARTS=8, SESSION_COOKIE_NAME='__Host-powder_session',
        SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
        SESSION_COOKIE_PATH='/', PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_SECONDS),
        SESSION_REFRESH_EACH_REQUEST=False)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1)
    password_hash = generate_password_hash(password)
    auth = hmac.new(secret.encode(), (username + '\0' + password).encode(), hashlib.sha256).hexdigest()
    del password
    attempts, attempt_lock, job_lock = deque(), Lock(), Lock()
    login_template = (ROOT / 'login.html').read_text(encoding='utf-8')
    demo = (ROOT / 'examples/scenarios_demo.json').read_bytes()

    def signed_in():
        return hmac.compare_digest(session.get('auth', ''), auth) and time.time() < session.get('expires', 0)

    def csrf_token():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        return session['csrf']

    def require_csrf():
        expected = session.get('csrf', '')
        if not expected or not hmac.compare_digest(expected.encode(), request.form.get('csrf', '').encode()):
            abort(403)

    @app.before_request
    def protect():
        if request.host.lower() not in hosts:
            abort(400)
        if request.endpoint == 'healthz':
            return None
        if not request.is_secure:
            if request.method in ('GET', 'HEAD'):
                return redirect('https://' + request.host + request.full_path.rstrip('?'), 308)
            abort(403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('Origin') != 'https://' + request.host:
                abort(403)
        if request.endpoint not in ('login', 'favicon') and not signed_in():
            if request.method not in ('GET', 'HEAD') or request.endpoint == 'example':
                return jsonify(error='Your session has ended. Sign in again in another tab and retry.',
                               login_url='/login'), 401
            return redirect('/login', 302)

    @app.after_request
    def headers(response):
        nonce = g.get('script_nonce')
        scripts = f"'nonce-{nonce}'" if nonce else "'none'"
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Robots-Tag': 'noindex, nofollow, noarchive', 'Referrer-Policy': 'same-origin',
            'Content-Security-Policy': f"default-src 'none'; script-src {scripts}; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        if request.is_secure:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.get('/healthz')
    def healthz():
        return jsonify(status='ok')

    @app.get('/favicon.ico')
    def favicon():
        return Response(status=204)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if signed_in():
            return redirect('/', 303)
        error, status = '', 200
        if request.method == 'POST':
            require_csrf()
            now = time.monotonic()
            with attempt_lock:
                while attempts and now - attempts[0] >= 60:
                    attempts.popleft()
                allowed = len(attempts) < 10
                if allowed:
                    attempts.append(now)
            if not allowed:
                error, status = 'Too many attempts. Please wait one minute and try again.', 429
            else:
                supplied = request.form.get('password', '')
                password_ok = len(supplied) <= 256 and check_password_hash(password_hash, supplied)
                user_ok = hmac.compare_digest(request.form.get('username', '').encode(), username.encode())
                if password_ok and user_ok:
                    session.clear()
                    session.permanent = True
                    session.update(auth=auth, expires=time.time() + SESSION_SECONDS)
                    csrf_token()
                    return redirect('/', 303)
                error, status = 'The username or password is incorrect.', 401
        response = Response(render_template_string(login_template, csrf=csrf_token(), error=error),
                            status=status, mimetype='text/html')
        if status == 429:
            response.headers['Retry-After'] = '60'
        return response

    @app.post('/logout')
    def logout():
        require_csrf()
        session.clear()
        return redirect('/login', 303)

    @app.get('/')
    @app.get('/single', strict_slashes=False)
    @app.get('/scenarios', strict_slashes=False)
    def workspace():
        nonce = g.script_nonce = secrets.token_urlsafe(24)
        button = render_template_string(
            '<form method="post" action="/logout"><input type="hidden" name="csrf" value="{{ csrf }}">'
            '<button class="subtle" type="submit">Sign out</button></form>', csrf=csrf_token())
        page = HTML.replace('__DEFAULT__', json.dumps(DEFAULT)).replace('__APP_VERSION__', VERSION)
        page = page.replace('<script>', f'<script nonce="{nonce}">', 1)
        page = page.replace('</div></header>', button + '</div></header>', 1)
        return Response(page, mimetype='text/html')

    @app.get('/examples/scenarios_demo.json')
    def example():
        return Response(demo, mimetype='application/json')

    @app.post('/<any(solve,classify,validate,import_excel):operation>')
    def api(operation):
        if request.mimetype != 'application/json':
            return jsonify(error='Send a JSON request.'), 415
        def invalid_constant(_):
            raise ValueError('Use finite numeric values.')
        expensive = operation in ('solve', 'import_excel')
        if expensive and not job_lock.acquire(blocking=False):
            return jsonify(error='Another calculation or workbook import is running. Try again when it finishes.'), 429
        try:
            body = json.loads(request.get_data(), parse_constant=invalid_constant)
            if not isinstance(body, dict):
                raise ValueError('Send a JSON object.')
            result = OPERATIONS[operation](body)
            return Response(json.dumps(result, allow_nan=False), mimetype='application/json')
        except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as error:
            return jsonify(error=str(error)), 400
        except (OSError, EOFError):
            return jsonify(error='Could not read the submitted file.'), 400
        except Exception:
            app.logger.error('Powder API operation failed: %s', operation)
            return jsonify(error='The operation could not be completed. Check your inputs and try again.'), 500
        finally:
            if expensive:
                job_lock.release()

    return app
