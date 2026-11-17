#!/usr/bin/env python3
"""
Serpiente-juego para el README de GitHub.

- Empieza con 1 pixel y juega como una persona: elige a que dia ir, gira distinto cada vez y duda a ratos.
- Cada dia con contribuciones que se come le suma 1 pixel.
- Se los come todos con 75% de probabilidad (y celebra);
  con 25% se muerde a mitad del camino, sin terminar. (Sin ningun dia: da un paseo y choca.)
- Despues el juego se reinicia con 1 pixel (el SVG se repite en bucle).
  La suerte se vuelve a tirar cada vez que el workflow regenera el SVG.

Uso en GitHub Actions:  python generate_snake.py
Pruebas locales:        python generate_snake.py --demo [--die | --win] [--days N]
"""
import datetime
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

MANY_DAYS = 1            # desde cuantos dias se aplica la regla del 75% gana / 25% muere
DEATH_CHANCE = 0.25      # probabilidad de morir cuando hay muchos dias

STEP_SECONDS = 0.18      # segundos por movimiento (siempre igual)
MAX_SECONDS = 30         # (ya no se usa: la velocidad es siempre la misma)
HOLD_START = 8           # pasos mirando el pixel inicial
HOLD_END = 10            # pasos con el tablero vacio antes de reiniciar

CELL, PITCH, PAD = 12, 15, 15   # tamano de celda, paso entre celdas, margen

# colores de los dias (nivel 1..4) segun el tema claro/oscuro
LIGHT = {"empty": "#ebedf0", 1: "#854d0e", 2: "#c2410c", 3: "#b91c1c", 4: "#7f1d1d"}
DARK = {"empty": "#161b22", 1: "#854d0e", 2: "#c2410c", 3: "#b91c1c", 4: "#7f1d1d"}

# dias especiales: (mes, dia) -> dibujo. Sin contribucion se ve en gris; con contribucion, a color.
# Para quitar uno, borra su linea. Dibujos disponibles: calabaza, regalo, fuegos, pastel
SPECIAL_DAYS = {
    (7, 25): "gato", (8, 7): "florero", (12, 7): "vela",
    (10, 31): "calabaza",   # Halloween
    (12, 24): "regalo",     # Nochebuena
    (12, 31): "fuegos",     # Fin de ano
    (9, 24): "pastel",      # cumpleanos
}

# grises de los dibujos sin contribucion (de claro a oscuro) en tema claro / oscuro
GRAY_LIGHT = {1: "#c3c8cf", 2: "#a6adb6", 3: "#868f99"}
GRAY_DARK = {1: "#444c56", 2: "#59636f", 3: "#78828f"}
# ---------------------------------------------------------------------------------------

ROWS = 7
DIRS = [(1, 0), (0, 1), (-1, 0), (0, -1)]  # sentido horario en pantalla (y hacia abajo)


# ------------------------------ datos de contribuciones ------------------------------
def fetch_calendar(user, token):
    query = (
        "query($login:String!){user(login:$login){contributionsCollection{"
        "contributionCalendar{weeks{contributionDays{contributionCount weekday date}}}}}}"
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
    cells, dates = {}, {}
    for c, week in enumerate(weeks):
        for day in week["contributionDays"]:
            cells[(c, day["weekday"])] = day["contributionCount"]
            dates[(c, day["weekday"])] = datetime.date.fromisoformat(day["date"])
    return cells, dates


def demo_calendar(n_food=12, seed=7):
    rnd = random.Random(seed)
    cells = {(c, r): 0 for c in range(53) for r in range(ROWS)}
    for k in rnd.sample(list(cells), n_food):
        cells[k] = rnd.randint(1, 9)
    return cells


def demo_dates(cells, today=None):
    """Fechas de mentira para probar: la ultima columna termina hoy (como el calendario real)."""
    today = today or datetime.date.today()
    since_sunday = (today.weekday() + 1) % 7
    first_sunday = today - datetime.timedelta(days=7 * 52 + since_sunday)
    dates = {(c, r): first_sunday + datetime.timedelta(days=7 * c + r) for (c, r) in list(cells)}
    for k in [k for k, d in dates.items() if d > today]:   # no hay dias en el futuro
        del dates[k]
        del cells[k]
    return dates


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
        total = len(foods)
        lo = min(total, max(5, math.ceil(0.3 * total)))
        hi = min(total, max(lo, math.ceil(0.85 * total)))
        stop_after = rnd.randint(lo, hi)

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


# ------------------------------- dibujos de dias especiales -------------------------------
# Cada dibujo cabe en una caja de 12x12. Cada color es (color_real, nivel_de_gris 1..3):
# con contribucion se usa el color real; sin contribucion, el gris del nivel indicado.
def _shape(colored, tag, attrs, fill=None, stroke=None, sw=1.0):
    a, cls = dict(attrs), []
    if fill is None:
        a["fill"] = "none"
    elif colored:
        a["fill"] = fill[0]
    else:
        cls.append(f"g{fill[1]}")
    if stroke is not None:
        a["stroke-width"] = sw
        a["stroke-linecap"] = "round"
        a["stroke-linejoin"] = "round"
        if colored:
            a["stroke"] = stroke[0]
        else:
            cls.append(f"t{stroke[1]}")
    if cls:
        a["class"] = " ".join(cls)
    return f"<{tag} " + " ".join(f'{k}="{v}"' for k, v in a.items()) + "/>"


def icon_calabaza(colored):
    body, rib, stem, face = ("#f58a1f", 1), ("#c85f00", 2), ("#3f8f2f", 3), ("#3a1c00", 3)
    s = lambda *a, **k: _shape(colored, *a, **k)
    return "".join([
        s("rect", {"x": 5.1, "y": 0.5, "width": 1.8, "height": 2.6, "rx": 0.7}, fill=stem),
        s("ellipse", {"cx": 6, "cy": 7.2, "rx": 5.5, "ry": 4.4}, fill=body),
        s("path", {"d": "M6 2.9 C4.1 4.3 4.1 10.3 6 11.5"}, stroke=rib, sw=0.6),
        s("path", {"d": "M6 2.9 C7.9 4.3 7.9 10.3 6 11.5"}, stroke=rib, sw=0.6),
        s("path", {"d": "M3.3 7 L5 7 L4.15 5.2 Z"}, fill=face),
        s("path", {"d": "M7 7 L8.7 7 L7.85 5.2 Z"}, fill=face),
        s("path", {"d": "M3.6 8.7 L4.8 9.8 L6 8.9 L7.2 9.8 L8.4 8.7"}, stroke=face, sw=0.8),
    ])


def icon_regalo(colored):
    box, lid, rib = ("#e3342f", 2), ("#b91c1c", 3), ("#ffd54a", 1)
    s = lambda *a, **k: _shape(colored, *a, **k)
    return "".join([
        s("rect", {"x": 1.2, "y": 5.4, "width": 9.6, "height": 6.4, "rx": 0.7}, fill=box),
        s("rect", {"x": 0.5, "y": 3.8, "width": 11, "height": 2.4, "rx": 0.7}, fill=lid),
        s("rect", {"x": 5.1, "y": 3.8, "width": 1.8, "height": 8}, fill=rib),
        s("path", {"d": "M6 3.8 C4.6 4 2.2 3.6 2.6 2 C3 0.6 5.4 1.2 6 3.8 Z"}, fill=rib),
        s("path", {"d": "M6 3.8 C7.4 4 9.8 3.6 9.4 2 C9 0.6 6.6 1.2 6 3.8 Z"}, fill=rib),
        s("circle", {"cx": 6, "cy": 3.7, "r": 0.9}, fill=rib),
    ])


def icon_fuegos(colored):
    rays = [("#ff3d6e", 2), ("#f5b700", 1), ("#16b8f3", 2), ("#9b6dff", 3)]
    s = lambda *a, **k: _shape(colored, *a, **k)
    parts = []
    for i in range(8):
        ang = math.radians(i * 45)
        r_in, r_out = 1.7, (4.9 if i % 2 == 0 else 4.2)
        x1, y1 = 6 + r_in * math.cos(ang), 6 + r_in * math.sin(ang)
        x2, y2 = 6 + r_out * math.cos(ang), 6 + r_out * math.sin(ang)
        parts.append(s("line", {"x1": f"{x1:.2f}", "y1": f"{y1:.2f}", "x2": f"{x2:.2f}", "y2": f"{y2:.2f}"},
                       stroke=rays[i % 4], sw=1.15))
    parts.append(s("circle", {"cx": 6, "cy": 6, "r": 1.1}, fill=("#f5b700", 1)))
    return "".join(parts)


def icon_pastel(colored):
    plate, bottom, top, icing = ("#8b8f98", 3), ("#ff7aa8", 2), ("#ffe0b8", 1), ("#fff6fa", 1)
    candle, flame = ("#16b8f3", 3), ("#ffb000", 1)
    s = lambda *a, **k: _shape(colored, *a, **k)
    return "".join([
        s("rect", {"x": 0.4, "y": 10.9, "width": 11.2, "height": 1, "rx": 0.5}, fill=plate),
        s("rect", {"x": 1, "y": 7, "width": 10, "height": 4, "rx": 0.8}, fill=bottom),
        s("rect", {"x": 2.4, "y": 4.5, "width": 7.2, "height": 3, "rx": 0.7}, fill=top),
        s("rect", {"x": 2.4, "y": 4.5, "width": 7.2, "height": 1.1, "rx": 0.55}, fill=icing),
        s("path", {"d": "M1.2 7.5 Q2.3 8.7 3.4 7.5 T5.8 7.5 T8.2 7.5 T10.6 7.5"}, stroke=icing, sw=0.9),
        s("rect", {"x": 5.4, "y": 2.4, "width": 1.2, "height": 2.2}, fill=candle),
        s("ellipse", {"cx": 6, "cy": 1.3, "rx": 0.85, "ry": 1.15}, fill=flame),
    ])


def icon_florero(colored):
    vase, rim, stem = ("#2f6fb5", 2), ("#dbe6f5", 1), ("#3f8f2f", 3)
    red, yel = ("#ce1126", 2), ("#fcd116", 1)
    s = lambda *a, **k: _shape(colored, *a, **k)
    return "".join([
        s("path", {"d": "M6 6 L4.6 2.6"}, stroke=stem, sw=0.6),
        s("path", {"d": "M6 6 L7.6 2.4"}, stroke=stem, sw=0.6),
        s("path", {"d": "M6 6 L6 1.8"}, stroke=stem, sw=0.6),
        s("circle", {"cx": 4.5, "cy": 2.4, "r": 1.35}, fill=red),
        s("circle", {"cx": 7.7, "cy": 2.2, "r": 1.35}, fill=yel),
        s("circle", {"cx": 6, "cy": 1.6, "r": 1.25}, fill=("#1d4fa8", 3)),
        s("path", {"d": "M4.6 5.6 L7.4 5.6 L7.4 6.6 C9.8 7.6 10 10.4 8.6 11.5 L3.4 11.5 C2 10.4 2.2 7.6 4.6 6.6 Z"}, fill=vase),
        s("rect", {"x": 4.2, "y": 5.3, "width": 3.6, "height": 1, "rx": 0.4}, fill=rim),
        s("path", {"d": "M3.6 9 Q6 10.2 8.4 9"}, stroke=rim, sw=0.6),
    ])


def icon_gato(colored):
    """El Gato del Rio de Cali: escultura rojiza, ojos redondos grises, nariz cuadrada y cola en espiral."""
    red, dark, bronze, ring, pupil = ("#a8321f", 2), ("#6f1d12", 3), ("#c9953f", 1), ("#c9ccd2", 1), ("#2a2a2a", 3)
    s = lambda *a, **k: _shape(colored, *a, **k)
    return "".join([
        # cola: placa alta con espiral arriba
        s("path", {"d": "M7.9 11.6 C7.4 8.4 7.7 4.6 8.6 3.3 C9.4 2.1 11.2 2.4 11.3 4 C11.4 5.6 10.8 8.6 11 11.6 Z"}, fill=red),
        s("path", {"d": "M9.1 3.9 C9.2 3 10.6 3 10.6 3.9 C10.6 4.8 9.5 4.9 9.4 4.2"}, stroke=dark, sw=0.5),
        # cuerpo sentado
        s("path", {"d": "M1.3 11.6 C1.1 8.4 2 5.6 3.6 4.6 L6.6 4.6 C8 5.6 8.4 8.4 8 11.6 Z"}, fill=red),
        s("ellipse", {"cx": 5.5, "cy": 9.6, "rx": 1.9, "ry": 1.7}, fill=bronze),
        s("path", {"d": "M2.7 7.2 L2.7 11.4"}, stroke=dark, sw=0.4),
        # orejas puntiagudas y cabeza
        s("path", {"d": "M1.5 3.4 L1.9 0.2 L4.2 1.7 Z"}, fill=red),
        s("path", {"d": "M7.5 3.4 L7.1 0.2 L4.8 1.7 Z"}, fill=red),
        s("rect", {"x": 1.3, "y": 1.3, "width": 6.4, "height": 3.9, "rx": 1.8}, fill=red),
        # ojos redondos con aro gris y nariz cuadrada
        s("circle", {"cx": 3.2, "cy": 2.9, "r": 1.05}, fill=ring),
        s("circle", {"cx": 5.8, "cy": 2.9, "r": 1.05}, fill=ring),
        s("circle", {"cx": 3.2, "cy": 2.9, "r": 0.4}, fill=pupil),
        s("circle", {"cx": 5.8, "cy": 2.9, "r": 0.4}, fill=pupil),
        s("rect", {"x": 4.0, "y": 3.7, "width": 1.0, "height": 1.0}, fill=pupil),
        s("path", {"d": "M1.7 4.3 C1.2 3.9 1.5 3.4 1.9 3.7 M7.3 4.3 C7.8 3.9 7.5 3.4 7.1 3.7"}, stroke=dark, sw=0.35),
    ])


def icon_vela(colored):
    """Farolito de papel del Dia de las Velitas."""
    s = lambda *a, **k: _shape(colored, *a, **k)
    return "".join([
        s("path", {"d": "M3.4 1.6 L8.6 1.6 L10 10 L2 10 Z"}, fill=("#ff9f1c", 2), stroke=("#c26a00", 3), sw=0.5),
        s("ellipse", {"cx": 6, "cy": 6, "rx": 1.8, "ry": 2.6}, fill=("#fff3b0", 1)),
        s("rect", {"x": 2, "y": 10, "width": 8, "height": 1.2, "rx": 0.4}, fill=("#8a5a2b", 3)),
        s("path", {"d": "M5 1.6 L5 0.4 L7 0.4 L7 1.6"}, stroke=("#8a5a2b", 3), sw=0.5),
    ])


ICONS = {"calabaza": icon_calabaza, "regalo": icon_regalo, "fuegos": icon_fuegos, "pastel": icon_pastel,
         "florero": icon_florero, "gato": icon_gato, "vela": icon_vela}



# ------------------------- efectos al comerse un dia especial -------------------------
BIRTHDAY_PINK = "#ff6fa5"   # color al que parpadea la serpiente en tu cumple
PINK_BLINK_FRAMES = 10      # cuantos pasos parpadea


def _track(attr, pts, dur, mode=None):
    """SMIL con (segundo, valor); asegura keyTimes crecientes dentro del ciclo."""
    pts = sorted(pts, key=lambda p: p[0])
    if pts[0][0] > 0:
        pts.insert(0, (0, pts[0][1]))
    if pts[-1][0] < dur:
        pts.append((dur, pts[-1][1]))
    ts, last = [], -1.0
    for t, _ in pts:
        t = max(0.0, min(dur, t))
        if t <= last:
            t = min(dur, last + 1e-4)
        ts.append(t)
        last = t
    ts[0] = 0.0
    vals = ";".join(str(v) for _, v in pts)
    kts = ";".join(f"{t / dur:.5f}" for t in ts)
    m = f' calcMode="{mode}"' if mode else ""
    return (f'<animate attributeName="{attr}"{m} values="{vals}" keyTimes="{kts}" '
            f'dur="{dur:.2f}s" repeatCount="indefinite"/>')


def _fx_night(te, dur, w, h):
    """31 oct: se hace de noche, sale la luna y brillan estrellas."""
    rnd = random.Random(31)
    ov = [(0, 0), (te, 0), (te + 0.5, 0.55), (te + 2.4, 0.55), (te + 3.1, 0)]
    op = [(0, 0), (te + 0.2, 0), (te + 0.7, 1), (te + 2.4, 1), (te + 3.0, 0)]
    under = f'<rect width="{w}" height="{h}" fill="#0b1030" opacity="0">{_track("opacity", ov, dur)}</rect>'
    over = [f'<g transform="translate({w - 40},{PAD + 16})" opacity="0">{_track("opacity", op, dur)}'
            '<path d="M0 -7 A7 7 0 1 0 0 7 A5.4 5.4 0 1 1 0 -7 Z" fill="#ffe9a8"/></g>']
    for i in range(int(w / 40)):
        x, y = rnd.uniform(PAD, w - PAD), rnd.uniform(PAD, h - PAD)
        over.append(
            f'<g transform="translate({x:.1f},{y:.1f})" opacity="0">{_track("opacity", op, dur)}'
            f'<circle r="1.1" fill="#fff6c0"><animate attributeName="opacity" values="1;0.2;1" '
            f'dur="{0.7 + 0.1 * (i % 4):.1f}s" repeatCount="indefinite"/></circle></g>')
    return under, over


def _fx_lights(te, dur, w, h):
    """24 dic: guirnalda de luces de Navidad parpadeando."""
    cols = ["#ff3d3d", "#34c759", "#ffc400", "#2f9bff"]
    n = max(8, int((w - 2 * PAD) / 24))
    pts = [(PAD + 4 + i * (w - 2 * PAD - 8) / (n - 1), PAD - 8 + 3 * math.sin(i * 1.1)) for i in range(n)]
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    grp = [(0, 0), (te, 0), (te + 0.25, 1), (te + 3.1, 1), (te + 3.4, 0)]
    out = [f'<g opacity="0">{_track("opacity", grp, dur)}<path d="{d}" fill="none" stroke="#2e7d32" stroke-width="0.7"/>']
    for i, (x, y) in enumerate(pts):
        blink = [(0, 0.2)] + [(te + k * 0.3, 1 if (k + i) % 2 == 0 else 0.2) for k in range(11)]
        out.append(f'<circle cx="{x:.1f}" cy="{y + 1.6:.1f}" r="1.7" fill="{cols[i % 4]}">'
                   f'{_track("opacity", blink, dur, "discrete")}</circle>')
    out.append("</g>")
    return out


def _fx_cumple(te, dur, w, h):
    """24 sep: cartel de colores que cruza el tablero."""
    cols = ["#ff3d6e", "#ff7a00", "#f5b700", "#34c759", "#16b8f3", "#9b6dff"]
    text = ["&#161;"] + list("FELIZ CUMPLE!")
    spans = "".join(f'<tspan fill="{cols[i % 6]}">{ch}</tspan>' for i, ch in enumerate(text))
    y = PAD + 2 * PITCH
    x0, x1 = -130, w + 10
    k1, k2 = min(te / dur, 0.998), min((te + 5.0) / dur, 0.999)
    tr = (f'<animateTransform attributeName="transform" type="translate" values="{x0} {y};{x0} {y};{x1} {y};{x1} {y}" '
          f'keyTimes="0;{k1:.5f};{k2:.5f};1" dur="{dur:.2f}s" repeatCount="indefinite"/>')
    return [f'<g transform="translate({x0},{y})">{tr}<text x="0" y="0" font-family="Arial Black, Arial, sans-serif" '
            f'font-weight="900" font-size="16" textLength="120" lengthAdjust="spacingAndGlyphs" stroke="#ffffff" '
            f'stroke-width="2" paint-order="stroke">{spans}</text></g>']


def _fx_countdown(te, dur, w, h, year):
    """31 dic: cuenta atras 3, 2, 1 y el a\u00f1o nuevo."""
    cx, y = w / 2, PAD + 3.5 * PITCH + 9
    items = [("3", te, te + 0.7, 28), ("2", te + 0.7, te + 1.4, 28), ("1", te + 1.4, te + 2.1, 28),
             (str(year), te + 2.1, te + 4.0, 32)]
    out = []
    for txt, a, b, fs in items:
        out.append(f'<text x="{cx}" y="{y}" text-anchor="middle" font-family="Arial Black, Arial, sans-serif" '
                   f'font-weight="900" font-size="{fs}" fill="#f5b700" stroke="#7a5200" stroke-width="1.2" '
                   f'paint-order="stroke" opacity="0">{_track("opacity", [(0, 0), (a, 1), (b, 0)], dur, "discrete")}{txt}</text>')
    return out


GOLD_BLINK = "#ffc400"      # parpadeo dorado (Halloween, Navidad y Nochevieja)


def _track_t(attr, pts, dur, kind=None):
    """Como _track pero admite animateTransform (kind: translate/rotate/scale)."""
    if not kind:
        return _track(attr, pts, dur)
    pts = sorted(pts, key=lambda p: p[0])
    if pts[0][0] > 0:
        pts.insert(0, (0, pts[0][1]))
    if pts[-1][0] < dur:
        pts.append((dur, pts[-1][1]))
    ts, last = [], -1.0
    for t, _ in pts:
        t = max(0.0, min(dur, t))
        if t <= last:
            t = min(dur, last + 1e-4)
        ts.append(t)
        last = t
    ts[0] = 0.0
    vals = ";".join(str(v) for _, v in pts)
    kts = ";".join(f"{t / dur:.5f}" for t in ts)
    return (f'<animateTransform attributeName="{attr}" type="{kind}" values="{vals}" '
            f'keyTimes="{kts}" dur="{dur:.2f}s" repeatCount="indefinite"/>')


def _particle(shape, path, t0, length, dur, spin=0, fi=0.1, fo=0.5):
    """Una figurita que viaja por 'path' (lista de (x,y)) a partir del segundo t0."""
    length = max(0.3, length)
    fo = min(fo, length - fi - 0.01)
    first = f"{path[0][0]:.1f} {path[0][1]:.1f}"
    tr = [(0, first), (t0, first)]
    for i in range(1, len(path)):
        tr.append((t0 + length * i / (len(path) - 1), f"{path[i][0]:.1f} {path[i][1]:.1f}"))
    op = [(0, 0), (t0, 0), (t0 + fi, 1), (t0 + length - fo, 1), (t0 + length, 0)]
    inner = shape
    if spin:
        inner = f'<g>{_track_t("transform", [(0, 0), (t0, 0), (t0 + length, spin)], dur, "rotate")}{shape}</g>'
    return (f'<g transform="translate({path[0][0]:.1f},{path[0][1]:.1f})">'
            f'{_track_t("transform", tr, dur, "translate")}<g opacity="0">{_track("opacity", op, dur)}{inner}</g></g>')


def _fade_group(inner, x, y, t0, length, dur, fi=0.4, fo=0.6):
    op = [(0, 0), (t0, 0), (t0 + fi, 1), (t0 + length - fo, 1), (t0 + length, 0)]
    return f'<g transform="translate({x:.1f},{y:.1f})" opacity="0">{_track("opacity", op, dur)}{inner}</g>'


def _star_pts(r):
    return " ".join(f"{(r if i % 2 == 0 else r * .45) * math.cos(-math.pi / 2 + i * math.pi / 5):.2f},"
                    f"{(r if i % 2 == 0 else r * .45) * math.sin(-math.pi / 2 + i * math.pi / 5):.2f}" for i in range(10))


def _bat(color):
    return (f'<g><animateTransform attributeName="transform" type="scale" values="1 1;1 0.35;1 1" dur="0.32s" repeatCount="indefinite"/>'
            f'<path d="M-0.8 -0.3 Q-2.8 -3 -6.4 -1.6 Q-4.8 -0.4 -5.2 1.6 Q-3 0.5 -0.8 1.2 Z" fill="{color}"/>'
            f'<path d="M0.8 -0.3 Q2.8 -3 6.4 -1.6 Q4.8 -0.4 5.2 1.6 Q3 0.5 0.8 1.2 Z" fill="{color}"/>'
            f'<ellipse cx="0" cy="0.3" rx="1.2" ry="1.7" fill="{color}"/>'
            f'<path d="M-1 -1.2 L-0.7 -2.6 L0 -1.5 L0.7 -2.6 L1 -1.2 Z" fill="{color}"/></g>')


def _santa():
    return ('<g><ellipse cx="0" cy="4.5" rx="3.6" ry="4" fill="#d62828"/><rect x="-3.6" y="6.2" width="7.2" height="1" fill="#fff"/>'
            '<circle cx="0" cy="-1.2" r="2.6" fill="#f6c9a0"/>'
            '<path d="M-2.7 -0.2 Q0 5 2.7 -0.2 Q0 1.4 -2.7 -0.2 Z" fill="#fff"/>'
            '<path d="M-2.9 -2.2 L0 -8.4 L2.9 -2.2 Z" fill="#d62828"/><rect x="-3" y="-2.6" width="6" height="1.3" rx="0.6" fill="#fff"/>'
            '<circle cx="0" cy="-8.6" r="1.1" fill="#fff"/><circle cx="-0.9" cy="-1.5" r="0.35" fill="#222"/>'
            '<circle cx="0.9" cy="-1.5" r="0.35" fill="#222"/></g>')


def _baby():
    return ('<g><path d="M-7 5 L7 5 L5.5 9 L-5.5 9 Z" fill="#8a5a2b"/><path d="M-6 5 Q0 2.5 6 5" fill="#e9c46a"/>'
            '<ellipse cx="0" cy="3.6" rx="4.4" ry="2.3" fill="#fff6dc"/><circle cx="-1.6" cy="1.3" r="2.5" fill="#f6c9a0"/>'
            '<ellipse cx="-1.6" cy="-1.8" rx="3" ry="0.9" fill="none" stroke="#ffd54a" stroke-width="0.8"/>'
            '<circle cx="-2.4" cy="1.2" r="0.3" fill="#444"/><circle cx="-0.8" cy="1.2" r="0.3" fill="#444"/></g>')


def _burst(x, y, t0, dur, r_out=17, length=0.9, n=12, width=1.8,
           colors=("#ff3d6e", "#f5b700", "#16b8f3", "#9b6dff")):
    parts = []
    for i in range(n):
        a = math.radians(i * 360 / n)
        r1 = r_out if i % 2 == 0 else r_out * 0.76
        parts.append(f'<line x1="{2.5 * math.cos(a):.2f}" y1="{2.5 * math.sin(a):.2f}" x2="{r1 * math.cos(a):.2f}" '
                     f'y2="{r1 * math.sin(a):.2f}" stroke="{colors[i % len(colors)]}" stroke-width="{width}" stroke-linecap="round"/>')
        parts.append(f'<circle cx="{(r1 + 2.2) * math.cos(a):.2f}" cy="{(r1 + 2.2) * math.sin(a):.2f}" r="1.1" fill="{colors[(i + 1) % len(colors)]}"/>')
    sc = [(0, 0.2), (t0, 0.2), (t0 + 0.5, 1.3)]
    op = [(0, 0), (t0, 0), (t0 + 0.02, 1), (t0 + length, 0)]
    return (f'<g transform="translate({x:.1f},{y:.1f})"><g>{_track_t("transform", sc, dur, "scale")}'
            f'<g opacity="0">{_track("opacity", op, dur)}{"".join(parts)}</g></g></g>')


def _fx_bats_big(te, dur, w, h):
    """31 oct: murcielagos grandes que cruzan por delante."""
    out = []
    for i in range(4):
        y = PAD + 14 + i * 22
        a, b = (-18, w + 18) if i % 2 == 0 else (w + 18, -18)
        shape = '<g transform="scale(1.9)">' + _bat("#1b1230") + "</g>"
        out.append(_particle(shape, [(a, y), ((a + b) / 2, y - 8), (b, y)], te + 0.15 + i * 0.35, 2.4 + w / 400, dur, fi=0.1, fo=0.1))
    return out


def _fx_santa_baby(te, dur, w, h, cx):
    """24 dic: Papa Noel cruza el tablero y aparece el Nino Dios con estrella."""
    out = []
    y0 = PAD + 5.5 * PITCH
    pts = [(-12 + (w + 24) * k / 24, y0 + (-2 if k % 2 else 1)) for k in range(25)]
    out.append(_particle(_santa(), pts, te, 4.0 + w / 160, dur, fi=0.2, fo=0.2))
    bx = min(max(cx, PAD + 30), w - PAD - 30)
    by = PAD + 2.3 * PITCH
    rays = "".join(f'<line x1="0" y1="0" x2="{16 * math.cos(i * math.pi / 5):.1f}" y2="{16 * math.sin(i * math.pi / 5):.1f}" '
                   'stroke="#ffe28a" stroke-width="0.9" stroke-linecap="round"/>' for i in range(10))
    spin = f'<g>{_track_t("transform", [(0, 0), (te, 0), (te + 3.2, 40)], dur, "rotate")}{rays}</g>'
    out.append(_fade_group(spin + _baby(), bx, by, te, 3.4, dur))
    star = (f'<g><animateTransform attributeName="transform" type="scale" values="0.65;1.3;0.65" dur="0.5s" repeatCount="indefinite"/>'
            f'<polygon points="{_star_pts(3.4)}" fill="#ffc400"/></g>')
    out.append(_fade_group(star, bx + 9, by - 16, te, 3.4, dur))
    return out


def _fx_rockets(te, dur, w, h, cx):
    """31 dic: 3 cohetes que suben y explotan."""
    out = []
    for i, dx in enumerate((-60, 0, 55)):
        x = min(max(cx + dx, PAD + 20), w - PAD - 20)
        t0 = te + 0.3 + i * 0.7
        top = PAD + 12 + 6 * i
        out.append(_particle('<path d="M0 -4 L1.4 1 L-1.4 1 Z" fill="#ff3d6e"/>', [(x, h - PAD + 4), (x, top)], t0, 0.7, dur, fi=0.03, fo=0.05))
        out.append(_burst(x, top, t0 + 0.7, dur, r_out=17))
    return out


def _fx_confetti_gold(te, dur, w, h):
    """31 dic: lluvia de confeti dorado y plateado."""
    rnd = random.Random(2027)
    out = []
    for i in range(int(w / 10)):
        x0 = rnd.uniform(PAD, w - PAD)
        pts = [(x0 + 4 * math.sin(u * 8 + i), PAD - 8 + (h - 2 * PAD + 14) * u) for u in [k / 7 for k in range(8)]]
        c = ["#f5b700", "#ffd54a", "#c0c7d0", "#ffc400"][i % 4]
        shape = f'<circle r="{rnd.choice([0.9, 1.2, 1.5])}" fill="{c}"/>'
        out.append(_particle(shape, pts, te + rnd.uniform(0, 1.4), rnd.uniform(2.2, 3.0), dur, fi=0.1, fo=0.4))
    return out


CALI_BLUE = "#6ec6ff"        # parpadeo celeste (25 de julio)
TRICOLOR = ["#fcd116", "#1d4fa8", "#ce1126"]


def _note(c):
    return (f'<g><ellipse cx="0" cy="3" rx="1.7" ry="1.2" fill="{c}" transform="rotate(-20 0 3)"/>'
            f'<rect x="1.2" y="-4" width="0.8" height="7.3" fill="{c}"/><path d="M2 -4 Q4.6 -3 3.8 0" fill="none" stroke="{c}" stroke-width="0.8"/></g>')


def _vase():
    return ('<path d="M-2.6 -3 L2.6 -3 L2.6 -1.5 C6 -0.5 6 5 3.6 6.5 L-3.6 6.5 C-6 5 -6 -0.5 -2.6 -1.5 Z" fill="#2f6fb5"/>'
            '<rect x="-3.2" y="-3.6" width="6.4" height="1.2" rx="0.5" fill="#dbe6f5"/>')


def _flower(c):
    return f'<circle r="1.5" fill="{c}"/><circle r="0.5" fill="#fff6c0"/>'


def _waving_flag():
    w, h = 14, 9
    return ('<g><animateTransform attributeName="transform" type="skewY" values="-6;6;-6" dur="0.45s" repeatCount="indefinite"/>'
            f'<rect x="0" y="0" width="{w}" height="{h / 2}" fill="#fcd116"/><rect x="0" y="{h / 2}" width="{w}" height="{h / 4}" fill="#1d4fa8"/>'
            f'<rect x="0" y="{h * 3 / 4}" width="{w}" height="{h / 4}" fill="#ce1126"/>'
            f'<line x1="0" y1="-1" x2="0" y2="{h + 5}" stroke="#6b5a45" stroke-width="0.8"/></g>')


def _fx_florero(te, dur, w, h, cx, cy):
    """7 ago: el florero cae y se rompe (flores tricolor) y cruza la bandera de Colombia."""
    rnd = random.Random(7)
    out = [_particle(_vase(), [(cx, PAD - 6), (cx, cy)], max(0.0, te - 0.7), 0.7, dur, fi=0.05, fo=0.01)]
    for i in range(10):
        a = math.radians(rnd.uniform(-170, -10))
        d = rnd.uniform(14, 32)
        sh = f'<polygon points="0,-2 2,1 -1.5,1.6" fill="{["#2f6fb5", "#dbe6f5", "#1d4fa8"][i % 3]}"/>'
        out.append(_particle(sh, [(cx, cy), (cx + d * math.cos(a), cy + d * math.sin(a) * 0.8), (cx + d * 1.2 * math.cos(a), cy + 18)],
                             te, 1.5, dur, spin=rnd.choice([-1, 1]) * 400, fi=0.03, fo=0.5))
    for i in range(8):
        x1 = cx + rnd.uniform(-30, 30)
        out.append(_particle(_flower(TRICOLOR[i % 3]), [(cx, cy), (x1, cy - 26), (x1 + rnd.uniform(-8, 8), cy + 10)],
                             te, 2.0, dur, spin=300, fi=0.05, fo=0.5))
    pts = [(-18 + (w + 36) * k / 10, cy - 8 + 5 * math.sin(k)) for k in range(11)]
    out.append(_particle(_waving_flag(), pts, te, 4.5 + w / 300, dur, fi=0.2, fo=0.2))
    return out


def _butterfly():
    return ('<g><g><animateTransform attributeName="transform" type="scale" values="1 1;0.25 1;1 1" dur="0.35s" repeatCount="indefinite"/>'
            '<path d="M0 0 C-5 -5 -7 -1 -4 1.5 C-6 3.6 -2 5 0 1.4 Z" fill="#ffd23f"/><path d="M0 0 C5 -5 7 -1 4 1.5 C6 3.6 2 5 0 1.4 Z" fill="#ffd23f"/>'
            '<circle cx="-3.4" cy="-0.6" r="0.7" fill="#e0a800"/><circle cx="3.4" cy="-0.6" r="0.7" fill="#e0a800"/></g>'
            '<rect x="-0.4" y="-1.6" width="0.8" height="4.4" rx="0.4" fill="#5a3a1a"/></g>')


def _condor():
    return ('<g><g><animateTransform attributeName="transform" type="scale" values="1 1;1 0.55;1 1" dur="1.1s" repeatCount="indefinite"/>'
            '<path d="M0 0 C-6 -6 -16 -4 -22 2 C-16 0 -10 1 -6 3 Z" fill="#1c1c24"/><path d="M0 0 C6 -6 16 -4 22 2 C16 0 10 1 6 3 Z" fill="#1c1c24"/>'
            '<path d="M-20 1.6 L-14 0.4 M-16 2 L-10 1 M20 1.6 L14 0.4 M16 2 L10 1" stroke="#fff" stroke-width="0.8"/></g>'
            '<ellipse cx="0" cy="1.6" rx="2.6" ry="4" fill="#1c1c24"/><rect x="-2.6" y="-1.6" width="5.2" height="1.2" rx="0.5" fill="#fff"/>'
            '<circle cx="0" cy="-3" r="2" fill="#c9302c"/><path d="M0 -3 L1.6 -2.4 L0 -1.8 Z" fill="#e0c050"/></g>')


def _chiva():
    return ('<g><rect x="-13" y="-2" width="26" height="8" rx="1.5" fill="#fcd116"/><rect x="-13" y="2.4" width="26" height="1.6" fill="#1d4fa8"/>'
            '<rect x="-13" y="4" width="26" height="2" fill="#ce1126"/>'
            + "".join(f'<rect x="{-10 + i * 5.4}" y="-1.2" width="3.6" height="3" fill="#bfe6ff"/>' for i in range(5)) +
            '<rect x="-9" y="-5.2" width="14" height="3.2" rx="0.8" fill="#8a5a2b"/><rect x="-6" y="-7" width="5" height="1.8" fill="#2f9e44"/>'
            '<circle cx="-7.5" cy="6.4" r="2.2" fill="#222"/><circle cx="7.5" cy="6.4" r="2.2" fill="#222"/>'
            '<circle cx="-7.5" cy="6.4" r="0.8" fill="#999"/><circle cx="7.5" cy="6.4" r="0.8" fill="#999"/></g>')


def _fx_colombia(te, dur, w, h, cx, cy):
    """25 jul: mariposas amarillas, condor de los Andes y una chiva."""
    rnd = random.Random(25)
    out = []
    spread = max(80, w / 8)
    for i in range(16):
        x1 = cx + rnd.uniform(-spread, spread)
        pts = [(cx + (x1 - cx) * u + 7 * math.sin(u * 11 + i), cy - (cy + 8) * u + 4 * math.cos(u * 9)) for u in [k / 10 for k in range(11)]]
        out.append(_particle(_butterfly(), pts, te + i * 0.14, 3.2, dur, fi=0.15, fo=0.6))
    top = PAD + 18
    pts = [(-30 + (w + 60) * u, top - 8 * math.sin(math.pi * u)) for u in [k / 8 for k in range(9)]]
    out.append(_particle(_condor(), pts, te, 5.0 + w / 250, dur, fi=0.3, fo=0.3))
    pts2 = [(x, y + 30) for x, y in pts][::-1]
    out.append(_particle(f'<g transform="scale(0.6)">{_condor()}</g>', pts2, te + 0.9, 4.2 + w / 250, dur, fi=0.3, fo=0.3))
    y = h - PAD - 8
    pts = [(-30 + (w + 60) * k / 20, y + (-1.2 if k % 2 else 0.6)) for k in range(21)]
    out.append(_particle(_chiva(), pts, te, 5.0 + w / 250, dur, fi=0.2, fo=0.2))
    return out


def _candle():
    return ('<g><rect x="-1.6" y="0" width="3.2" height="7" rx="0.6" fill="#f4f0e6"/><rect x="-1.6" y="2.6" width="3.2" height="0.8" fill="#d9d2bf"/>'
            '<g><animateTransform attributeName="transform" type="scale" values="1 1;0.85 1.12;1 1" dur="0.4s" repeatCount="indefinite"/>'
            '<path d="M0 -4.4 C2 -2.2 1.8 -0.4 0 0 C-1.8 -0.4 -2 -2.2 0 -4.4 Z" fill="#ffb000"/>'
            '<path d="M0 -2.4 C0.9 -1.4 0.9 -0.5 0 -0.2 C-0.9 -0.5 -0.9 -1.4 0 -2.4 Z" fill="#ff6a00"/></g></g>')


def _fx_velitas(te, dur, w, h):
    """7 dic: anochece y se encienden las velitas, una a una."""
    n = max(8, int((w - 2 * PAD) / 36))
    total = n * 0.2 + 2.0
    ov = [(0, 0), (te, 0), (te + 0.5, 0.5), (te + total - 0.7, 0.5), (te + total, 0)]
    under = f'<rect width="{w}" height="{h}" fill="#0b1030" opacity="0">{_track("opacity", ov, dur)}</rect>'
    over = []
    y = h - PAD - 15
    for i in range(n):
        x = PAD + 10 + (w - 2 * PAD - 20) * i / (n - 1)
        t0 = te + 0.3 + i * 0.2
        op = [(0, 0), (t0, 0), (t0 + 0.05, 1), (te + total - 0.4, 1), (te + total, 0)]
        over.append(f'<g transform="translate({x:.1f},{y:.1f})" opacity="0">{_track("opacity", op, dur)}'
                    f'<circle cy="-3" r="9" fill="#ffd27a" opacity="0.28"/>{_candle()}</g>')
    return under, over


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


def build_svg(cells, frames, eaten_at, dates=None):
    cols = max(k[0] for k in cells) + 1

    # celdas de dias especiales (segun la fecha de cada cuadrito)
    special = {}
    for k, d in (dates or {}).items():
        kind = SPECIAL_DAYS.get((d.month, d.day))
        if kind in ICONS and k in cells:
            special[k] = kind
    width = cols * PITCH + PAD * 2
    height = ROWS * PITCH + PAD * 2

    nonzero = [v for v in cells.values() if v > 0]
    vmax = max(nonzero) if nonzero else 1

    def level(v):
        return 0 if v <= 0 else max(1, min(4, math.ceil(4 * v / vmax)))

    def pos(c, r):
        return PAD + c * PITCH, PAD + r * PITCH

    n = len(frames)
    dt = STEP_SECONDS   # siempre la misma velocidad, tenga los dias que tenga
    dur = n * dt

    # efectos de los dias especiales que de verdad se comen (solo si hubo contribucion)
    fx_under, fx_over, blink = [], [], {}
    for cell, i in sorted(eaten_at.items()):
        kind = special.get(cell)
        if not kind:
            continue
        te = i * dt
        if kind == "calabaza":
            u, o = _fx_night(te, dur, width, height)
            fx_under.append(u)
            fx_over += o
            fx_over += _fx_bats_big(te, dur, width, height)
            blink.update({j: (i, GOLD_BLINK) for j in range(i, min(i + PINK_BLINK_FRAMES, n))})
        elif kind == "regalo":
            fx_over += _fx_lights(te, dur, width, height)
            fx_over += _fx_santa_baby(te, dur, width, height, pos(*cell)[0] + CELL / 2)
            blink.update({j: (i, GOLD_BLINK) for j in range(i, min(i + PINK_BLINK_FRAMES, n))})
        elif kind == "pastel":
            fx_over += _fx_cumple(te, dur, width, height)
            blink.update({j: (i, BIRTHDAY_PINK) for j in range(i, min(i + PINK_BLINK_FRAMES, n))})
        elif kind == "florero":
            cx_, cy_ = pos(*cell)[0] + CELL / 2, pos(*cell)[1] + CELL / 2
            fx_over += _fx_florero(te, dur, width, height, cx_, cy_)
            blink.update({j: (i, GOLD_BLINK) for j in range(i, min(i + PINK_BLINK_FRAMES, n))})
        elif kind == "gato":
            cx_, cy_ = pos(*cell)[0] + CELL / 2, pos(*cell)[1] + CELL / 2
            fx_over += _fx_colombia(te, dur, width, height, cx_, cy_)
            blink.update({j: (i, CALI_BLUE) for j in range(i, min(i + PINK_BLINK_FRAMES, n))})
        elif kind == "vela":
            u, o = _fx_velitas(te, dur, width, height)
            fx_under.append(u)
            fx_over += o
            blink.update({j: (i, GOLD_BLINK) for j in range(i, min(i + PINK_BLINK_FRAMES, n))})
        elif kind == "fuegos":
            fx_over += _fx_confetti_gold(te, dur, width, height)
            fx_over += _fx_countdown(te, dur, width, height, dates[cell].year + 1)
            fx_over += _fx_rockets(te, dur, width, height, pos(*cell)[0] + CELL / 2)
            blink.update({j: (i, GOLD_BLINK) for j in range(i, min(i + PINK_BLINK_FRAMES, n))})

    css = []
    for name, pal, gray in (("", LIGHT, GRAY_LIGHT), ("@media (prefers-color-scheme: dark){", DARK, GRAY_DARK)):
        rules = [f".e{{fill:{pal['empty']}}}"] + [f".l{i}{{fill:{pal[i]}}}" for i in range(1, 5)]
        rules += [f".g{i}{{fill:{gray[i]}}}.t{i}{{stroke:{gray[i]}}}" for i in range(1, 4)]
        css.append("".join(rules) if not name else name + "".join(rules) + "}")

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        f"<style>{''.join(css)}</style>",
        f'<defs><clipPath id="cell"><rect width="{CELL}" height="{CELL}" rx="2.5"/></clipPath></defs>',
    ]

    # tablero
    for (c, r), v in sorted(cells.items()):
        x, y = pos(c, r)
        if (c, r) in special:   # dia especial: fondo neutro + dibujo (a color si hubo contribucion)
            out.append(f'<rect class="e" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.5"/>')
            icon = ICONS[special[(c, r)]](colored=v > 0)
            out.append(f'<g transform="translate({x},{y})" clip-path="url(#cell)">{icon}</g>')
            continue
        cls = "e" if level(v) == 0 else f"l{level(v)}"
        out.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.5"/>')

    # dias comidos: se "borran" cuando pasa la cabeza y reaparecen al reiniciar
    for cell, i in sorted(eaten_at.items()):
        x, y = pos(*cell)
        vals, times = ["0", "1", "1"], [0.0, i / n, 1.0]
        # un poquito mas grande que la celda para tapar el borde fino del color de abajo
        out.append(
            f'<rect class="e" x="{x - 0.4}" y="{y - 0.4}" width="{CELL + 0.8}" height="{CELL + 0.8}" rx="2.9" opacity="0">'
            f'{anim("opacity", vals, times, dur)}</rect>'
        )

    out.extend(fx_under)   # la noche va por debajo de la serpiente

    # estados por celda y frame
    color = {"body": SNAKE_BODY, "dead": SNAKE_DEAD, "win": SNAKE_WIN}
    body_state, head_state = {}, {}
    for i, (body, mode) in enumerate(frames):
        st = "dead" if mode == "dead" else "win" if mode == "win" else "body"
        if st == "body" and i in blink and (i - blink[i][0]) % 2 == 0:
            st = "blink:" + blink[i][1]   # parpadeo de color al comerse un dia especial
        for cell in set(body):
            if cell not in body_state:
                body_state[cell] = ["off"] * n
            body_state[cell][i] = st
        if body and mode in ("play", "alive"):
            if body[0] not in head_state:
                head_state[body[0]] = ["off"] * n
            head_state[body[0]][i] = ("head" + st[5:]) if st.startswith("blink") else "head"

    for cell, states in sorted(body_state.items()):
        x, y = pos(*cell)
        vals, times = compress(states, n)
        colors = [s[6:] if s.startswith("blink:") else color.get(s, SNAKE_BODY) for s in vals]
        ops = ["0" if s == "off" else "1" for s in vals]
        out.append(
            f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.5" fill="{SNAKE_BODY}" opacity="0">'
            f'{anim("opacity", ops, times, dur)}{anim("fill", colors, times, dur)}</rect>'
        )
    for cell, states in sorted(head_state.items()):
        x, y = pos(*cell)
        vals, times = compress(states, n)
        ops = ["0" if s == "off" else "1" for s in vals]
        hcol = [s[5:] if s.startswith("head:") else SNAKE_HEAD for s in vals]
        out.append(
            f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.5" fill="{SNAKE_HEAD}" opacity="0">'
            f'{anim("opacity", ops, times, dur)}{anim("fill", hcol, times, dur)}</rect>'
        )

    out.extend(fx_over)
    out.append("</svg>")
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    force = "die" if "--die" in args else "win" if "--win" in args else None
    if "--demo" in args:
        days = int(args[args.index("--days") + 1]) if "--days" in args else 40
        cells = demo_calendar(days)
        dates = demo_dates(cells)
        # en la prueba: Halloween y Nochebuena con contribucion (a color), cumple y Nochevieja sin ella (gris)
        for k, d in dates.items():
            if (d.month, d.day) in ((10, 31), (12, 24)) or ("--efectos" in args and (d.month, d.day) in ((9, 24), (12, 31), (7, 25), (8, 7), (12, 7))):
                cells[k] = 5
            elif (d.month, d.day) in ((9, 24), (12, 31)):
                cells[k] = 0
    else:
        user = os.environ.get("SNAKE_USER") or os.environ.get("GITHUB_ACTOR")
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not user or not token:
            raise SystemExit("Faltan SNAKE_USER y GH_TOKEN (o usa --demo para probar).")
        cells, dates = fetch_calendar(user, token)

    frames, eaten_at, dies = simulate(cells, force)
    svg = build_svg(cells, frames, eaten_at, dates)

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
