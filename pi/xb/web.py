"""The settings page, served by the Pi on port 8080.

Open http://xbridge.local:8080 (or the Pi's IP address) on a phone or PC on
the same network.
"""

import copy
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as cfgmod
from .keycodes import ALL_CODES

PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'page.html')


class Shared:
    """What the settings page and the main loop share."""

    def __init__(self, cfg, path):
        self.lock = threading.Lock()
        self.config = copy.deepcopy(cfg)
        self.path = path
        self.pending = None
        self.status = {}
        self.version = 1          # goes up every time settings change

    def get_config(self):
        with self.lock:
            return copy.deepcopy(self.config)

    def set_config_from_web(self, cfg):
        """Check, save to disk and queue for the main loop. Raises ConfigError."""
        clean = cfgmod.validate(cfg)
        with self.lock:
            cfgmod.save(self.path, clean)
            self.config = copy.deepcopy(clean)
            self.pending = copy.deepcopy(clean)
            self.version += 1
        return clean

    def take_pending(self):
        with self.lock:
            p, self.pending = self.pending, None
            return p

    def set_config_from_loop(self, cfg):
        with self.lock:
            cfgmod.save(self.path, cfg)
            self.config = copy.deepcopy(cfg)
            self.version += 1


def _key_list():
    keyboard = [k for k in ALL_CODES if not k.startswith(('MOUSE_', 'WHEEL_'))]
    mouse = [k for k in ALL_CODES if k.startswith(('MOUSE_', 'WHEEL_'))]
    return mouse + keyboard


def make_handler(shared):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'XBridge/1'

        def log_message(self, fmt, *args):   # keep the system log quiet
            pass

        def _send(self, code, body, ctype='application/json'):
            data = body if isinstance(body, bytes) else body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj))

        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path in ('/', '/index.html'):
                try:
                    with open(PAGE_PATH, 'rb') as f:
                        self._send(200, f.read(), 'text/html')
                except OSError:
                    self._send(500, 'Settings page file is missing.', 'text/plain')
            elif path == '/api/status':
                self._json(200, shared.status)
            elif path == '/api/config':
                with shared.lock:
                    body = {'version': shared.version, 'config': copy.deepcopy(shared.config)}
                self._json(200, body)
            elif path == '/api/meta':
                self._json(200, {
                    'keys': _key_list(),
                    'actions': list(cfgmod.ALL_ACTIONS),
                    'action_labels': cfgmod.ACTION_LABELS,
                    'button_actions': list(cfgmod.BUTTON_ACTIONS),
                    'profiles': list(cfgmod.PROFILES),
                    'limits': cfgmod.LIMITS,
                    'defaults': cfgmod.defaults(),
                })
            else:
                self._send(404, 'Not found', 'text/plain')

        def do_POST(self):
            path = self.path.split('?', 1)[0]
            if path != '/api/config':
                self._send(404, 'Not found', 'text/plain')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 256 * 1024:
                    raise cfgmod.ConfigError('No settings were sent.')
                cfg = json.loads(self.rfile.read(length).decode('utf-8'))
                clean = shared.set_config_from_web(cfg)
                self._json(200, {'ok': True, 'config': clean, 'version': shared.version})
            except (ValueError, cfgmod.ConfigError) as exc:
                self._json(400, {'ok': False, 'error': str(exc)})
            except OSError as exc:
                self._json(500, {'ok': False, 'error': 'Could not save settings: %s' % exc})

    return Handler


class WebServer:
    def __init__(self, shared, port=8080, host='0.0.0.0'):
        self.httpd = ThreadingHTTPServer((host, port), make_handler(shared))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, name='web', daemon=True)

    @property
    def port(self):
        return self.httpd.server_address[1]

    def start(self):
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
