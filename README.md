# Generador de Fixture · Liga de Bolivia

Herramienta web para generar imágenes del fixture de la **División Profesional boliviana**
listas para compartir por WhatsApp.

## ¿Qué hace?

- Carga automáticamente los partidos del día (o del día siguiente) desde la API
- Muestra hora en **horario boliviano (UTC-4)**
- Detecta el torneo (Apertura / Clausura / Play-offs) y la fecha automáticamente
- Escudos reales de los 18 clubes embebidos en el archivo
- Genera una imagen PNG de 1080px lista para enviar a grupos de WhatsApp

## Uso

```bash
python3 proxy.py            # levanta el servidor en el puerto 8888
```

Y abrí <http://localhost:8888/fixture_liga_bolivia.html>.

El proxy es necesario: evita el bloqueo CORS del navegador y mantiene la key
fuera del HTML que se comparte.

## API utilizada

Los datos se obtienen desde **[API-Football](https://www.api-football.com/)** (API-Sports),
directo a `v3.football.api-sports.io` con el header `x-apisports-key`.

Pegá tu key en `config.js` (copiá `config.example.js`). El registro gratuito da
**100 requests por día**: <https://dashboard.api-football.com/register>.

### El límite del plan gratuito, y cómo lo esquivamos

El plan Free **rechaza el parámetro `season` para la temporada en curso**:

```
/fixtures?league=344&season=2026
→ {"errors":{"plan":"Free plans do not have access to this season, try from 2022 to 2024."}}
```

Pero **no limita la consulta por fecha**. Por eso la app pide:

```
/fixtures?date=2026-07-27&timezone=America/La_Paz
```

…que devuelve la jornada mundial completa de ese día boliviano, y filtra
`league.id === 344` del lado del cliente. Cuesta **1 request por día consultado**
(2 si elegís "Ambas"). El contador de requests restantes se muestra en el panel.

### Dos gotchas de esta API

1. **Cuando falta cobertura devuelve `200 OK` con `response: []`, no un error.**
   Por eso el manejo de errores revisa el *body*, no el status code. La app
   distingue tres casos: `errors` poblado (key inválida o cuota agotada),
   `response` vacío (sin data ese día) y fallo de red.
2. **`errors` viene como `[]` cuando no hay errores y como objeto cuando sí.**
   `apiErrors()` normaliza ambas formas.

## Archivos

| Archivo | Rol |
|---|---|
| `fixture_liga_bolivia.html` | La app completa: UI, tarjeta, lógica y assets embebidos |
| `proxy.py` | Sirve los estáticos y reenvía `/api/*` agregando la key |
| `fetch_assets.py` | Resuelve el `league.id` y descarga los escudos (correr una vez) |
| `config.js` | Tu key — está en `.gitignore`, no se sube |

Como en el Mundial, **la app es un solo HTML autocontenido**: los escudos y los
logos viven en la línea `const ASSETS = {…};` dentro del `<script>`. Así el
archivo se puede mover o compartir solo y sigue funcionando (salvo la carga
automática, que necesita el proxy).

## Actualizar escudos o equipos

```bash
python3 fetch_assets.py            # temporada actual, con fallback automático
python3 fetch_assets.py --list     # solo lista las ligas de Bolivia (1 request)
python3 fetch_assets.py --season 2023
```

Reescribe **solo** la línea `const ASSETS` del HTML; el resto queda intacto.
Los escudos salen de la temporada 2024 (la última que permite el plan gratuito
en `/teams`), pero los ids y escudos de los clubes no cambian entre temporadas.

### Clubes fuera del roster

`media.api-sports.io` sirve los escudos **sin key y sin consumir cuota**, así que
un club que no aparece en el roster de la temporada se resuelve igual por id.
Hay dos caminos:

- **Embebido**: poné el `apiId` en `SEED_TEAMS` dentro de `fetch_assets.py` y
  corré el script — baja el escudo aunque el club no esté en el roster. Así se
  resolvió **ABB** (id `17743`), que no jugó 2024.
- **En vivo**: si un club desconocido aparece en un fixture, la app pide su
  escudo a `/crest/<id>.png` y el proxy lo trae al vuelo. Corré
  `fetch_assets.py` después para embeberlo de forma permanente.

Para encontrar el id de un club: `/teams?country=Bolivia` lista los 114 equipos
bolivianos que conoce la API (1 request).

### Escudos equivocados en la API: la carpeta `escudos/`

API-Football tiene errores en algunos escudos. El caso confirmado:
`media.api-sports.io/football/teams/3707.png` (Oriente Petrolero) y
`.../15702.png` (Independiente Petrolero) devuelven **el mismo archivo**, byte
por byte. La metadata apunta bien, pero la imagen de Oriente es la de
Independiente.

Para eso existe `escudos/`. Poné ahí `escudos/<slug>.png` y corré el script:

```bash
cp mi_escudo.png "escudos/orientepetrolero.png"
python3 fetch_assets.py
```

**El escudo local no se aplica siempre: solo cuando hace falta.** El criterio es
la duplicación misma. Si el archivo que la API manda para ese club es byte por
byte igual al de otro club, sigue roto y entra el local. Si es único, se asume
que lo corrigieron y se usa el de la API — así el proyecto se beneficia de la
corrección sin que haya que tocar nada, y el script te avisa que el archivo local
quedó sin usar.

| Situación | Qué usa |
|---|---|
| La API manda el mismo archivo que otro club | el local |
| La API manda un archivo propio y único | **el de la API** (avisa) |
| La API no manda escudo para ese club | el local |
| `python3 fetch_assets.py --forzar-escudos` | el local, siempre |

`--forzar-escudos` es la salida para el caso en que la API mande un escudo único
pero igualmente equivocado, que la detección por duplicado no puede ver.

Los escudos locales se embeben a 144 px en vez de los 96 px de la API, porque
suelen traer detalle fino (estrellas, texto) que a 96 px se pierde.

