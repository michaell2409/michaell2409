#!/usr/bin/env python3
"""
Serpiente-juego para el README de GitHub.

- Empieza con 1 pixel y juega como una persona: elige a que dia ir, gira distinto cada vez y duda a ratos.
- Cada dia con contribuciones que se come le suma 1 pixel.
- Si hay pocos dias: se los come todos y al final choca consigo misma.
- Si hay muchos dias: se los come todos con 75% de probabilidad (y celebra);
  con 25% de probabilidad se muerde a mitad del camino, sin terminar.
- Despues el juego se reinicia con 1 pixel (el SVG se repite en bucle).
  La suerte se vuelve a tirar cada vez que el workflow regenera el SVG.

Uso en GitHub Actions:  python generate_snake.py
Pruebas locales:        python generate_snake.py --demo [--die | --win] [--days N]
"""
import json
import math
import os
import random
import sys
import urllib.request
from collections import deque

# ----------------------------- AJUSTES (puedes cambiarlos) -----------------------------
OUT_FILE = "dist/github-contribution-grid-snake.svg"

SNAKE_BODY = "#800080"   # cuerpo (el mismo morado de antes)
SNAKE_HEAD = "#800080"   # cabeza (igual que el cuerpo)
SNAKE_DEAD = "#f85149"   # color al chocar (rojo)
SNAKE_WIN = "#ffd700"    # color al terminar de comer todo (dorado)

MANY_DAYS = 10           # a partir de cuantos dias se considera "muchos"
DEATH_CHANCE = 0.25      # probabilidad de morir cuando hay muchos dias

STEP_SECONDS = 0.12      # segundos por movimiento
MAX_SECONDS = 30         # si el recorrido es largo, acelera para no pasar de esto
HOLD_START = 8           # pasos mirando el pixel inicial
HOLD_END = 10            # pasos con el tablero vacio antes de reiniciar

CELL, PITCH, PAD = 12, 15, 15   # tamano de celda, paso entre celdas, margen

# colores de los dias (nivel 1..4) segun el tema claro/oscuro
LIGHT = {"empty": "#ebedf0", 1: "#9be9a8", 2: "#40c463", 3: "#30a14e", 4: "#216e39"}
DARK = {"empty": "#161b22", 1: "#0e4429", 2: "#006d32", 3: "#26a641", 4: "#39d353"}
# ---------------------------------------------------------------------------------------

ROWS = 7
DIRS = [(1, 0), (0, 1), (-1, 0), (0, -1)]  # sentido horario en pantalla (y hacia abajo)


# ------------------------------ datos de contribuciones ------------------------------
def fetch_calendar(user, token):
    query = (
        "query($login:String!){user(login:$login){contributionsCollection{"
        "contributionCalendar{weeks{contributionDays{contributionCount weekday}}}}}}"
    )
    body = json.dumps({"query": query, "variables": {"login": user}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "snake-game-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if "errors" in data or not data.get("data", {}).get("user"):
        raise SystemExit(f"Error al leer las contribuciones: {data.get('errors')}")
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    cells = {}
    for c, week in enumerate(weeks):
        for day in week["contributionDays"]:
            cells[(c, day["weekday"])] = day["contributionCount"]
    return cells


def demo_calendar(n_food=12, seed=7):
    rnd = random.Random(seed)
    cells = {(c, r): 0 for c in range(53) for r in range(ROWS)}
    for k in rnd.sample(list(cells), n_food):
        cells[k] = rnd.randint(1, 9)
    return cells


# ------------------------------------ el juego ------------------------------------
def rot(d, turn):
    return DIRS[(DIRS.index(d) + turn) % 4]


def sweep_path(cols):
    """Recorrido en zigzag por columnas: pasa por todas las celdas sin cruzarse nunca."""
    path = []
    for c in range(cols):
        rows = range(ROWS) if c % 2 == 0 else range(ROWS - 1, -1, -1)
        path.extend((c, r) for r in rows)
    return path


def dist_map(source, blocked, field):
    """Distancia (en pasos) desde source a cada celda libre."""
    dist = {source: 0}
    q = deque([source])
    while q:
        cur = q.popleft()
        for d in DIRS:
            n = (cur[0] + d[0], cur[1] + d[1])
            if n in field and n not in dist and n not in blocked:
                dist[n] = dist[cur] + 1
                q.append(n)
    return dist


def can_reach_tail(body, field):
    """Seguridad: despues de moverse la cabeza aun puede llegar a su cola (no se encierra)."""
    head, tail = body[0], body[-1]
    if head == tail:
        return True
    blocked = set(body[1:-1])
    seen, q = {head}, deque([head])
    while q:
        cur = q.popleft()
        for d in DIRS:
            n = (cur[0] + d[0], cur[1] + d[1])
            if n == tail:
                return True
            if n in field and n not in seen and n not in blocked:
                seen.add(n)
                q.append(n)
    return False


def route_human(start, foods, field, rnd, max_steps=2500):
    """Juega "como una persona": elige a que dia ir (no siempre el mas cercano), gira cuando
    quiere, a veces da un rodeo, pero nunca se encierra. Devuelve (ruta, se_comio_todo)."""
    foods = set(foods)
    body, route = [start], []
    direction = (1, 0)
    pending = 0
    if start in foods:
        foods.discard(start)
        pending = 1
    target = None

    while foods and len(route) < max_steps:
        head, tail = body[0], body[-1]
        mid = set(body[1:-1])

        # elegir objetivo: uno de los 3 mas cercanos, con algo de capricho
        if target is None or target not in foods or rnd.random() < 0.04:
            dist_h = dist_map(head, mid, field)
            reach = sorted((dist_h[f], f) for f in foods if f in dist_h)
            if reach:
                pick = rnd.choices(range(min(3, len(reach))), weights=[60, 25, 15][: min(3, len(reach))])[0]
                target = reach[pick][1]
            else:
                target = None

        # movimientos posibles (sin dar media vuelta ni pisar el cuerpo)
        neck = body[1] if len(body) > 1 else None
        cands = []
        for d in DIRS:
            n = (head[0] + d[0], head[1] + d[1])
            if n not in field or n == neck or n in mid:
                continue
            if n == tail and len(body) > 1 and n in foods:
                continue
            grow = n in foods
            nb = [n] + (body if (grow or pending) else body[:-1])
            if can_reach_tail(nb, field):
                cands.append((n, d))
        if not cands:
            return route, False

        choice = None
        if target is not None:
            dt = dist_map(target, mid, field)
            here = dt.get(head, 10**9)
            better = [(n, d) for n, d in cands if dt.get(n, 10**9) < here]
            if better:
                straight = [c for c in better if c[1] == direction]
                if straight and rnd.random() < 0.55:
                    choice = straight[0]
                else:
                    choice = rnd.choice(better)
                # a veces se desvia un poco antes de ir al dia (como dudando)
                if rnd.random() < 0.06:
                    others = [c for c in cands if c not in better and c[1] != direction]
                    if others:
                        choice = rnd.choice(others)
        if choice is None:
            choice = rnd.choice(cands)

        n, d = choice
        direction = d
        body.insert(0, n)
        if n in foods:
            foods.discard(n)
        elif pending:
            pending -= 1
        else:
            body.pop()
        route.append(n)

    return route, not foods


def plan_crash(body, field):
    """Devuelve los pasos (cuerpos) hasta que la serpiente choca consigo misma."""
    length = len(body)
    if length < 2:
        return []
    head = body[0]

    # 1) si tiene su propio cuerpo al lado, gira y se lo come (choque directo)
    if length >= 4:
        for d in DIRS:
            n = (head[0] + d[0], head[1] + d[1])
            if n in body[2:-1]:
                return [(n,) + tuple(body[:-1])]

    cur = (head[0] - body[1][0], head[1] - body[1][1])
    back = (-cur[0], -cur[1])

    # 2) se enrolla en un cuadrito y se muerde
    if length >= 5:
        for d0 in [d for d in DIRS if d != back]:
            for turn in (1, -1):
                sim, d, frames = list(body), d0, []
                for _ in range(8):
                    n = (sim[0][0] + d[0], sim[0][1] + d[1])
                    if n not in field:
                        break
                    hit = n in sim[:-1]
                    sim = [n] + sim[:-1]
                    frames.append(tuple(sim))
                    if hit:
                        return frames
                    d = rot(d, turn)

    # 3) da media vuelta y choca con su cuello
    return [(body[1],) + tuple(body[:-1])]


def simulate(cells, force=None, rnd=None):
    """force: None (tira la suerte), 'die' o 'win'."""
    rnd = rnd or random.Random()
    cols = max(k[0] for k in cells) + 1
    field = {(c, r) for c in range(cols) for r in range(ROWS)}
    foods = {k for k, v in cells.items() if v > 0}
    path = sweep_path(cols)

    many = len(foods) >= MANY_DAYS
    if many:
        dies = (rnd.random() < DEATH_CHANCE) if force is None else (force == "die")
    else:
        dies = True  # con pocos dias: se los come todos y choca al final

    # cuantos dias alcanza a comer antes de morir (solo si muere con muchos dias)
    stop_after = len(foods)
    if many and dies:
        lo = max(5, math.ceil(0.3 * len(foods)))
        hi = max(lo, math.ceil(0.85 * len(foods)))
        stop_after = rnd.randint(lo, min(hi, len(foods)))

    route = None
    for _ in range(12):                              # varios intentos "humanos"
        start = (0, rnd.randrange(ROWS))
        route, complete = route_human(start, foods, field, rnd)
        if complete and len(route) <= 1200:
            break
        route = None
    if route is None:
        if many:
            start, route = path[0], path[1:]         # respaldo: barrido, se come TODOS los dias
        else:
            start = (0, rnd.randrange(ROWS))
            route, _ = route_human(start, foods, field, rnd)

    body = [start]
    frames = [(tuple(body), "play")] * HOLD_START
    eaten_at = {}
    pending = 0
    foods = set(foods)
    if start in foods:                              # el pixel inicial ya era un dia
        eaten_at[start] = 0
        foods.discard(start)
        pending = 1
    for cell in route:
        if len(eaten_at) >= stop_after:
            break
        body.insert(0, cell)
        if cell in foods:
            foods.discard(cell)
            eaten_at[cell] = len(frames)
        elif pending:
            pending -= 1
            frames.append((tuple(body), "play"))
            continue
        else:
            body.pop()
        frames.append((tuple(body), "play"))
        if rnd.random() < 0.035:                     # duda un instante, como una persona
            frames.append((tuple(body), "play"))

    if dies:
        crash = plan_crash(body, field)
        frames.extend((b, "play") for b in crash)
        if crash:
            last = crash[-1]
            for mode in ("dead", "alive", "dead", "alive", "dead", "dead", "dead"):
                frames.append((last, mode))
        else:
            frames.extend([(tuple(body), "play")] * 6)
    else:
        last = tuple(body)
        for mode in ("win", "alive", "win", "alive", "win", "win", "win"):
            frames.append((last, mode))

    frames.extend([((), "gone")] * HOLD_END)
    return frames, eaten_at, dies


# ------------------------------------- el SVG -------------------------------------
def compress(states, total):
    """Convierte una lista de estados por frame en values/keyTimes (modo discreto)."""
    vals, times, prev = [], [], object()
    for i, s in enumerate(states):
        if s != prev:
            vals.append(s)
            times.append(i / total)
            prev = s
    vals.append(vals[-1])
    times.append(1.0)
    return vals, times


def anim(attr, vals, times, dur):
    v = ";".join(str(x) for x in vals)
    t = ";".join(f"{x:.5f}" for x in times)
    return (
        f'<animate attributeName="{attr}" values="{v}" keyTimes="{t}" '
        f'dur="{dur:.2f}s" calcMode="discrete" repeatCount="indefinite"/>'
    )


def build_svg(cells, frames, eaten_at):
    cols = max(k[0] for k in cells) + 1
    width = cols * PITCH + PAD * 2
    height = ROWS * PITCH + PAD * 2

    nonzero = [v for v in cells.values() if v > 0]
    vmax = max(nonzero) if nonzero else 1

    def level(v):
        return 0 if v <= 0 else max(1, min(4, math.ceil(4 * v / vmax)))

    def pos(c, r):
        return PAD + c * PITCH, PAD + r * PITCH

    n = len(frames)
    dt = min(STEP_SECONDS, MAX_SECONDS / n)
    dur = n * dt

    css = []
    for name, pal in (("", LIGHT), ("@media (prefers-color-scheme: dark){", DARK)):
        rules = [f".e{{fill:{pal['empty']}}}"] + [f".l{i}{{fill:{pal[i]}}}" for i in range(1, 5)]
        css.append("".join(rules) if not name else name + "".join(rules) + "}")

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        f"<style>{''.join(css)}</style>",
    ]

    # tablero
    for (c, r), v in sorted(cells.items()):
        x, y = pos(c, r)
        cls = "e" if level(v) == 0 else f"l{level(v)}"
        out.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.5"/>')

    # dias comidos: se "borran" cuando pasa la cabeza y reaparecen al reiniciar
    for cell, i in sorted(eaten_at.items()):
        x, y = pos(*cell)
        vals, times = ["0", "1", "1"], [0.0, i / n, 1.0]
        out.append(
            f'<rect class="e" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.5" opacity="0">'
            f'{anim("opacity", vals, times, dur)}</rect>'
        )

    # estados por celda y frame
    color = {"body": SNAKE_BODY, "dead": SNAKE_DEAD, "win": SNAKE_WIN}
    body_state, head_state = {}, {}
    for i, (body, mode) in enumerate(frames):
        st = "dead" if mode == "dead" else "win" if mode == "win" else "body"
        for cell in set(body):
            if cell not in body_state:
                body_state[cell] = ["off"] * n
            body_state[cell][i] = st
        if body and mode in ("play", "alive"):
            if body[0] not in head_state:
                head_state[body[0]] = ["off"] * n
            head_state[body[0]][i] = "head"

    for cell, states in sorted(body_state.items()):
        x, y = pos(*cell)
        vals, times = compress(states, n)
        colors = [color.get(s, SNAKE_BODY) for s in vals]
        ops = ["0" if s == "off" else "1" for s in vals]
        out.append(
            f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.5" fill="{SNAKE_BODY}" opacity="0">'
            f'{anim("opacity", ops, times, dur)}{anim("fill", colors, times, dur)}</rect>'
        )
    for cell, states in sorted(head_state.items()):
        x, y = pos(*cell)
        vals, times = compress(states, n)
        ops = ["0" if s == "off" else "1" for s in vals]
        out.append(
            f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.5" fill="{SNAKE_HEAD}" opacity="0">'
            f'{anim("opacity", ops, times, dur)}</rect>'
        )

    out.append("</svg>")
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    force = "die" if "--die" in args else "win" if "--win" in args else None
    if "--demo" in args:
        days = int(args[args.index("--days") + 1]) if "--days" in args else 40
        cells = demo_calendar(days)
    else:
        user = os.environ.get("SNAKE_USER") or os.environ.get("GITHUB_ACTOR")
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not user or not token:
            raise SystemExit("Faltan SNAKE_USER y GH_TOKEN (o usa --demo para probar).")
        cells = fetch_calendar(user, token)

    frames, eaten_at, dies = simulate(cells, force)
    svg = build_svg(cells, frames, eaten_at)

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(svg)
    total = sum(1 for v in cells.values() if v > 0)
    print(
        f"OK: {OUT_FILE} ({len(svg)//1024} KB) | dias: {total} | comidos: {len(eaten_at)} | "
        f"resultado: {'muere' if dies else 'gana'} | pasos: {len(frames)}"
    )


if __name__ == "__main__":
    main()
