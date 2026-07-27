#!/usr/bin/env python3
"""
Regenera teams.js con datos reales de API-Football.

Hace el arranque en frío que la app no puede hacer sola:
  1. /leagues?country=Bolivia  → resuelve el league.id de la División Profesional
  2. /teams?league=&season=    → ids y nombres de los clubes de la temporada
  3. media.api-sports.io       → descarga los escudos y los embebe en base64

Los escudos se sirven sin key, así que el gasto son 2 requests del plan gratuito.

Uso:
    python3 fetch_assets.py              # temporada = año actual
    python3 fetch_assets.py --season 2025
    python3 fetch_assets.py --list       # solo lista las ligas y sale (1 request)
"""

import argparse
import base64
import datetime
import hashlib
import io
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

HERE = Path(__file__).parent
PAGE = HERE / 'fixture_liga_bolivia.html'
CRESTS_DIR = HERE / 'escudos'   # escudos/<slug>.png pisa lo que manda la API
API_BASE = 'https://v3.football.api-sports.io'
MEDIA_BASE = 'https://media.api-sports.io/football/teams'   # sin key, sin gastar cuota
CREST_PX = 96          # escudos que vienen de la API (nativos 150x150)
LOCAL_CREST_PX = 144   # escudos de escudos/, curados a mano

# La app es un HTML autocontenido: los assets viven en esta línea del <script>.
ASSETS_RE = re.compile(r'^const ASSETS = \{.*\};?$', re.M)


# ── acceso a la API ──────────────────────────────────────────────────────────

def read_key():
    cfg = HERE / 'config.js'
    if not cfg.exists():
        sys.exit("✗ No existe config.js. Copiá config.example.js y pegá tu key.")
    m = re.search(r"""API_KEY\s*=\s*['"]([^'"]+)['"]""", cfg.read_text())
    if not m or m.group(1) == 'TU_KEY_AQUI':
        sys.exit("✗ config.js no tiene una key válida.\n"
                 "  Registrate gratis en https://dashboard.api-football.com/register")
    return m.group(1)


def api_get(path, key, soft=False):
    """GET a la API. Valida el body, no el status code: API-Football devuelve
    200 OK con response vacío cuando no hay cobertura, y 200 OK con `errors`
    poblado cuando el plan no cubre lo que pediste.

    soft=True devuelve (response, errores) en vez de abortar.
    """
    req = urllib.request.Request(API_BASE + path, headers={'x-apisports-key': key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            left = resp.headers.get('x-ratelimit-requests-remaining')
    except urllib.error.HTTPError as e:
        sys.exit(f"✗ HTTP {e.code} en {path}: {e.read().decode(errors='replace')[:300]}")
    except Exception as e:
        sys.exit(f"✗ Fallo de red en {path}: {e}")

    if left is not None:
        print(f"    (quedan {left} requests hoy)")

    errors = data.get('errors') or {}
    if isinstance(errors, list):
        errors = {} if not errors else {'api': ' · '.join(map(str, errors))}
    if errors and not soft:
        sys.exit(f"✗ La API devolvió errores en {path}: {json.dumps(errors, ensure_ascii=False)}")
    return (data.get('response', []), errors) if soft else data.get('response', [])


def plan_season_range(errors):
    """El plan gratuito responde 'Free plans do not have access to this season,
    try from 2022 to 2024.' — sacamos ese rango para reintentar solo."""
    txt = ' '.join(str(v) for v in errors.values())
    m = re.search(r'from\s+(\d{4})\s+to\s+(\d{4})', txt)
    return (int(m.group(1)), int(m.group(2))) if m else None


# ── semilla de clubes ────────────────────────────────────────────────────────
# Nombres cortos y colores curados a mano: la API no los trae. El color se usa
# para el monograma cuando un club todavía no tiene escudo descargado.

SEED_TEAMS = {
    'alwaysready':        {'name': 'Always Ready',        'short': 'Always Ready',  'color': '#e2001a'},
    'bolivar':            {'name': 'Bolívar',             'short': 'Bolívar',       'color': '#4a9fe0'},
    'thestrongest':       {'name': 'The Strongest',       'short': 'Strongest',     'color': '#f5c518'},
    'orientepetrolero':   {'name': 'Oriente Petrolero',   'short': 'Oriente',       'color': '#00843d'},
    'blooming':           {'name': 'Blooming',            'short': 'Blooming',      'color': '#009fe3'},
    'guabira':            {'name': 'Guabirá',             'short': 'Guabirá',       'color': '#e2001a'},
    'nacionalpotosi':     {'name': 'Nacional Potosí',     'short': 'Nal. Potosí',   'color': '#5b2d8e'},
    'realtomayapo':       {'name': 'Real Tomayapo',       'short': 'Tomayapo',      'color': '#d4001a'},
    'sanantonio':         {'name': 'San Antonio B.B.',    'short': 'San Antonio',   'color': '#f39200'},
    'aurora':             {'name': 'Aurora',              'short': 'Aurora',        'color': '#7c1c2e'},
    'gvsanjose':          {'name': 'GV San José',         'short': 'San José',      'color': '#0047ab'},
    'universitariovinto': {'name': 'Universitario Vinto', 'short': 'Univ. Vinto',   'color': '#002d62'},
    'realoruro':          {'name': 'Real Oruro',          'short': 'Real Oruro',    'color': '#009fe3'},
    'independiente':      {'name': 'Independiente Petr.', 'short': 'Independiente', 'color': '#e2001a'},
    'wilstermann':        {'name': 'Jorge Wilstermann',   'short': 'Wilstermann',   'color': '#e2001a'},
    'royalpari':          {'name': 'Royal Pari',          'short': 'Royal Pari',    'color': '#1b3a8f'},
    'santacruz':          {'name': 'Real Santa Cruz',     'short': 'Real S. Cruz',  'color': '#0a8b4c'},
    # apiId fijo: ABB no está en el roster que devuelve el plan gratuito, pero su
    # escudo sí se puede traer por id desde media.api-sports.io.
    'abb':                {'name': 'ABB',                 'short': 'ABB',           'color': '#f2c200',
                           'apiId': 17743},
}

# Nombres de la API que la normalización no resuelve sola.
SEED_ALIASES = {
    'gualberto villarroel sj': 'gvsanjose',
    'gualberto villarroel san jose': 'gvsanjose',
    'gv san jose': 'gvsanjose',
    'san jose': 'gvsanjose',
    'jorge wilstermann': 'wilstermann',
    'universitario de vinto': 'universitariovinto',
    'san antonio bulo bulo': 'sanantonio',
    'independiente petrolero': 'independiente',
    'academia del balompie boliviano': 'abb',
    'santa cruz': 'santacruz',
}


# ── lectura y escritura del bloque ASSETS dentro del HTML ────────────────────

def normalize(name):
    """Minúsculas sin acentos ni puntuación, para comparar nombres de club."""
    txt = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    txt = re.sub(r'\b(club|deportivo|cd|fc|cf|sc)\b', ' ', txt.lower())
    return re.sub(r'[^a-z0-9]+', ' ', txt).strip()


def load_assets():
    """Devuelve el ASSETS actual del HTML (o {} si todavía está vacío)."""
    if not PAGE.exists():
        sys.exit(f"✗ No encuentro {PAGE.name}.")
    m = ASSETS_RE.search(PAGE.read_text())
    if not m:
        sys.exit(f"✗ No encontré la línea 'const ASSETS = …;' en {PAGE.name}.")
    raw = m.group(0)[len('const ASSETS = '):].rstrip(';')
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def save_assets(assets):
    """Reescribe solo esa línea del HTML; el resto del archivo queda intacto."""
    src = PAGE.read_text()
    line = 'const ASSETS = ' + json.dumps(assets, ensure_ascii=False, separators=(',', ':')) + ';'
    PAGE.write_text(ASSETS_RE.sub(lambda _m: line, src, count=1))


def match_slug(api_name, teams, aliases):
    norm = normalize(api_name)
    if norm in aliases:
        return aliases[norm]
    for slug, t in teams.items():
        if normalize(t['name']) == norm or slug == norm.replace(' ', ''):
            return slug
    # último intento: coincidencia por prefijo de palabra
    for slug, t in teams.items():
        if norm and normalize(t['name']).startswith(norm.split()[0]):
            return slug
    return None


def slugify(api_name):
    return re.sub(r'[^a-z0-9]', '', normalize(api_name)) or 'equipo'


# ── escudos ──────────────────────────────────────────────────────────────────

def encode_crest(raw, px=CREST_PX):
    """Reduce a px y devuelve el data URI."""
    if Image is not None:
        try:
            im = Image.open(io.BytesIO(raw)).convert('RGBA')
            im.thumbnail((px, px), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, 'PNG', optimize=True)
            raw = buf.getvalue()
        except Exception as e:
            print(f"    ⚠️  no pude optimizar el escudo ({e}), lo embebo tal cual")
    return 'data:image/png;base64,' + base64.b64encode(raw).decode()


def apply_local_crests(teams, api_md5, forzar=False):
    """Usa los escudos de escudos/<slug>.png, pero solo cuando hacen falta.

    API-Football tiene escudos equivocados: sirve el de Independiente Petrolero
    para Oriente Petrolero — los archivos de 3707 y 15702 son idénticos. El
    criterio para meter el escudo local es justamente esa duplicación: si el
    archivo que manda la API para ese club es byte por byte igual al de otro
    club, sigue roto. Si es único, damos por hecho que lo corrigieron y usamos
    el de la API, así el proyecto se beneficia de la corrección sin tocar nada.

    api_md5: {slug: md5 del PNG crudo que devolvió la API en esta corrida}
    forzar:  aplica los locales sin evaluar la condición
    """
    if not CRESTS_DIR.is_dir():
        return

    # Qué md5 de la API aparece en más de un club: esos son los datos malos.
    repetidos = {h for h in api_md5.values() if list(api_md5.values()).count(h) > 1}

    aplicados, descartados = [], []
    for png in sorted(CRESTS_DIR.glob('*.png')):
        slug = png.stem
        if slug not in teams:
            print(f"    ⚠️  {png.name}: no hay ningún club con el slug '{slug}', lo ignoro")
            continue

        mio = api_md5.get(slug)
        if forzar:
            motivo = 'forzado con --forzar-escudos'
        elif mio is None:
            motivo = 'la API no dio escudo para este club'
        elif mio in repetidos:
            gemelos = [teams[s]['name'] for s, h in api_md5.items() if h == mio and s != slug]
            motivo = f"la API sigue mandando el mismo archivo que {', '.join(gemelos)}"
        else:
            descartados.append(slug)
            continue

        # Más resolución que los de la API: son pocos, curados a mano, y algunos
        # traen detalle fino (estrellas, texto) que a 96 px se pierde.
        teams[slug]['crest'] = encode_crest(png.read_bytes(), px=LOCAL_CREST_PX)
        aplicados.append((teams[slug]['name'], png.name, motivo))

    if aplicados:
        print("\n→ Escudos locales aplicados …")
        for name, archivo, motivo in aplicados:
            print(f"    ✓ {name} ({archivo}) — {motivo}")

    if descartados:
        print("\n→ Escudos locales NO aplicados: la API ya manda uno propio 🎉")
        for slug in descartados:
            print(f"    • {teams[slug]['name']}: ahora se usa el de la API. "
                  f"Revisá que sea el correcto; si no, corré con --forzar-escudos.")


def fetch_crest(url):
    """Descarga el escudo. Devuelve (data_uri, md5_del_png_crudo) o (None, None).

    El md5 es del archivo tal como lo manda la API, antes de reducirlo: sirve
    para detectar que dos clubes reciben exactamente la misma imagen.
    """
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            raw = resp.read()
    except Exception as e:
        print(f"    ⚠️  no pude bajar {url}: {e}")
        return None, None
    return encode_crest(raw), hashlib.md5(raw).hexdigest()


# ── escritura ────────────────────────────────────────────────────────────────

def write_page(assets, league, teams, aliases):
    """Actualiza league / teams / aliases dentro de ASSETS, preservando logos."""
    assets['league'] = {
        'id': league['id'],
        'name': league['name'],
        'country': league['country'],
        'crestSeason': league['season'],   # temporada de la que salieron los escudos
        'updated': datetime.date.today().isoformat(),
        'verified': True,
    }
    # Forma uniforme: todo club tiene las cinco claves, aunque nunca haya jugado.
    assets['teams'] = {
        slug: {
            'name':  teams[slug]['name'],
            'short': teams[slug].get('short') or teams[slug]['name'],
            'color': teams[slug].get('color') or '#0057b7',
            'apiId': teams[slug].get('apiId'),
            'crest': teams[slug].get('crest'),
        }
        for slug in sorted(teams, key=lambda s: teams[s]['name'])
    }
    assets['aliases'] = dict(sorted(aliases.items()))
    save_assets(assets)

    kb = PAGE.stat().st_size // 1024
    con = sum(1 for t in teams.values() if t.get('crest'))
    print(f"\n✓ {PAGE.name} actualizado ({kb} KB · {len(teams)} equipos, {con} con escudo)")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--season', type=int, help='Temporada (default: año actual)')
    ap.add_argument('--league', type=int, help='Forzar un league.id en vez de autodetectarlo')
    ap.add_argument('--list', action='store_true', help='Solo listar las ligas de Bolivia')
    ap.add_argument('--forzar-escudos', action='store_true',
                    help='Aplica los escudos de escudos/ sin evaluar si la API ya los corrigió')
    args = ap.parse_args()

    key = read_key()

    print("→ Consultando /leagues?country=Bolivia …")
    leagues = api_get('/leagues?country=Bolivia', key)
    if not leagues:
        sys.exit("✗ La API devolvió 0 ligas para Bolivia. Revisá que la key esté activa.")

    print(f"\n  Ligas disponibles ({len(leagues)}):")
    for item in leagues:
        lg = item['league']
        seasons = [s['year'] for s in item.get('seasons', [])]
        rango = f"{min(seasons)}–{max(seasons)}" if seasons else "sin temporadas"
        print(f"    [{lg['id']:>5}] {lg['name']:<28} {lg['type']:<6} {rango}")

    if args.list:
        return

    # Elegimos la liga: la forzada, o la primera de tipo League cuyo nombre
    # suene a la división principal.
    if args.league:
        chosen = next((i for i in leagues if i['league']['id'] == args.league), None)
        if not chosen:
            sys.exit(f"✗ El league.id {args.league} no aparece entre las ligas de Bolivia.")
    else:
        def score(item):
            name = normalize(item['league']['name'])
            return (
                item['league']['type'] == 'League',
                'division profesional' in name or 'primera' in name,
            )
        chosen = max(leagues, key=score)

    lg = chosen['league']
    years = [s['year'] for s in chosen.get('seasons', [])]
    season = args.season or next((s['year'] for s in chosen.get('seasons', []) if s.get('current')),
                                 max(years) if years else datetime.date.today().year)
    print(f"\n→ Liga elegida: [{lg['id']}] {lg['name']} · temporada {season}")
    print(f"→ Consultando /teams?league={lg['id']}&season={season} …")
    api_teams, errors = api_get(f'/teams?league={lg["id"]}&season={season}', key, soft=True)

    # El plan gratuito solo cubre temporadas viejas para /teams. No importa: los
    # ids y escudos de los clubes no cambian, así que reintentamos con la última
    # temporada permitida y esos escudos sirven igual para la temporada en curso.
    if errors and not args.season:
        rango = plan_season_range(errors)
        if not rango:
            sys.exit(f"✗ La API devolvió errores: {json.dumps(errors, ensure_ascii=False)}")
        fallback = max(y for y in (years or [rango[1]]) if rango[0] <= y <= rango[1])
        print(f"\n  ℹ️  El plan gratuito no cubre {season} en /teams "
              f"(permitido: {rango[0]}–{rango[1]}).")
        print(f"  → Reintentando con la temporada {fallback} para obtener los escudos …")
        api_teams, errors = api_get(f'/teams?league={lg["id"]}&season={fallback}', key, soft=True)
        season = fallback   # es la temporada de la que salen realmente los escudos

    if errors:
        sys.exit(f"✗ La API devolvió errores: {json.dumps(errors, ensure_ascii=False)}")
    if not api_teams:
        sys.exit(f"✗ response vacío: no hay cobertura de equipos para league={lg['id']}.\n"
                 f"  Probá otra temporada con --season.")

    # La semilla aporta nombres cortos y colores; lo ya guardado en el HTML manda,
    # así que los escudos y retoques previos sobreviven a una regeneración.
    assets = load_assets()
    teams = {slug: dict(data) for slug, data in SEED_TEAMS.items()}
    for slug, data in (assets.get('teams') or {}).items():
        # Solo los valores presentes: un apiId/crest en null del HTML no debe
        # borrar el que trae la semilla.
        teams.setdefault(slug, {}).update({k: v for k, v in data.items() if v is not None})
    aliases = {**SEED_ALIASES, **(assets.get('aliases') or {})}
    seen = set()
    api_md5 = {}   # slug -> md5 del PNG crudo, para detectar imágenes repetidas

    print(f"\n→ Descargando {len(api_teams)} escudos …")
    for item in api_teams:
        t = item['team']
        slug = match_slug(t['name'], teams, aliases)
        if slug is None:
            slug = slugify(t['name'])
            teams[slug] = {'name': t['name'], 'short': t['name'], 'color': '#0057b7'}
            print(f"    + club nuevo: {t['name']} → {slug}")
        seen.add(slug)
        teams[slug]['apiId'] = t['id']
        if t.get('logo'):
            uri, md5 = fetch_crest(t['logo'])
            if uri:
                teams[slug]['crest'] = uri
                api_md5[slug] = md5
        aliases[normalize(t['name'])] = slug
        print(f"    ✓ {t['name']:<28} id={t['id']}")

    # Clubes que no están en el roster de esta temporada pero cuyo id conocemos:
    # el escudo se baja igual por id, sin consumir cuota.
    pendientes = [s for s in teams if s not in seen and teams[s].get('apiId')
                  and not teams[s].get('crest')]
    if pendientes:
        print(f"\n→ Escudos por id (fuera del roster) …")
        for slug in pendientes:
            uri, md5 = fetch_crest(f"{MEDIA_BASE}/{teams[slug]['apiId']}.png")
            if uri:
                teams[slug]['crest'] = uri
                api_md5[slug] = md5
                print(f"    ✓ {teams[slug]['name']:<28} id={teams[slug]['apiId']}")

    # Los escudos locales entran solo si la API sigue mandando uno repetido.
    apply_local_crests(teams, api_md5, forzar=args.forzar_escudos)

    faltantes = [s for s in teams if not teams[s].get('crest')]
    if faltantes:
        print(f"\n  ℹ️  Siguen sin escudo (se dibuja el monograma): {', '.join(faltantes)}")

    # Dos clubes con el mismo escudo suele ser un dato malo de la API.
    vistos = {}
    for slug, t in teams.items():
        if t.get('crest'):
            vistos.setdefault(t['crest'], []).append(teams[slug]['name'])
    repetidos = [v for v in vistos.values() if len(v) > 1]
    if repetidos:
        print("\n  ⚠️  Escudos repetidos entre clubes (revisá la carpeta escudos/):")
        for grupo in repetidos:
            print(f"      {' = '.join(grupo)}")

    write_page(
        assets,
        {'id': lg['id'], 'name': lg['name'], 'country': 'Bolivia', 'season': season},
        teams, aliases,
    )
    print("  Recargá el navegador para ver los escudos.")


if __name__ == '__main__':
    main()
