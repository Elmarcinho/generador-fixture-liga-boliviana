#!/usr/bin/env python3
"""
Proxy local para fixture_liga_bolivia.html

Sirve los archivos estáticos en / y reenvía /api/* a v3.football.api-sports.io
agregando el header x-apisports-key. Existe por dos motivos: evita el bloqueo
CORS del navegador y mantiene la key fuera del HTML que se comparte.

Uso: python3 proxy.py [puerto]   (default: 8888)
"""

import json
import re
import sys
import urllib.error
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

API_BASE = 'https://v3.football.api-sports.io'
MEDIA_BASE = 'https://media.api-sports.io'   # escudos, sin key y sin consumir cuota

# Cabeceras de cuota que devuelve API-Sports; las reenviamos para que la app
# pueda mostrar cuántos requests quedan del plan gratuito (100/día).
QUOTA_HEADERS = (
    'x-ratelimit-requests-limit',
    'x-ratelimit-requests-remaining',
    'X-RateLimit-Limit',
    'X-RateLimit-Remaining',
)


def read_key():
    """Lee API_KEY desde config.js (mismo archivo que consume el navegador)."""
    cfg = Path(__file__).with_name('config.js')
    if not cfg.exists():
        return None
    m = re.search(r"""API_KEY\s*=\s*['"]([^'"]+)['"]""", cfg.read_text())
    return m.group(1) if m else None


class ProxyHandler(SimpleHTTPRequestHandler):

    def do_GET(self):
        if self.path.startswith('/api/'):
            self._proxy_api()
        elif self.path.startswith('/crest/'):
            self._proxy_crest()
        else:
            super().do_GET()

    def _proxy_crest(self):
        """Escudo de un club que apareció en vivo y no está embebido en teams.js.
        Lo servimos desde el mismo origen para que html2canvas pueda dibujarlo.
        media.api-sports.io no pide key, así que no gasta cuota."""
        m = re.fullmatch(r'/crest/(\d+)\.png', self.path)
        if not m:
            return self._json(404, {'errors': {'crest': 'ruta inválida'}})
        url = f'{MEDIA_BASE}/football/teams/{m.group(1)}.png'
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                body = resp.read()
        except Exception as e:
            return self._json(502, {'errors': {'crest': str(e)}})
        self.send_response(200)
        self.send_header('Content-Type', 'image/png')
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.end_headers()
        self.wfile.write(body)

    def _proxy_api(self):
        key = read_key()
        if not key or key == 'TU_KEY_AQUI':
            return self._json(401, {
                'errors': {'token': 'Falta la key en config.js. '
                                    'Registrate gratis en dashboard.api-football.com'},
                'response': [],
            })

        # /api/fixtures?... → https://v3.football.api-sports.io/fixtures?...
        upstream = API_BASE + self.path[len('/api'):]
        req = urllib.request.Request(upstream, headers={'x-apisports-key': key})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read()
                quota = {h: resp.headers[h] for h in QUOTA_HEADERS if resp.headers.get(h)}
            self._raw(200, body, quota)
        except urllib.error.HTTPError as e:
            self._raw(e.code, e.read(), {})
        except Exception as e:
            self._json(502, {'errors': {'proxy': str(e)}, 'response': []})

    def _json(self, code, payload):
        self._raw(code, json.dumps(payload).encode(), {})

    def _raw(self, code, body, extra_headers):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        for name, value in extra_headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} — {fmt % args}")


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    if not read_key():
        print("⚠️  No encontré API_KEY en config.js — la app va a mostrar el error de key.\n")
    server = HTTPServer(('0.0.0.0', port), ProxyHandler)
    print(f"Proxy corriendo en http://localhost:{port}/")
    print(f"Abre: http://localhost:{port}/fixture_liga_bolivia.html")
    print("Ctrl+C para detener.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDetenido.")
