"""Vuelca el backlog del proyecto (gestion/backlog.yml) a GitHub.

Subcomandos
    validate   Comprueba el backlog: numeración, dependencias (solo hacia tareas
               anteriores), ciclos, revisores, pre-registros, carga por sprint,
               reparto por áreas y rotación de revisiones.
    report     Genera el roadmap en Markdown (stdout o ``-o fichero``).
    plan       Simulación offline de ``apply``: no llama a GitHub.
    apply      Crea o completa en GitHub labels, milestones, tareas, épicas,
               sub-issues, dependencias y el GitHub Project (campos, items y
               vistas). Idempotente: relanzarlo no duplica nada. En un repo sin
               issues ni PRs, la tarea N queda como issue #N.

Requisitos
    Python >= 3.11, PyYAML y GitHub CLI (gh) autenticado con los scopes
    ``repo`` y ``project`` (``gh auth refresh -s project``). Se ejecuta desde
    dentro del clon del repositorio (el repo se detecta del remoto ``origin``).

Uso
    uv run --with pyyaml python gestion/bootstrap_github.py validate
    uv run --with pyyaml python gestion/bootstrap_github.py report -o gestion/ROADMAP.md
    uv run --with pyyaml python gestion/bootstrap_github.py plan
    uv run --with pyyaml python gestion/bootstrap_github.py apply
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, ClassVar

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - mensaje para el usuario
    sys.stderr.write(
        "Falta PyYAML. Ejecuta el script así:\n"
        "  uv run --with pyyaml python gestion/bootstrap_github.py <comando>\n"
    )
    raise SystemExit(2) from None

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_BACKLOG = HERE / "backlog.yml"

MARKER = "<!-- backlog-id: {id} -->"
MARKER_RE = re.compile(r"<!--\s*backlog-id:\s*([A-Za-z0-9-]+)\s*-->")
CHILDREN_START = "<!-- children:start -->"
CHILDREN_END = "<!-- children:end -->"
REF_RE = re.compile(r"#(\d+)\b")
REMOTE_RE = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")

FIBONACCI = {1, 2, 3, 5, 8, 13}
PRIORITIES = ("P0", "P1", "P2")
COVERAGE_AREAS = ("T1", "T2", "comun", "memoria")
PHASE_RANK = {"start": 0, None: 1, "end": 2}
PREREG_FIELDS = {
    "hipotesis": "Hipótesis",
    "candidatos": "Candidatos",
    "rendimiento": "Métricas de rendimiento",
    "recursos": "Métricas de recursos",
    "criterio": "Criterio de decisión",
    "entorno": "Entorno de medición",
}
ADR_RE = re.compile(r"^ADR-(\d{3})-[a-z0-9-]+$")
MONTHS = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)
PROJECTS_REST_API_VERSION = "2026-03-10"
MIN_GH_VERSION = (2, 40)

# Criterios comunes por tipo de tarea (los específicos están en backlog.yml).
EXP_ACCEPTANCE = [
    "Pre-registro de abajo completado y fijado **antes** de ejecutar.",
    "Todas las opciones medidas en el mismo entorno, con el hardware registrado en cada resultado.",
    "Rendimiento con IC95 según el pre-registro.",
    "Recursos: tiempo total, latencia p50/p95, RAM/VRAM pico y energía (kWh, con el método de medición declarado).",
    "Resumen en este issue: tabla de resultados y gráfico calidad vs. coste.",
    "Borrador de `doc/adr/{adr}.md` con contexto, opciones y tabla de evidencias, listo para la decisión {dec}.",
]
DEC_ACCEPTANCE = [
    "Reunión de los tres (≤45 min) sobre las evidencias: {inputs}.",
    "Decisión tomada con el criterio pre-registrado; votos y discrepancias anotados en el ADR.",
    "`doc/adr/{adr}.md` con decisión, consecuencias, fecha y participantes, en estado *Aceptado*.",
    "Tareas que dependían de esta decisión revisadas y desbloqueadas.",
]
DOD = {
    "default": [
        "PR contra `develop` con `Closes {self}`, aprobada por {reviewers}.",
        "CI en verde; tests añadidos o actualizados.",
        "Reproducible: configuración en `configs/`, semilla fija y comando en la descripción de la PR.",
        "Entrada en `Diario_de_trabajo.md` (qué, por qué, fuente y estado).",
    ],
    "experimento": [
        "PR contra `develop` con `Closes {self}`, aprobada por {reviewers}.",
        "Resultados en `results/{id}/` con el esquema común (rendimiento, recursos y hardware).",
        "Entrada en `Diario_de_trabajo.md` con las opciones probadas y su resultado.",
    ],
    "decision": [
        "PR del ADR contra `develop` con `Closes {self}`, aprobada por {reviewers}.",
        "Entrada en `Diario_de_trabajo.md` y fila en `doc/adr/README.md`.",
    ],
    "gestion": [
        "Acta en `Diario_de_trabajo.md` mediante PR (`Closes {self}`) aprobada por {reviewers}.",
        "Tablero actualizado: estados, sprint y estimaciones.",
    ],
    "docs": [
        "PR contra `develop` con `Closes {self}`, aprobada por {reviewers}.",
        "Cifras y figuras trazables a `results/`; referencias en `referencias.bib`.",
        "Entrada en `Diario_de_trabajo.md` si se tomó alguna decisión.",
    ],
    "entrega": [
        "Evidencia (captura, hash o log) adjunta a este issue y revisada por {reviewers}.",
        "Entrada en `Diario_de_trabajo.md`.",
    ],
}
# Se añade a la Definition of Done de todas las tareas: el repo será público al acabar.
PUBLIC_DOD = (
    "Apto para un repo público (CONTRIBUTING §14): sin secretos, textos del corpus, material de la "
    "asignatura ni datos personales en el diff, los notebooks, `results/` o los logs."
)
DEC_AGENDA = [
    "Repaso del criterio pre-registrado (5 min).",
    "Resultados presentados por quien hizo cada experimento (10 min).",
    "Discusión de los compromisos entre rendimiento y recursos (15 min).",
    "Decisión y votación; consecuencias y tareas afectadas (10 min).",
    "Quién redacta y quién revisa el ADR (5 min).",
]


class BacklogError(Exception):
    """El backlog no se puede cargar."""


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    key: str
    github: str
    name: str
    role: str

    @property
    def handle(self) -> str:
        return f"@{self.github}"


@dataclass(frozen=True)
class Sprint:
    id: str
    title: str
    start: dt.date
    end: dt.date | None
    goal: str
    order: int


@dataclass(frozen=True)
class LabelSpec:
    name: str
    color: str
    description: str


@dataclass
class Epic:
    id: str
    title: str
    area: str
    goal: str

    @property
    def full_title(self) -> str:
        return f"[{self.id}] Épica · {self.title}"


@dataclass
class Item:
    id: str
    title: str
    epic: str
    type: str
    area: str
    owners: list[str]
    reviewers: list[str]
    sprint: str
    phase: str | None
    points: int
    priority: str
    depends_on: list[str]
    goal: str
    acceptance: list[str]
    notes: str
    prereg: dict[str, str] | None
    decision: str | None
    adr: str | None
    no_experiment_required: bool
    index: int

    @property
    def lead(self) -> str:
        return self.owners[0]

    @property
    def full_title(self) -> str:
        return f"[{self.id}] {self.title}"

    @property
    def number(self) -> int:
        return int(self.id) if self.id.isdigit() else 0


@dataclass
class Backlog:
    path: Path
    project: dict[str, Any]
    capacity: dict[str, Any]
    team: dict[str, Member]
    sprints: dict[str, Sprint]
    label_types: dict[str, LabelSpec]
    label_areas: dict[str, LabelSpec]
    label_priorities: dict[str, LabelSpec]
    label_extra: list[LabelSpec]
    board: dict[str, Any]
    epics: dict[str, Epic]
    items: dict[str, Item]
    risks: list[dict[str, Any]]
    scope_cut: list[str]
    duplicates: list[str]

    @property
    def capacity_points(self) -> float:
        return float(self.capacity.get("points_per_member_per_sprint", 12))

    def all_labels(self) -> list[LabelSpec]:
        return [
            *self.label_types.values(),
            *self.label_areas.values(),
            *self.label_priorities.values(),
            *self.label_extra,
        ]

    def labels_for(self, entity: Epic | Item) -> list[str]:
        if isinstance(entity, Epic):
            return [self.label_types["epica"].name, self.label_areas[entity.area].name]
        return [
            self.label_types[entity.type].name,
            self.label_areas[entity.area].name,
            self.label_priorities[entity.priority].name,
        ]

    def handles(self, keys: list[str]) -> list[str]:
        return [self.team[k].handle for k in keys if k in self.team]

    def experiments_for(self, dec_id: str) -> list[Item]:
        return [
            i
            for i in self.items.values()
            if i.type == "experimento" and i.decision == dec_id
        ]

    def successors(self, iid: str) -> list[Item]:
        return [i for i in self.items.values() if iid in i.depends_on]

    def children(self, epic_id: str) -> list[Item]:
        kids = [i for i in self.items.values() if i.epic == epic_id]
        return sorted(kids, key=lambda i: (i.number, i.index))

    def sprint_range(self, sprint: Sprint) -> str:
        end = fmt_date(sprint.end) if sprint.end else "fecha del examen"
        return f"{fmt_date(sprint.start)} → {end}"

    def creation_order(self) -> list[Item | Epic]:
        """Tareas en orden numérico y épicas al final (tarea N = issue #N en un repo vacío)."""
        return [
            *sorted(self.items.values(), key=lambda i: (i.number, i.index)),
            *self.epics.values(),
        ]


def fmt_date(value: dt.date | None) -> str:
    if value is None:
        return "—"
    return f"{value.day:02d}-{MONTHS[value.month - 1]}"


def as_date(value: Any, where: str) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise BacklogError(
            f"{where}: fecha no válida «{value}» (usa AAAA-MM-DD)"
        ) from exc


def _req(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data or data[key] is None:
        raise BacklogError(f"{where}: falta el campo obligatorio «{key}»")
    return data[key]


def _label(spec: dict[str, Any], where: str) -> LabelSpec:
    return LabelSpec(
        name=str(_req(spec, "name", where)),
        color=str(_req(spec, "color", where)).lstrip("#"),
        description=str(spec.get("description", "")),
    )


def load_backlog(path: Path) -> Backlog:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BacklogError(f"No existe {path}") from exc
    except yaml.YAMLError as exc:
        raise BacklogError(f"YAML no válido en {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BacklogError("El backlog debe ser un mapa YAML")

    team = {
        str(key): Member(
            key=str(key),
            github=str(_req(m, "github", f"team.{key}")).lstrip("@"),
            name=str(m.get("name", key)),
            role=str(m.get("role") or ""),
        )
        for key, m in _req(raw, "team", "raíz").items()
    }

    sprints: dict[str, Sprint] = {}
    for order, s in enumerate(_req(raw, "sprints", "raíz")):
        where = f"sprints[{order}]"
        sid = str(_req(s, "id", where))
        sprints[sid] = Sprint(
            id=sid,
            title=str(_req(s, "title", where)),
            start=as_date(_req(s, "start", where), where),
            end=as_date(s.get("end"), where),
            goal=str(s.get("goal", "")).strip(),
            order=order,
        )

    labels = _req(raw, "labels", "raíz")
    label_types = {
        k: _label(v, f"labels.types.{k}") for k, v in labels.get("types", {}).items()
    }
    label_areas = {
        k: _label(v, f"labels.areas.{k}") for k, v in labels.get("areas", {}).items()
    }
    label_prios = {
        k: _label(v, f"labels.priorities.{k}")
        for k, v in labels.get("priorities", {}).items()
    }
    label_extra = [_label(v, "labels.extra") for v in labels.get("extra", [])]

    seen: Counter[str] = Counter()
    epics: dict[str, Epic] = {}
    for n, e in enumerate(_req(raw, "epics", "raíz")):
        where = f"epics[{n}]"
        eid = str(_req(e, "id", where))
        seen[eid] += 1
        epics[eid] = Epic(
            id=eid,
            title=str(_req(e, "title", where)),
            area=str(_req(e, "area", where)),
            goal=str(e.get("goal", "")).strip(),
        )

    items: dict[str, Item] = {}
    for n, it in enumerate(_req(raw, "items", "raíz")):
        iid = str(_req(it, "id", f"items[{n}]"))
        seen[iid] += 1
        prereg = it.get("prereg")
        items[iid] = Item(
            id=iid,
            title=str(_req(it, "title", iid)),
            epic=str(_req(it, "epic", iid)),
            type=str(_req(it, "type", iid)),
            area=str(_req(it, "area", iid)),
            owners=[str(o) for o in _req(it, "owners", iid)],
            reviewers=[str(r) for r in it.get("reviewers") or []],
            sprint=str(_req(it, "sprint", iid)),
            phase=it.get("phase"),
            points=int(_req(it, "points", iid)),
            priority=str(_req(it, "priority", iid)),
            depends_on=[str(d) for d in it.get("depends_on") or []],
            goal=str(it.get("goal", "")).strip(),
            acceptance=[str(a) for a in it.get("acceptance") or []],
            notes=str(it.get("notes") or "").strip(),
            prereg={str(k): str(v).strip() for k, v in prereg.items()}
            if isinstance(prereg, dict)
            else None,
            decision=str(it["decision"]) if it.get("decision") else None,
            adr=str(it["adr"]) if it.get("adr") else None,
            no_experiment_required=bool(it.get("no_experiment_required", False)),
            index=n,
        )

    return Backlog(
        path=path,
        project=_req(raw, "project", "raíz"),
        capacity=raw.get("capacity") or {},
        team=team,
        sprints=sprints,
        label_types=label_types,
        label_areas=label_areas,
        label_priorities=label_prios,
        label_extra=label_extra,
        board=raw.get("board") or {},
        epics=epics,
        items=items,
        risks=raw.get("risks") or [],
        scope_cut=[str(s) for s in raw.get("scope_cut") or []],
        duplicates=[k for k, c in seen.items() if c > 1],
    )


def repo_from_remote(root: Path = REPO_ROOT) -> str | None:
    """«usuario/repo» del remoto origin, sin llamar a GitHub."""
    try:
        url = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        return None
    match = REMOTE_RE.search(url)
    return f"{match.group(1)}/{match.group(2)}" if match else None


# ---------------------------------------------------------------------------
# Numeración: tarea → número de issue
# ---------------------------------------------------------------------------


class Numbering:
    """Traduce identificadores del backlog a números de issue de GitHub."""

    def __init__(self, mapping: dict[str, int]) -> None:
        self.map = dict(mapping)

    @classmethod
    def planned(cls, b: Backlog, start: int = 1) -> Numbering:
        """Numeración esperada si se crean en orden a partir de ``start``."""
        return cls({e.id: start + n for n, e in enumerate(b.creation_order())})

    def ref(self, iid: str) -> str:
        num = self.map.get(iid)
        return f"#{num}" if num else f"`{iid}`"

    def text(self, value: str) -> str:
        """Reescribe «#N» (tarea N del backlog) con su número real de issue."""

        def sub(m: re.Match[str]) -> str:
            num = self.map.get(f"{int(m.group(1)):02d}")
            return f"#{num}" if num else m.group(0)

        return REF_RE.sub(sub, value)


# ---------------------------------------------------------------------------
# Análisis: orden, carga, reparto, revisiones, ruta crítica
# ---------------------------------------------------------------------------


def topo_order(b: Backlog) -> list[Item]:
    """Orden topológico; empates por sprint, fase, profundidad y posición en el YAML."""
    depth: dict[str, int] = {}

    def depth_of(iid: str, stack: tuple[str, ...] = ()) -> int:
        if iid in stack:  # ciclo: lo informa validate()
            return 0
        if iid not in depth:
            deps = [d for d in b.items[iid].depends_on if d in b.items]
            depth[iid] = 1 + max((depth_of(d, (*stack, iid)) for d in deps), default=-1)
        return depth[iid]

    def key(iid: str) -> tuple[int, int, int, int]:
        it = b.items[iid]
        sprint = b.sprints[it.sprint].order if it.sprint in b.sprints else 99
        return (sprint, PHASE_RANK.get(it.phase, 1), depth_of(iid), it.index)

    indeg = {i: 0 for i in b.items}
    rev: dict[str, list[str]] = defaultdict(list)
    for item in b.items.values():
        for dep in item.depends_on:
            if dep in b.items:
                indeg[item.id] += 1
                rev[dep].append(item.id)
    ready = sorted((i for i, d in indeg.items() if d == 0), key=key)
    out: list[Item] = []
    while ready:
        cur = ready.pop(0)
        out.append(b.items[cur])
        for nxt in rev[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
        ready.sort(key=key)
    return out


def find_cycles(b: Backlog) -> list[list[str]]:
    color: dict[str, int] = dict.fromkeys(b.items, 0)
    stack: list[str] = []
    cycles: list[list[str]] = []

    def dfs(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for dep in b.items[node].depends_on:
            if dep not in b.items:
                continue
            if color[dep] == 1:
                cycles.append([*stack[stack.index(dep) :], dep])
            elif color[dep] == 0:
                dfs(dep)
        stack.pop()
        color[node] = 2

    for node in b.items:
        if color[node] == 0:
            dfs(node)
    return cycles


def load_matrix(
    b: Backlog, priorities: tuple[str, ...] = ("P0", "P1")
) -> dict[str, dict[str, float]]:
    """Puntos por persona y sprint; las tareas compartidas se reparten a partes iguales."""
    load: dict[str, dict[str, float]] = {
        s: dict.fromkeys(b.team, 0.0) for s in b.sprints
    }
    for item in b.items.values():
        if item.priority not in priorities or item.sprint not in load:
            continue
        share = item.points / len(item.owners)
        for owner in item.owners:
            if owner in b.team:
                load[item.sprint][owner] += share
    return load


def area_matrix(b: Backlog) -> dict[str, dict[str, int]]:
    """Puntos por área y persona en tareas individuales (todas las prioridades)."""
    out: dict[str, dict[str, int]] = {
        a: dict.fromkeys(b.team, 0) for a in b.label_areas
    }
    for item in b.items.values():
        if len(item.owners) == 1 and item.area in out and item.lead in b.team:
            out[item.area][item.lead] += item.points
    return out


def review_matrix(b: Backlog) -> dict[tuple[str, str], int]:
    """(revisor, autor) → nº de PRs que el revisor aprueba de ese autor."""
    pairs: Counter[tuple[str, str]] = Counter()
    for item in b.items.values():
        for rev in item.reviewers:
            pairs[(rev, item.lead)] += 1
    return dict(pairs)


def review_counts(b: Backlog) -> dict[str, int]:
    counts = dict.fromkeys(b.team, 0)
    for item in b.items.values():
        for rev in item.reviewers:
            if rev in counts:
                counts[rev] += 1
    return counts


def critical_path(
    b: Backlog, priorities: tuple[str, ...] = ("P0", "P1")
) -> tuple[list[Item], int]:
    """Camino más largo (en puntos) hasta la entrega; excluye ceremonias, la defensa y lo posterior."""
    target = b.project.get("delivery_target")
    best: dict[str, tuple[int, str | None]] = {}
    for item in topo_order(b):
        sprint = b.sprints[item.sprint]
        if (
            item.priority not in priorities
            or item.type == "gestion"
            or sprint.end is None
            or (isinstance(target, dt.date) and sprint.start > target)
        ):
            continue
        prev = [(best[d][0], d) for d in item.depends_on if d in best]
        top = max(prev, default=(0, None))
        best[item.id] = (top[0] + item.points, top[1])
    if not best:
        return [], 0
    end = max(best, key=lambda k: best[k][0])
    total = best[end][0]
    chain: list[Item] = []
    cur: str | None = end
    while cur:
        chain.append(b.items[cur])
        cur = best[cur][1]
    return list(reversed(chain)), total


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


def validate(b: Backlog) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    team = set(b.team)

    for dup in b.duplicates:
        errors.append(f"ID duplicado: {dup}")
    for sprint in b.sprints.values():
        if sprint.end and sprint.end < sprint.start:
            errors.append(f"{sprint.id}: termina antes de empezar")
    ordered = sorted(b.sprints.values(), key=lambda s: s.order)
    for prev, nxt in pairwise(ordered):
        if prev.end and nxt.start <= prev.end:
            errors.append(f"{nxt.id} empieza antes de que termine {prev.id}")
    if b.board.get("current_sprint") not in b.sprints:
        errors.append("board.current_sprint no es un sprint válido")
    for epic in b.epics.values():
        if epic.area not in b.label_areas:
            errors.append(f"{epic.id}: área «{epic.area}» sin label")
        if epic.id.isdigit():
            errors.append(
                f"Épica {epic.id}: su ID no puede ser numérico (se confundiría con una tarea)"
            )
    if "epica" not in b.label_types:
        errors.append("labels.types necesita la clave «epica»")
    numbers = [i.number for i in b.items.values()]
    if sorted(numbers) != list(range(1, len(numbers) + 1)):
        errors.append(
            "Las tareas deben estar numeradas 01, 02… sin huecos ni repeticiones"
        )

    adr_numbers: Counter[str] = Counter()
    for it in b.items.values():
        p = it.id
        if not it.id.isdigit():
            errors.append(f"{p}: el ID de una tarea debe ser su número (p. ej. «07»)")
        if it.epic not in b.epics:
            errors.append(f"{p}: épica «{it.epic}» inexistente")
        if it.sprint not in b.sprints:
            errors.append(f"{p}: sprint «{it.sprint}» inexistente")
        if it.type not in b.label_types:
            errors.append(f"{p}: tipo «{it.type}» sin label")
        if it.area not in b.label_areas:
            errors.append(f"{p}: área «{it.area}» sin label")
        if it.priority not in PRIORITIES or it.priority not in b.label_priorities:
            errors.append(f"{p}: prioridad «{it.priority}» no válida")
        if it.phase not in PHASE_RANK:
            errors.append(f"{p}: fase «{it.phase}» no válida (start, end o vacía)")
        if not it.owners:
            errors.append(f"{p}: sin responsable")
        if len(set(it.owners)) != len(it.owners):
            errors.append(f"{p}: responsables repetidos")
        for who in [*it.owners, *it.reviewers]:
            if who not in team:
                errors.append(f"{p}: «{who}» no está en team")
        if not it.reviewers:
            errors.append(f"{p}: sin revisor")
        if it.owners and it.lead in it.reviewers:
            errors.append(
                f"{p}: quien hace la tarea ({it.lead}) no puede revisar su propia PR"
            )
        if it.points not in FIBONACCI:
            errors.append(
                f"{p}: {it.points} puntos (usa Fibonacci: {sorted(FIBONACCI)})"
            )
        if len(it.title) > 200:
            errors.append(f"{p}: título demasiado largo")
        if not it.goal:
            warnings.append(f"{p}: sin objetivo")

        for dep in it.depends_on:
            if dep == it.id:
                errors.append(f"{p}: depende de sí misma")
                continue
            if dep not in b.items:
                errors.append(f"{p}: dependencia «{dep}» inexistente")
                continue
            d = b.items[dep]
            if d.number >= it.number:
                errors.append(
                    f"{p} depende de {dep}: una tarea solo puede depender de tareas con número menor"
                )
            if (
                it.sprint in b.sprints
                and d.sprint in b.sprints
                and b.sprints[d.sprint].order > b.sprints[it.sprint].order
            ):
                errors.append(
                    f"{p} ({it.sprint}) depende de {dep}, planificada más tarde ({d.sprint})"
                )
            if d.priority == "P2" and it.priority == "P0":
                errors.append(f"{p} (P0) no puede depender de {dep} (P2)")
            elif d.priority == "P2" and it.priority == "P1":
                warnings.append(f"{p} (P1) depende de {dep} (P2)")

        if it.type == "experimento":
            if not it.decision or it.decision not in b.items:
                errors.append(f"{p}: experimento sin decisión válida (campo decision)")
            else:
                dec = b.items[it.decision]
                if dec.type != "decision":
                    errors.append(f"{p}: «{it.decision}» no es de tipo decision")
                elif dec.number < it.number:
                    errors.append(f"{p} alimenta a {dec.id}, que va antes")
            missing = [k for k in PREREG_FIELDS if not (it.prereg or {}).get(k)]
            if missing:
                errors.append(f"{p}: pre-registro incompleto ({', '.join(missing)})")
        elif len(it.acceptance) < 2 and it.type != "decision":
            errors.append(f"{p}: necesita ≥2 criterios de aceptación")

        if it.type == "decision":
            if not it.adr or not ADR_RE.match(it.adr):
                errors.append(f"{p}: ADR no válido (formato ADR-NNN-slug)")
            else:
                adr_numbers[it.adr[:7]] += 1
            if set(it.owners) != team:
                errors.append(f"{p}: en una decisión participan los tres")
            if set(it.reviewers) != team - {it.lead}:
                errors.append(f"{p}: el ADR lo aprueban los otros dos")
            if not it.no_experiment_required and not b.experiments_for(it.id):
                errors.append(f"{p}: ningún experimento alimenta esta decisión")

    for adr, count in adr_numbers.items():
        if count > 1:
            errors.append(f"{adr} usado por {count} decisiones")
    for cycle in find_cycles(b):
        errors.append("Ciclo de dependencias: " + " → ".join(cycle))
    for risk in b.risks:
        if risk.get("owner") not in team:
            errors.append(f"Riesgo {risk.get('id')}: dueño no válido")

    # Reparto: carga por sprint, todas las áreas para todos y revisiones rotatorias.
    limit = b.capacity_points + 2
    for sprint_id, row in load_matrix(b).items():
        if b.sprints[sprint_id].end is None:
            continue
        for who, pts in row.items():
            if pts > limit:
                warnings.append(
                    f"{sprint_id}: {b.team[who].name} tiene {pts:.1f} pts P0+P1 (capacidad {b.capacity_points:.0f})"
                )
    areas = area_matrix(b)
    for area in COVERAGE_AREAS:
        total = sum(areas.get(area, {}).values())
        for who, pts in areas.get(area, {}).items():
            if total and pts < 0.2 * total:
                warnings.append(
                    f"{b.team[who].name} apenas trabaja en el área {area} ({pts} de {total} pts)"
                )
    pairs = review_matrix(b)
    for rev in team:
        for author in team - {rev}:
            if not pairs.get((rev, author)):
                warnings.append(
                    f"{b.team[rev].name} nunca revisa a {b.team[author].name}"
                )
    last: dict[str, tuple[str, int]] = {}
    for it in sorted(b.items.values(), key=lambda i: i.number):
        if len(it.reviewers) != 1:
            continue
        prev = last.get(it.lead)
        streak = prev[1] + 1 if prev and prev[0] == it.reviewers[0] else 1
        last[it.lead] = (it.reviewers[0], streak)
        if streak == 4:
            warnings.append(
                f"{b.team[it.lead].name} tiene al mismo revisor 4 veces seguidas (hasta {it.id})"
            )
    return errors, warnings


# ---------------------------------------------------------------------------
# Cuerpos de los issues
# ---------------------------------------------------------------------------


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def reviewers_text(b: Backlog, item: Item) -> str:
    return " y ".join(b.handles(item.reviewers)) or "—"


def render_item_body(b: Backlog, item: Item, nums: Numbering) -> str:
    sprint = b.sprints[item.sprint]
    epic = b.epics[item.epic]
    owners = b.handles(item.owners)
    who = f"**{owners[0]}**" + (
        f" con {', '.join(owners[1:])}" if len(owners) > 1 else ""
    )
    t = nums.text
    lines = [MARKER.format(id=item.id), ""]
    lines += [
        "| Tarea | Sprint | Puntos | Prioridad | La hace | La revisa | Épica |",
        "|:-:|:--|:-:|:-:|:--|:--|:--|",
        (
            f"| **{item.id}** | {_cell(sprint.title)} ({b.sprint_range(sprint)}) | {item.points} | "
            f"{item.priority} | {who} | {' y '.join(b.handles(item.reviewers))} | "
            f"{nums.ref(epic.id)} {_cell(epic.title)} |"
        ),
        "",
    ]
    if item.depends_on:
        lines.append(
            "**⛔ Antes de empezar deben estar terminadas:** "
            + " · ".join(
                nums.ref(d)
                for d in sorted(item.depends_on, key=lambda d: b.items[d].number)
            )
        )
    else:
        lines.append(
            "**✅ Sin requisitos previos:** se puede empezar en cuanto arranque su sprint."
        )
    unlocks = b.successors(item.id)
    if unlocks:
        lines.append("**➡️ Desbloquea:** " + " · ".join(nums.ref(s.id) for s in unlocks))
    lines.append("")

    if item.type == "decision":
        exps = b.experiments_for(item.id)
        lines.append(
            f"**Participan:** los tres — **Presenta:** {b.team[item.lead].handle} — **ADR:** `doc/adr/{item.adr}.md`"
        )
        if exps:
            lines.append("**Evidencias:** " + " · ".join(nums.ref(e.id) for e in exps))
        lines.append("")

    lines += ["### Objetivo", t(item.goal) or "—", ""]

    acceptance = [t(a) for a in item.acceptance]
    if item.type == "experimento" and item.decision:
        dec = b.items[item.decision]
        acceptance = [
            a.format(adr=dec.adr, dec=nums.ref(dec.id)) for a in EXP_ACCEPTANCE
        ] + acceptance
    elif item.type == "decision":
        inputs = [
            e.id for e in b.experiments_for(item.id) if e.priority != "P2"
        ] or item.depends_on
        generic = [
            a.format(
                adr=item.adr,
                inputs=", ".join(nums.ref(i) for i in inputs) or "las de esta tarea",
            )
            for a in DEC_ACCEPTANCE
        ]
        acceptance = generic[:3] + acceptance + generic[3:]
    lines.append("### Criterios de aceptación")
    lines += [f"- [ ] {a}" for a in acceptance]
    lines.append("")

    if item.type == "experimento" and item.prereg:
        dec = b.items.get(item.decision or "")
        lines += [
            "### Pre-registro (fijar antes de ejecutar)",
            "| Campo | Valor |",
            "|:--|:--|",
        ]
        for key, label in PREREG_FIELDS.items():
            lines.append(f"| {label} | {_cell(t(item.prereg.get(key, '—')))} |")
        if dec:
            lines.append(f"| Alimenta a | {nums.ref(dec.id)} → `{dec.adr}` |")
        lines += [
            "",
            "### Resultados (rellenar al terminar)",
            "| Opción | Rendimiento (IC95) | Latencia p50 / p95 | Energía | RAM / VRAM pico | Notas |",
            "|:--|:--|:--|:--|:--|:--|",
            "|  |  |  |  |  |  |",
            "",
        ]
    if item.type == "decision":
        lines.append("### Orden del día (≤45 min)")
        lines += [f"{n}. {step}" for n, step in enumerate(DEC_AGENDA, 1)]
        lines.append("")

    dod = [*DOD.get(item.type, DOD["default"]), PUBLIC_DOD]
    lines.append("### Definition of Done")
    lines += [
        "- [ ] "
        + d.format(
            reviewers=reviewers_text(b, item), id=item.id, self=nums.ref(item.id)
        )
        for d in dod
    ]
    lines.append("")
    if item.notes:
        lines += ["### Notas", t(item.notes), ""]
    lines.append(
        "<sub>Generado desde `gestion/backlog.yml` (línea base). Proceso, DoR y DoD en `CONTRIBUTING.md`. "
        "Este issue es ahora la fuente de verdad: edítalo libremente.</sub>"
    )
    return "\n".join(lines)


def render_children(b: Backlog, epic: Epic, nums: Numbering) -> str:
    rows = [
        CHILDREN_START,
        "| Tarea | Título | Sprint | La hace | Pts | Prio | Requiere |",
        "|:-:|:--|:-:|:--|:-:|:-:|:--|",
    ]
    for child in b.children(epic.id):
        deps = ", ".join(nums.ref(d) for d in child.depends_on) or "—"
        rows.append(
            f"| {nums.ref(child.id)} | {_cell(child.title)} | {child.sprint} | "
            f"{b.team[child.lead].handle} | {child.points} | {child.priority} | {deps} |"
        )
    rows.append(CHILDREN_END)
    return "\n".join(rows)


def render_epic_body(b: Backlog, epic: Epic, nums: Numbering) -> str:
    kids = b.children(epic.id)
    points = sum(k.points for k in kids)
    must = sum(k.points for k in kids if k.priority == "P0")
    return "\n".join(
        [
            MARKER.format(id=epic.id),
            "",
            "### Objetivo",
            epic.goal or "—",
            "",
            f"**Alcance:** {len(kids)} tareas · {points} puntos ({must} de P0).",
            "",
            "### Tareas de la épica",
            render_children(b, epic, nums),
            "",
            "<sub>Generado desde `gestion/backlog.yml`. El progreso se ve en los sub-issues.</sub>",
        ]
    )


def replace_children(body: str, block: str) -> str:
    pattern = re.compile(
        re.escape(CHILDREN_START) + r".*?" + re.escape(CHILDREN_END), re.DOTALL
    )
    if pattern.search(body):
        return pattern.sub(lambda _: block, body)
    return body.rstrip() + "\n\n" + block + "\n"


# ---------------------------------------------------------------------------
# Informe (ROADMAP.md)
# ---------------------------------------------------------------------------


def _mid(iid: str) -> str:
    return "T" + re.sub(r"[^A-Za-z0-9]", "_", iid)


def _gantt_name(text: str) -> str:
    """Mermaid gantt usa «:» como separador y no tolera comas ni «#» en el nombre."""
    return re.sub(r"\s+", " ", re.sub(r"[:;,#]", " ", text)).strip()


def _short(text: str, limit: int = 40) -> str:
    """Etiqueta corta para diagramas: quita prefijos repetidos y corta por palabra."""
    for prefix in ("Decisión: ", "Benchmark de "):
        text = text.removeprefix(prefix)
    text = (text[:1].upper() + text[1:]).replace('"', "'")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,:;(") + "…"


def render_report(b: Backlog) -> str:
    sprints = sorted(b.sprints.values(), key=lambda s: s.order)
    load = load_matrix(b)
    load_p2 = load_matrix(b, ("P2",))
    areas = area_matrix(b)
    pairs = review_matrix(b)
    reviews = review_counts(b)
    chain, chain_pts = critical_path(b)
    total = {
        p: sum(i.points for i in b.items.values() if i.priority == p)
        for p in PRIORITIES
    }
    names = {k: m.name for k, m in b.team.items()}
    out: list[str] = []
    w = out.append

    w("# Roadmap del proyecto · TAPLN (TA1C)")
    w("")
    w(
        "> Generado con `uv run --with pyyaml python gestion/bootstrap_github.py report -o gestion/ROADMAP.md`. "
        "No editar a mano: se cambia `gestion/backlog.yml` y se regenera. Es la **línea base** del plan; "
        "el estado vivo está en el GitHub Project."
    )
    w("")
    w(
        f"**Entrega:** {b.project.get('delivery_deadline', '—')} · **Objetivo interno:** "
        f"{b.project.get('delivery_target', '—')} · **Alcance:** {len(b.items)} tareas · "
        f"{total['P0']} pts P0 + {total['P1']} pts P1 + {total['P2']} pts P2 (ampliaciones)."
    )
    w("")
    w("## Cómo leer este plan")
    w("")
    w(
        "- **Tareas numeradas en orden de ejecución.** Una tarea solo puede empezar cuando están terminadas las de "
        "su columna *Requiere*, que siempre tienen un número menor. En el repo, la tarea N es el issue #N."
    )
    if b.project.get("numbering_note"):
        w(f"- **Numeración en GitHub.** {b.project['numbering_note']}")
    w(
        "- **Reparto rotatorio.** Los tres trabajan en todas las áreas (T1, T2, plataforma y evaluación, memoria) "
        "y la carga por sprint está equilibrada. Las decisiones se toman entre los tres."
    )
    w(
        "- **Revisiones rotatorias.** El revisor cambia de una tarea a otra, y cada uno revisa a los otros dos más o "
        "menos lo mismo. Cuando se puede, revisa quien hará la tarea siguiente que depende de esa, para que llegue "
        "conociendo el código."
    )
    w(
        "- **Portavoz** (lo exige el enunciado): "
        + ", ".join(m.handle for m in b.team.values() if m.role)
        + "."
    )
    w("")

    w("## Calendario")
    w("")
    w("| Sprint | Fechas | Tareas | Objetivo | Puntos P0+P1 |")
    w("|:--|:--|:-:|:--|:-:|")
    for s in sprints:
        nums = [i.number for i in b.items.values() if i.sprint == s.id]
        pts = sum(
            i.points
            for i in b.items.values()
            if i.sprint == s.id and i.priority != "P2"
        )
        rng = f"{min(nums):02d}–{max(nums):02d}" if nums else "—"
        w(f"| **{s.title}** | {b.sprint_range(s)} | {rng} | {_cell(s.goal)} | {pts} |")
    w("")
    w("```mermaid")
    w("gantt")
    w("    title Calendario TAPLN")
    w("    dateFormat YYYY-MM-DD")
    w("    axisFormat %d-%m")
    w("    section Sprints")
    for s in sprints:
        if s.end:
            end = (s.end + dt.timedelta(days=1)).isoformat()
            w(f"    {_gantt_name(s.title)} :S{s.order}, {s.start.isoformat()}, {end}")
    w("    section Épicas")
    for e in b.epics.values():
        kids = [k for k in b.children(e.id) if b.sprints[k.sprint].end]
        if not kids:
            continue
        start = min(b.sprints[k.sprint].start for k in kids)
        end = max(b.sprints[k.sprint].end for k in kids) + dt.timedelta(days=1)
        w(
            f"    {e.id} {_gantt_name(e.title)} :{e.id}, {start.isoformat()}, {end.isoformat()}"
        )
    w("    section Hitos")
    target = b.project.get("delivery_target")
    if target:
        w(
            f"    Entrega objetivo :milestone, hito1, {as_date(target, 'delivery_target').isoformat()}, 0d"
        )
    w("    Plazo de entrega :milestone, hito2, 2026-12-18, 0d")
    w("```")
    w("")

    w("## Reparto")
    w("")
    w(
        f"**Carga por sprint** (puntos P0+P1; las tareas compartidas se reparten a partes iguales; entre paréntesis, "
        f"las ampliaciones P2). Capacidad de referencia: **{b.capacity_points:.0f} pts/persona/sprint** "
        f"(1 pt ≈ {b.capacity.get('hours_per_point', 2)} h)."
    )
    w("")
    w("| Sprint | " + " | ".join(names.values()) + " | Total |")
    w("|:--|" + ":-:|" * (len(b.team) + 1))
    for s in sprints:
        row = load[s.id]
        cells = []
        for key in b.team:
            extra = load_p2[s.id][key]
            flag = " ⚠️" if row[key] > b.capacity_points + 2 and s.end else ""
            cells.append(
                f"{row[key]:.1f}{flag}" + (f" (+{extra:.1f})" if extra else "")
            )
        w(f"| {s.id} | " + " | ".join(cells) + f" | {sum(row.values()):.1f} |")
    totals = {k: sum(load[s][k] for s in load) for k in b.team}
    w(
        "| **Total** | "
        + " | ".join(f"**{v:.1f}**" for v in totals.values())
        + f" | **{sum(totals.values()):.1f}** |"
    )
    w("")
    w("**Todos en todas las áreas** (puntos de tareas individuales, incluidas las P2):")
    w("")
    w("| Área | " + " | ".join(names.values()) + " |")
    w("|:--|" + ":-:|" * len(b.team))
    for area, spec in b.label_areas.items():
        row = areas.get(area, {})
        if sum(row.values()):
            w(
                f"| {spec.name.removeprefix('area:')} | "
                + " | ".join(str(row[k]) for k in b.team)
                + " |"
            )
    w("")
    w(
        "**Quién revisa a quién** (nº de PRs que aprueba cada revisor de cada autor; los ADR los aprueban los otros dos):"
    )
    w("")
    w("| Revisor ↓ · Autor → | " + " | ".join(names.values()) + " | Total |")
    w("|:--|" + ":-:|" * (len(b.team) + 1))
    for rev in b.team:
        cells = ["—" if rev == a else str(pairs.get((rev, a), 0)) for a in b.team]
        w(f"| {names[rev]} | " + " | ".join(cells) + f" | {reviews[rev]} |")
    w("")

    w("## Lista de tareas")
    w("")
    w(
        "*Requiere* = tareas que deben estar **terminadas** antes de empezar. *Desbloquea* = tareas que esperan a esta. "
        "En las compartidas, el nombre en negrita abre la PR o redacta el acta."
    )
    for s in sprints:
        w("")
        w(f"### {s.title} ({b.sprint_range(s)})")
        w("")
        w(f"_{s.goal}_")
        w("")
        w("| Nº | Tarea | La hace | La revisa | Pts | Prio | Requiere | Desbloquea |")
        w("|:-:|:--|:--|:--|:-:|:-:|:--|:--|")
        for it in sorted(
            (i for i in b.items.values() if i.sprint == s.id), key=lambda i: i.number
        ):
            names_own = [names[o] for o in it.owners]
            owners = (
                "Los tres" if len(names_own) == len(b.team) else ", ".join(names_own)
            )
            if len(names_own) > 1:
                owners += f" (**{names_own[0]}**)"
            revs = ", ".join(names[r] for r in it.reviewers)
            deps = ", ".join(sorted(it.depends_on, key=int)) or "—"
            unlocks = ", ".join(x.id for x in b.successors(it.id)) or "—"
            w(
                f"| **{it.id}** | {_cell(it.title)} | {owners} | {revs} | {it.points} | {it.priority} | "
                f"{deps} | {unlocks} |"
            )
    w("")

    w("## Mapa de decisiones (experimentos → decisión → ADR)")
    w("")
    w(
        "Cada decisión de modelo o arquitectura se toma con experimentos pre-registrados que miden **rendimiento y "
        "recursos** (tiempo, latencia, memoria, energía), en una reunión de los tres, y queda en un ADR que pasa a la "
        "memoria."
    )
    w("")
    w("| Decisión | ADR | Sprint | Experimentos (quién los hace) | Presenta | Prio |")
    w("|:--|:--|:-:|:--|:--|:-:|")
    decisions = sorted(
        (i for i in b.items.values() if i.type == "decision"), key=lambda i: i.number
    )
    for d in decisions:
        exps = b.experiments_for(d.id)
        ev = ", ".join(
            f"**{e.id}** ({names[e.lead]}{', P2' if e.priority == 'P2' else ''})"
            for e in exps
        ) or ", ".join(f"**{x}**" for x in d.depends_on)
        w(
            f"| **{d.id}** {_cell(d.title.removeprefix('Decisión: '))} | `{d.adr}` | {d.sprint} | {ev} | "
            f"{names[d.lead]} | {d.priority} |"
        )
    w("")
    w("```mermaid")
    w("flowchart LR")
    shown: set[str] = set()
    for d in decisions:
        shown.add(d.id)
        shown.update(e.id for e in b.experiments_for(d.id))
        shown.update(d.depends_on)
    shown = {s for s in shown if b.sprints[b.items[s].sprint].end is not None}
    for iid in sorted(shown, key=lambda x: b.items[x].number):
        it = b.items[iid]
        label = f"{iid}<br/>{_short(it.title)}"
        w(
            f'    {_mid(iid)}{{{{"{label}"}}}}'
            if it.type == "decision"
            else f'    {_mid(iid)}["{label}"]'
        )
    edges: list[str] = []
    for d in decisions:
        for e in b.experiments_for(d.id):
            if e.id not in d.depends_on:
                edges.append(f"    {_mid(e.id)} -.-> {_mid(d.id)}")
    for iid in sorted(shown, key=lambda x: b.items[x].number):
        for dep in b.items[iid].depends_on:
            if dep in shown:
                edges.append(f"    {_mid(dep)} --> {_mid(iid)}")
    for edge in dict.fromkeys(edges):
        w(edge)
    w("    classDef dec fill:#fde2d8,stroke:#d93f0b,color:#000")
    w("    classDef opt stroke-dasharray:4 3")
    w("    class " + ",".join(_mid(d.id) for d in decisions if d.id in shown) + " dec")
    optional = [_mid(i) for i in sorted(shown) if b.items[i].priority == "P2"]
    if optional:
        w("    class " + ",".join(optional) + " opt")
    w("```")
    w("")

    w("## Ruta crítica")
    w("")
    w(
        f"Es la cadena de dependencias más larga ({chain_pts} pts P0+P1): si se retrasa cualquiera de estas tareas, se "
        "retrasa la entrega. Sus PRs se revisan en ≤24 h."
    )
    w("")
    w(" → ".join(f"**{i.id}**" for i in chain))
    w("")

    w("## Registro de riesgos")
    w("")
    w("| ID | Riesgo | Prob. | Impacto | Mitigación | Vigila |")
    w("|:--|:--|:-:|:-:|:--|:--|")
    for r in b.risks:
        owner = names.get(r.get("owner"), "—")
        w(
            f"| {r.get('id')} | {_cell(r.get('risk', ''))} | {r.get('probability', '')} | "
            f"{r.get('impact', '')} | {_cell(r.get('mitigation', ''))} | {owner} |"
        )
    w("")
    w("## Orden de recorte de alcance")
    w("")
    w(
        "Si la velocity real queda por debajo de lo planificado, se recorta en este orden (los P0 no se recortan):"
    )
    w("")
    for step in b.scope_cut:
        w(f"- {step}")
    w("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Cliente de GitHub (gh)
# ---------------------------------------------------------------------------


class GhError(RuntimeError):
    def __init__(
        self, cmd: list[str], returncode: int, stdout: str, stderr: str
    ) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        detail = (stderr or stdout).strip().splitlines()
        super().__init__(
            f"gh {' '.join(cmd[1:3])}… falló ({returncode}): {detail[-1] if detail else 'sin detalle'}"
        )


class GH:
    """Envoltorio mínimo de GitHub CLI con reintentos ante límites de peticiones."""

    WRITE_METHODS: ClassVar[frozenset[str]] = frozenset(
        {"POST", "PATCH", "PUT", "DELETE"}
    )

    def __init__(
        self,
        gh_bin: str = "gh",
        throttle: float = 0.8,
        verbose: bool = False,
        cwd: Path = REPO_ROOT,
    ) -> None:
        self.bin = gh_bin
        self.throttle = throttle
        self.verbose = verbose
        self.cwd = cwd
        self.calls = 0
        self.writes = 0

    def run(
        self,
        *args: str,
        stdin: str | None = None,
        check: bool = True,
        write: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [self.bin, *args]
        for attempt in range(4):
            if self.verbose:
                print(
                    "    $ gh " + " ".join(shlex.quote(a) for a in args),
                    file=sys.stderr,
                )
            proc = subprocess.run(
                cmd,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                cwd=self.cwd,
            )
            self.calls += 1
            text = f"{proc.stdout}\n{proc.stderr}".lower()
            limited = (
                "secondary rate limit" in text
                or "abuse detection" in text
                or "api rate limit exceeded" in text
            )
            if proc.returncode != 0 and limited:
                if attempt == 3:
                    raise SystemExit(
                        "GitHub sigue limitando las peticiones. Espera ~1 h y relanza `apply`: "
                        "es idempotente y continúa donde lo dejó."
                    )
                wait = 60 * 2**attempt
                print(
                    f"    · Límite de peticiones de GitHub: espero {wait} s y reintento…",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            break
        if write:
            self.writes += 1
            if self.throttle:
                time.sleep(self.throttle)
        if check and proc.returncode != 0:
            raise GhError(cmd, proc.returncode, proc.stdout, proc.stderr)
        return proc

    def out(self, *args: str, write: bool = False) -> str:
        return self.run(*args, write=write).stdout

    def json(self, *args: str, write: bool = False) -> Any:
        text = self.out(*args, write=write)
        return json.loads(text) if text.strip() else None

    def api(
        self,
        path: str,
        method: str = "GET",
        body: Any | None = None,
        headers: dict[str, str] | None = None,
        check: bool = True,
    ) -> Any:
        args = ["api", path, "--method", method]
        for key, value in (headers or {}).items():
            args += ["-H", f"{key}: {value}"]
        payload = None
        if body is not None:
            args += ["--input", "-"]
            payload = json.dumps(body, ensure_ascii=False)
        proc = self.run(
            *args, stdin=payload, check=check, write=method in self.WRITE_METHODS
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def graphql(
        self, query: str, variables: dict[str, Any] | None = None, write: bool = True
    ) -> dict[str, Any]:
        payload = json.dumps(
            {"query": query, "variables": variables or {}}, ensure_ascii=False
        )
        proc = self.run("api", "graphql", "--input", "-", stdin=payload, write=write)
        data = json.loads(proc.stdout or "{}")
        if data.get("errors"):
            raise GhError(
                [self.bin, "api", "graphql"],
                1,
                proc.stdout,
                json.dumps(data["errors"], ensure_ascii=False),
            )
        return data.get("data") or {}


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


@dataclass
class ApplyOptions:
    invite: bool = False
    skip_project: bool = False
    skip_views: bool = False
    skip_dependencies: bool = False
    repo_settings: bool = False
    protect_branches: bool = False
    update_bodies: bool = False
    force_fields: bool = False


class Applier:
    def __init__(self, b: Backlog, gh: GH, opts: ApplyOptions) -> None:
        self.b = b
        self.gh = gh
        self.opts = opts
        self.repo = ""
        self.owner = ""
        self.warnings: list[str] = []
        self.manual: list[str] = []
        self.created: Counter[str] = Counter()
        self.nums = Numbering({})
        self.rest_ids: dict[str, int] = {}
        self.urls: dict[str, str] = {}
        self.existing: dict[str, dict[str, Any]] = {}
        self.created_now: list[str] = []
        self.shifted = False
        self.milestones: dict[str, int] = {}
        self.assignable: set[str] = set()
        self.owner_id: int | None = None
        self.owner_type = "User"
        self.repo_node_id = ""
        self.project_number: int | None = None
        self.project_id: str | None = None
        self.project_url = ""
        self.fields: dict[str, dict[str, Any]] = {}
        self.order = b.creation_order()

    # -- utilidades -----------------------------------------------------

    def step(self, title: str) -> None:
        print(f"\n▶ {title}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"  ⚠ {msg}")

    def rest_id(self, iid: str) -> int:
        if iid not in self.rest_ids:
            data = self.gh.api(f"repos/{self.repo}/issues/{self.nums.map[iid]}")
            self.rest_ids[iid] = int(data["id"])
        return self.rest_ids[iid]

    # -- pasos ------------------------------------------------------------

    def preflight(self) -> None:
        self.step("Comprobaciones previas")
        if not shutil.which(self.gh.bin) and not Path(self.gh.bin).exists():
            raise SystemExit(
                "No encuentro GitHub CLI (gh). Instálalo: https://cli.github.com/"
            )
        version = self.gh.out("--version")
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
        if match and tuple(int(x) for x in match.groups()[:2]) < MIN_GH_VERSION:
            self.warn(
                f"gh {match.group(0)} es antiguo; se recomienda ≥ {'.'.join(map(str, MIN_GH_VERSION))} (gh upgrade)"
            )
        status = self.gh.run("auth", "status", check=False)
        status_text = status.stdout + status.stderr
        if status.returncode != 0:
            raise SystemExit("gh no está autenticado. Ejecuta: gh auth login")
        scopes = re.search(r"Token scopes:\s*(.*)", status_text)
        if scopes and "project" not in scopes.group(1):
            raise SystemExit(
                "Al token le falta el scope «project». Ejecuta: gh auth refresh -s project"
            )
        viewer = self.gh.api("user")["login"]

        configured = str(self.b.project.get("repo", "auto"))
        if configured == "auto":
            view = self.gh.run("repo", "view", "--json", "nameWithOwner", check=False)
            if view.returncode != 0:
                raise SystemExit(
                    "No encuentro el repositorio: ejecuta el script dentro del clon (con remoto «origin» en GitHub) "
                    "o escribe project.repo en backlog.yml."
                )
            configured = json.loads(view.stdout)["nameWithOwner"]
        self.repo = configured
        repo = self.gh.api(f"repos/{self.repo}")
        perms = repo.get("permissions") or {}
        if not (perms.get("admin") or perms.get("maintain") or perms.get("push")):
            raise SystemExit(f"@{viewer} no tiene permiso de escritura en {self.repo}")
        self.repo_node_id = repo["node_id"]
        owner = str(self.b.project.get("owner", "auto"))
        self.owner = repo["owner"]["login"] if owner == "auto" else owner
        owner_info = self.gh.api(f"users/{self.owner}")
        self.owner_id = int(owner_info["id"])
        self.owner_type = owner_info.get("type", "User")
        if (
            self.owner_type == "User"
            and viewer.lower() != self.owner.lower()
            and not self.opts.skip_project
        ):
            raise SystemExit(
                f"El Project se crea bajo @{self.owner}, pero estás autenticado como @{viewer}. "
                f"Ejecuta el script con la cuenta @{self.owner} o cambia project.owner en backlog.yml."
            )
        print(
            f"  ✓ gh autenticado como @{viewer} · repo {self.repo} · Project de @{self.owner}"
        )
        for m in self.b.team.values():
            check = self.gh.run(
                "api", f"repos/{self.repo}/collaborators/{m.github}", check=False
            )
            if check.returncode == 0:
                self.assignable.add(m.github)
                continue
            if self.opts.invite:
                self.gh.api(
                    f"repos/{self.repo}/collaborators/{m.github}",
                    "PUT",
                    {"permission": "push"},
                    check=False,
                )
                self.warn(
                    f"Invitación enviada a {m.handle}; cuando la acepte, relanza `apply` para asignarle sus tareas"
                )
            else:
                self.warn(
                    f"{m.handle} no es colaborador: sus tareas se crean sin asignar (usa --invite y relanza)"
                )

    def ensure_labels(self) -> None:
        self.step("Labels")
        current = {
            lab["name"]: lab
            for lab in self.gh.json(
                "label",
                "list",
                "--repo",
                self.repo,
                "--limit",
                "500",
                "--json",
                "name,color,description",
            )
            or []
        }
        for spec in self.b.all_labels():
            cur = current.get(spec.name)
            if (
                cur
                and cur.get("color", "").lower() == spec.color.lower()
                and (cur.get("description") or "") == spec.description
            ):
                continue
            self.gh.run(
                "label",
                "create",
                spec.name,
                "--repo",
                self.repo,
                "--color",
                spec.color,
                "--description",
                spec.description,
                "--force",
                write=True,
            )
            self.created["labels"] += 1
            print(f"  + {spec.name}")
        print(f"  ✓ {len(self.b.all_labels())} labels en orden")

    def ensure_milestones(self) -> None:
        self.step("Milestones (sprints)")
        current = {
            m["title"]: m
            for m in self.gh.api(f"repos/{self.repo}/milestones?state=all&per_page=100")
            or []
        }
        for s in sorted(self.b.sprints.values(), key=lambda x: x.order):
            nums = sorted(i.number for i in self.b.items.values() if i.sprint == s.id)
            span = f" · Tareas {nums[0]:02d}–{nums[-1]:02d}" if nums else ""
            description = f"{s.goal}\n\nFechas: {self.b.sprint_range(s)}{span}"
            body: dict[str, Any] = {"title": s.title, "description": description}
            if s.end:
                body["due_on"] = f"{s.end.isoformat()}T08:00:00Z"
            if s.title in current:
                m = current[s.title]
                due = (m.get("due_on") or "")[:10]
                if (m.get("description") or "") != description or due != (
                    s.end.isoformat() if s.end else ""
                ):
                    self.gh.api(
                        f"repos/{self.repo}/milestones/{m['number']}", "PATCH", body
                    )
                self.milestones[s.id] = int(m["number"])
                continue
            m = self.gh.api(f"repos/{self.repo}/milestones", "POST", body)
            self.milestones[s.id] = int(m["number"])
            self.created["milestones"] += 1
            print(f"  + {s.title}")
        print(f"  ✓ {len(self.milestones)} milestones")

    def plan_numbers(self) -> None:
        """Números de issue: los existentes se respetan; los nuevos se predicen en orden."""
        issues = (
            self.gh.json(
                "issue",
                "list",
                "--repo",
                self.repo,
                "--state",
                "all",
                "--limit",
                "1000",
                "--json",
                "number,title,body,url,assignees",
            )
            or []
        )
        for issue in issues:
            match = MARKER_RE.search(issue.get("body") or "")
            if match and match.group(1) not in self.existing:
                self.existing[match.group(1)] = issue
        latest = (
            self.gh.api(
                f"repos/{self.repo}/issues?state=all&per_page=1&sort=created&direction=desc"
            )
            or []
        )
        highest = max([int(latest[0]["number"])] if latest else [0])
        mapping = {iid: int(issue["number"]) for iid, issue in self.existing.items()}
        nxt = highest + 1
        for entity in self.order:
            if entity.id not in mapping:
                mapping[entity.id] = nxt
                nxt += 1
        self.nums = Numbering(mapping)
        if not self.existing:
            if highest == 0:
                print("  ✓ Repo sin issues ni PRs: la tarea N será el issue #N")
            else:
                self.warn(
                    f"El repo ya tiene {highest} issues/PRs: la tarea N será el issue #{highest}+N. "
                    "Los títulos llevan el número de tarea [NN] y las referencias entre issues se ajustan solas."
                )

    def ensure_issues(self) -> None:
        self.step("Tareas y épicas")
        self.plan_numbers()
        for entity in self.order:
            iid = entity.id
            if iid in self.existing:
                ex = self.existing[iid]
                self.urls[iid] = ex["url"]
                if isinstance(entity, Item):
                    want = [
                        self.b.team[o].github
                        for o in entity.owners
                        if self.b.team[o].github in self.assignable
                    ]
                    have = {a["login"].lower() for a in ex.get("assignees") or []}
                    missing = [w for w in want if w.lower() not in have]
                    if missing:
                        self.gh.api(
                            f"repos/{self.repo}/issues/{ex['number']}/assignees",
                            "POST",
                            {"assignees": missing},
                        )
                        print(
                            f"  ~ #{ex['number']} [{iid}]: asignada a {', '.join(missing)}"
                        )
                if self.opts.update_bodies:
                    self.gh.api(
                        f"repos/{self.repo}/issues/{ex['number']}",
                        "PATCH",
                        self.issue_payload(entity),
                    )
                continue
            expected = self.nums.map[iid]
            res = self.gh.api(
                f"repos/{self.repo}/issues", "POST", self.issue_payload(entity)
            )
            actual = int(res["number"])
            self.rest_ids[iid] = int(res["id"])
            self.urls[iid] = res["html_url"]
            self.created_now.append(iid)
            self.created["issues"] += 1
            if (
                actual != expected
            ):  # alguien creó algo a la vez: se corrige la numeración
                shift = actual - expected
                for later in self.order[self.order.index(entity) :]:
                    if later.id not in self.existing:
                        self.nums.map[later.id] += shift
                self.shifted = True
                self.warn(
                    f"[{iid}] ha quedado como #{actual} (se esperaba #{expected}); se corrigen las referencias"
                )
            print(f"  + #{actual} {entity.full_title}")
        if any(
            self.nums.map[i] != int(self.urls[i].rsplit("/", 1)[-1])
            for i in self.created_now
        ):
            raise SystemExit(
                "La numeración de GitHub no cuadra con la prevista; relanza `apply --update-bodies`."
            )
        self.fix_references()
        print(f"  ✓ {len(self.urls)} issues ({self.created['issues']} nuevos)")

    def fix_references(self) -> None:
        """Si la numeración se desplazó durante la creación, reescribe los cuerpos afectados."""
        if not self.shifted:
            return
        for iid in self.created_now:
            entity = self.b.items.get(iid) or self.b.epics[iid]
            self.gh.api(
                f"repos/{self.repo}/issues/{self.nums.map[iid]}",
                "PATCH",
                {"body": self.body(entity)},
            )

    def body(self, entity: Epic | Item) -> str:
        if isinstance(entity, Epic):
            return render_epic_body(self.b, entity, self.nums)
        return render_item_body(self.b, entity, self.nums)

    def issue_payload(self, entity: Epic | Item) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": entity.full_title,
            "body": self.body(entity),
            "labels": self.b.labels_for(entity),
        }
        if isinstance(entity, Item):
            payload["assignees"] = [
                self.b.team[o].github
                for o in entity.owners
                if self.b.team[o].github in self.assignable
            ]
            if entity.sprint in self.milestones:
                payload["milestone"] = self.milestones[entity.sprint]
        return payload

    def update_epics(self) -> None:
        self.step("Índice de cada épica")
        for epic in self.b.epics.values():
            if epic.id in self.created_now:
                continue
            number = self.nums.map[epic.id]
            current = self.gh.api(f"repos/{self.repo}/issues/{number}")
            body = current.get("body") or ""
            new = replace_children(body, render_children(self.b, epic, self.nums))
            if new != body:
                self.gh.api(
                    f"repos/{self.repo}/issues/{number}", "PATCH", {"body": new}
                )
                print(f"  ~ #{number} {epic.id}")
        print("  ✓ índices al día")

    def link_sub_issues(self) -> None:
        self.step("Sub-issues (épica → tareas)")
        linked = 0
        for epic in self.b.epics.values():
            number = self.nums.map[epic.id]
            try:
                current = (
                    self.gh.api(
                        f"repos/{self.repo}/issues/{number}/sub_issues?per_page=100"
                    )
                    or []
                )
            except GhError as exc:
                self.warn(
                    f"Sub-issues no disponibles ({exc}); el índice de cada épica sirve de alternativa"
                )
                return
            have = {int(s["number"]) for s in current}
            for child in self.b.children(epic.id):
                if self.nums.map[child.id] in have:
                    continue
                try:
                    self.gh.api(
                        f"repos/{self.repo}/issues/{number}/sub_issues",
                        "POST",
                        {"sub_issue_id": self.rest_id(child.id)},
                    )
                    linked += 1
                except GhError as exc:
                    self.warn(f"No se pudo colgar [{child.id}] de {epic.id}: {exc}")
        print(f"  ✓ {linked} sub-issues enlazados")

    def link_dependencies(self) -> None:
        self.step("Dependencias (blocked by)")
        linked = 0
        for item in sorted(self.b.items.values(), key=lambda i: i.number):
            if not item.depends_on:
                continue
            number = self.nums.map[item.id]
            try:
                current = (
                    self.gh.api(
                        f"repos/{self.repo}/issues/{number}/dependencies/blocked_by?per_page=100"
                    )
                    or []
                )
            except GhError as exc:
                self.warn(
                    f"Dependencias nativas no disponibles ({exc}); quedan en el texto de cada tarea"
                )
                return
            have = {int(d["number"]) for d in current}
            for dep in item.depends_on:
                if self.nums.map[dep] in have:
                    continue
                try:
                    self.gh.api(
                        f"repos/{self.repo}/issues/{number}/dependencies/blocked_by",
                        "POST",
                        {"issue_id": self.rest_id(dep)},
                    )
                    linked += 1
                except GhError as exc:
                    self.warn(
                        f"No se pudo marcar [{item.id}] bloqueada por [{dep}]: {exc}"
                    )
        print(f"  ✓ {linked} dependencias enlazadas")

    # -- GitHub Project ----------------------------------------------------

    def project_readme(self) -> str:
        base = f"https://github.com/{self.repo}/blob/{self.b.project.get('base_branch', 'develop')}"
        return "\n".join(
            [
                f"# {self.b.project['title']}",
                "",
                str(self.b.project.get("description", "")),
                "",
                "## Cómo se usa",
                (
                    "- **Tareas numeradas** en orden de ejecución: la tarea N es el issue #N y solo se empieza cuando "
                    "están cerradas las que aparecen en «Antes de empezar deben estar terminadas»."
                ),
                *([f"- **Numeración:** {note}"] if (note := self.b.project.get("numbering_note")) else []),
                "- **Columnas:** Backlog → Ready → In progress → In review → Done (máximo 2 tareas en curso por persona).",
                (
                    "- **Reparto rotatorio:** los tres trabajan en todas las áreas; el revisor (campo *Revisor*) "
                    "cambia de una tarea a otra."
                ),
                "- **Flujo:** rama desde `develop` → PR con `Closes #N` → revisión → *squash merge*.",
                (
                    "- **Decisiones:** experimentos pre-registrados (rendimiento + recursos) → reunión de los tres → "
                    "ADR en `doc/adr/`."
                ),
                "",
                (
                    f"Proceso: [CONTRIBUTING.md]({base}/CONTRIBUTING.md) · "
                    f"Plan: [ROADMAP.md]({base}/gestion/ROADMAP.md) · "
                    f"Benchmarks: [PROTOCOLO.md]({base}/doc/benchmarks/PROTOCOLO.md)"
                ),
            ]
        )

    def ensure_project(self) -> None:
        self.step("GitHub Project")
        title = str(self.b.project["title"])
        listing = (
            self.gh.json(
                "project",
                "list",
                "--owner",
                self.owner,
                "--format",
                "json",
                "--limit",
                "100",
            )
            or {}
        )
        found = next(
            (p for p in listing.get("projects", []) if p.get("title") == title), None
        )
        if not found:
            found = self.gh.json(
                "project",
                "create",
                "--owner",
                self.owner,
                "--title",
                title,
                "--format",
                "json",
                write=True,
            )
            self.created["project"] += 1
            print(f"  + Project «{title}»")
        self.project_number = int(found["number"])
        self.project_id = found["id"]
        self.project_url = found.get("url", "")
        link = self.gh.run(
            "project",
            "link",
            str(self.project_number),
            "--owner",
            self.owner,
            "--repo",
            self.repo,
            check=False,
            write=True,
        )
        if (
            link.returncode != 0
            and "already" not in (link.stdout + link.stderr).lower()
        ):
            try:
                self.gh.graphql(
                    "mutation($p:ID!,$r:ID!){linkProjectV2ToRepository(input:{projectId:$p,repositoryId:$r})"
                    "{clientMutationId}}",
                    {"p": self.project_id, "r": self.repo_node_id},
                )
            except GhError as exc:
                self.warn(
                    f"No se pudo vincular el Project al repo ({exc}); hazlo en Project → Settings"
                )
        self.gh.run(
            "project",
            "edit",
            str(self.project_number),
            "--owner",
            self.owner,
            "--description",
            str(self.b.project.get("description", ""))[:250],
            "--readme",
            self.project_readme(),
            write=True,
            check=False,
        )
        print(f"  ✓ Project #{self.project_number} {self.project_url}")

    def refresh_fields(self) -> None:
        data = (
            self.gh.json(
                "project",
                "field-list",
                str(self.project_number),
                "--owner",
                self.owner,
                "--format",
                "json",
                "--limit",
                "100",
            )
            or {}
        )
        self.fields = {f["name"]: f for f in data.get("fields", [])}

    def project_items(self) -> dict[str, dict[str, Any]]:
        data = (
            self.gh.json(
                "project",
                "item-list",
                str(self.project_number),
                "--owner",
                self.owner,
                "--format",
                "json",
                "--limit",
                "1000",
            )
            or {}
        )
        out = {}
        for it in data.get("items", []):
            url = (it.get("content") or {}).get("url")
            if url:
                out[url] = it
        return out

    def ensure_fields(self) -> None:
        self.step("Campos del Project")
        self.refresh_fields()
        desired = self.b.board["status_options"]
        status = self.fields.get("Status")
        current = [o["name"] for o in (status or {}).get("options", [])]
        if status and current != [o["name"] for o in desired]:
            if not self.project_items() or self.opts.force_fields:
                try:
                    self.gh.graphql(
                        "mutation($f:ID!,$o:[ProjectV2SingleSelectFieldOptionInput!]){"
                        "updateProjectV2Field(input:{fieldId:$f,singleSelectOptions:$o}){clientMutationId}}",
                        {
                            "f": status["id"],
                            "o": [
                                {
                                    "name": o["name"],
                                    "color": o["color"],
                                    "description": o["description"],
                                }
                                for o in desired
                            ],
                        },
                    )
                    print("  ~ Status: " + " · ".join(o["name"] for o in desired))
                except GhError as exc:
                    self.warn(f"No se pudieron cambiar las columnas de Status ({exc})")
                    self.manual.append(
                        "Project → campo Status: dejar las opciones Backlog, Ready, In progress, In review y Done "
                        "y mover las tarjetas de «Todo» según su sprint."
                    )
            else:
                self.warn(
                    "El Project ya tiene items: no se tocan las columnas de Status (usa --force-fields si quieres)"
                )
        f = self.b.board["fields"]
        reviewer_options = [m.handle for m in self.b.team.values()] + [
            self.b.board.get("team_review_option", "Equipo")
        ]
        specs = [
            (f["points"], "NUMBER", None),
            (f["reviewer"], "SINGLE_SELECT", reviewer_options),
            (f["start"], "DATE", None),
            (f["end"], "DATE", None),
        ]
        for name, kind, options in specs:
            if name in self.fields:
                continue
            args = [
                "project", "field-create", str(self.project_number), "--owner", self.owner,
                "--name", name, "--data-type", kind, "--format", "json",
            ]  # fmt: skip
            if options:
                args += ["--single-select-options", ",".join(options)]
            self.gh.run(*args, write=True)
            self.created["fields"] += 1
            print(f"  + campo {name} ({kind})")
        self.refresh_fields()
        print("  ✓ campos: " + ", ".join(sorted(self.fields)))

    def _option(
        self, field: str, wanted: str, fallbacks: tuple[str, ...] = ()
    ) -> str | None:
        options = {
            o["name"].lower(): o["id"]
            for o in self.fields.get(field, {}).get("options", [])
        }
        for name in (wanted, *fallbacks):
            if name.lower() in options:
                return options[name.lower()]
        return None

    def ensure_items(self) -> None:
        self.step("Items del Project y valores de los campos")
        existing = self.project_items()
        f = self.b.board["fields"]
        initial = self.b.board.get("initial_status", {})
        current_sprint = self.b.board.get("current_sprint")
        added = updated = 0
        for entity in self.order:
            url = self.urls[entity.id]
            if url in existing:
                item_id = existing[url]["id"]
                is_new = False
            else:
                res = self.gh.json(
                    "project",
                    "item-add",
                    str(self.project_number),
                    "--owner",
                    self.owner,
                    "--url",
                    url,
                    "--format",
                    "json",
                    write=True,
                )
                item_id = res["id"]
                is_new = True
                added += 1
            if not (is_new or self.opts.force_fields) or isinstance(entity, Epic):
                continue
            values: list[tuple[str, dict[str, Any]]] = []
            status_name = (
                initial.get("current_sprint", "Ready")
                if entity.sprint == current_sprint
                else initial.get("other", "Backlog")
            )
            opt = self._option("Status", status_name, ("Todo",))
            if opt:
                values.append(
                    (self.fields["Status"]["id"], {"singleSelectOptionId": opt})
                )
            if f["points"] in self.fields:
                values.append(
                    (self.fields[f["points"]]["id"], {"number": float(entity.points)})
                )
            rev = (
                self.b.team[entity.reviewers[0]].handle
                if len(entity.reviewers) == 1
                else self.b.board.get("team_review_option", "Equipo")
            )
            opt = self._option(f["reviewer"], rev)
            if opt:
                values.append(
                    (self.fields[f["reviewer"]]["id"], {"singleSelectOptionId": opt})
                )
            sprint = self.b.sprints[entity.sprint]
            if f["start"] in self.fields:
                values.append(
                    (self.fields[f["start"]]["id"], {"date": sprint.start.isoformat()})
                )
            if sprint.end and f["end"] in self.fields:
                values.append(
                    (self.fields[f["end"]]["id"], {"date": sprint.end.isoformat()})
                )
            if values:
                self.set_values(item_id, values)
                updated += 1
        print(f"  ✓ {added} items añadidos · {updated} con campos rellenados")

    def set_values(
        self, item_id: str, values: list[tuple[str, dict[str, Any]]]
    ) -> None:
        decl = ["$p:ID!", "$i:ID!"]
        body = []
        variables: dict[str, Any] = {"p": self.project_id, "i": item_id}
        for n, (field_id, value) in enumerate(values):
            decl += [f"$f{n}:ID!", f"$v{n}:ProjectV2FieldValue!"]
            body.append(
                f"m{n}:updateProjectV2ItemFieldValue(input:{{projectId:$p,itemId:$i,fieldId:$f{n},value:$v{n}}})"
                "{clientMutationId}"
            )
            variables[f"f{n}"] = field_id
            variables[f"v{n}"] = value
        self.gh.graphql(f"mutation({','.join(decl)}){{{' '.join(body)}}}", variables)

    def ensure_views(self) -> None:
        self.step("Vistas del Project")
        headers = {
            "X-GitHub-Api-Version": PROJECTS_REST_API_VERSION,
            "Accept": "application/vnd.github+json",
        }
        scope = "users" if self.owner_type == "User" else "orgs"
        base = f"{scope}/{self.owner}/projectsV2/{self.project_number}"
        try:
            rest_fields = (
                self.gh.api(f"{base}/fields?per_page=100", headers=headers) or []
            )
        except GhError as exc:
            self.warn(f"La API REST de vistas no está disponible ({exc})")
            self.manual.append(self.manual_views())
            return
        field_ids = {f["name"]: int(f["id"]) for f in rest_fields}
        existing: set[str] = set()
        listing = self.gh.api(
            f"{base}/views?per_page=100", headers=headers, check=False
        )
        if isinstance(listing, list):
            existing = {v.get("name") for v in listing}
        milestone = self.b.sprints[self.b.board["current_sprint"]].title
        bf = self.b.board["fields"]
        visible = (
            "Title",
            "Assignees",
            "Status",
            bf["points"],
            bf["reviewer"],
            "Milestone",
            "Labels",
        )
        table_fields = [field_ids[n] for n in visible if n in field_ids]
        targets = [
            f"{scope}/{self.owner_id}/projectsV2/{self.project_number}/views",
            f"{base}/views",
        ]
        failed = False
        for view in self.b.board.get("views", []):
            if view["name"] in existing:
                continue
            body: dict[str, Any] = {
                "name": view["name"],
                "layout": view["layout"],
                "filter": str(view.get("filter", "")).replace(
                    "{current_milestone}", milestone
                ),
            }
            if view.get("columns") in field_ids:
                body["vertical_group_by"] = [field_ids[view["columns"]]]
            if view.get("group_by") in field_ids:
                body["group_by"] = [field_ids[view["group_by"]]]
            if view["layout"] == "table" and table_fields:
                body["visible_fields"] = table_fields
            ok = any(
                self.gh.api(path, "POST", body, headers=headers, check=False)
                is not None
                for path in targets
            )
            if ok:
                self.created["views"] += 1
                print(f"  + vista {view['name']}")
            else:
                failed = True
        if failed:
            self.warn("Algunas vistas no se pudieron crear por API")
            self.manual.append(self.manual_views())
        else:
            print("  ✓ vistas creadas (borra la vista por defecto «View 1» si quieres)")

    def manual_views(self) -> str:
        lines = ["Crear las vistas a mano (Project → New view):"]
        milestone = self.b.sprints[self.b.board["current_sprint"]].title
        for v in self.b.board.get("views", []):
            extra = f", columnas = {v['columns']}" if v.get("columns") else ""
            extra += f", agrupar por {v['group_by']}" if v.get("group_by") else ""
            filt = str(v.get("filter", "")).replace("{current_milestone}", milestone)
            lines.append(f"    · {v['name']}: {v['layout']}{extra}, filtro `{filt}`")
        return "\n".join(lines)

    # -- ajustes del repositorio -------------------------------------------

    def repo_settings(self) -> None:
        self.step("Ajustes de merge del repositorio")
        res = self.gh.run(
            "repo",
            "edit",
            self.repo,
            "--enable-squash-merge",
            "--enable-merge-commit",
            "--enable-rebase-merge=false",
            "--delete-branch-on-merge",
            check=False,
            write=True,
        )
        if res.returncode == 0:
            print(
                "  ✓ squash y merge commit permitidos, rebase desactivado, ramas borradas al fusionar"
            )
        else:
            self.warn(
                f"No se pudieron cambiar los ajustes de merge: {res.stderr.strip()}"
            )

    def protect_branches(self) -> None:
        self.step("Protección de ramas")
        body = {
            "required_status_checks": None,
            "enforce_admins": False,
            "required_pull_request_reviews": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews": True,
                "require_code_owner_reviews": False,
            },
            "restrictions": None,
            "required_conversation_resolution": True,
            "allow_force_pushes": False,
            "allow_deletions": False,
        }
        for branch in (
            self.b.project.get("base_branch", "develop"),
            self.b.project.get("release_branch", "main"),
        ):
            try:
                self.gh.api(
                    f"repos/{self.repo}/branches/{branch}/protection", "PUT", body
                )
                print(
                    f"  ✓ {branch}: PR obligatoria + 1 aprobación + conversaciones resueltas"
                )
            except GhError as exc:
                self.warn(
                    f"No se pudo proteger {branch} ({exc}). En repos privados de cuentas Free hace falta GitHub Pro "
                    "(gratis con GitHub Education); mientras tanto, la norma queda en CONTRIBUTING.md."
                )

    # -- orquestación ------------------------------------------------------

    def run(self) -> None:
        self.preflight()
        self.ensure_labels()
        self.ensure_milestones()
        self.ensure_issues()
        self.update_epics()
        self.link_sub_issues()
        if not self.opts.skip_dependencies:
            self.link_dependencies()
        if self.opts.repo_settings:
            self.repo_settings()
        if self.opts.protect_branches:
            self.protect_branches()
        if not self.opts.skip_project:
            self.ensure_project()
            self.ensure_fields()
            self.ensure_items()
            if not self.opts.skip_views:
                self.ensure_views()
            others = ", ".join(
                m.handle
                for m in self.b.team.values()
                if m.github.lower() != self.owner.lower()
            )
            self.manual += [
                f"Project → Settings → Manage access: añadir a {others} como Admin.",
                (
                    "Project → Workflows: activar «Auto-add to project» (filtro `is:issue,pr is:open`), "
                    "«Item closed» → Done, «Pull request merged» → Done, «Item reopened» → In progress y, "
                    "si existe, «Pull request linked to issue» → In review."
                ),
                "Vista Roadmap: en su configuración, elegir los campos de fecha Inicio y Fin.",
            ]
        self.summary()

    def summary(self) -> None:
        print("\n" + "=" * 72)
        print("Resumen")
        for key, value in sorted(self.created.items()):
            print(f"  {key:<12} {value} nuevos")
        print(f"  llamadas a gh: {self.gh.calls} ({self.gh.writes} de escritura)")
        if self.project_url:
            print(f"  Project: {self.project_url}")
        print(f"  Issues:  https://github.com/{self.repo}/issues")
        if self.warnings:
            print(f"\nAvisos ({len(self.warnings)}):")
            for w in self.warnings:
                print(f"  ⚠ {w}")
        if self.manual:
            print("\nPasos manuales pendientes:")
            for n, step in enumerate(self.manual, 1):
                print(f"  {n}. {step}")


# ---------------------------------------------------------------------------
# plan (offline)
# ---------------------------------------------------------------------------


def print_plan(b: Backlog, show: list[str]) -> None:
    repo = b.project.get("repo", "auto")
    detected = repo_from_remote() if repo == "auto" else repo
    nums = Numbering.planned(b)
    print(
        f"Repositorio: {detected or 'no detectado (se resolverá en apply)'} · Project «{b.project['title']}»"
    )
    print(
        f"\nLabels ({len(b.all_labels())}): "
        + ", ".join(lab.name for lab in b.all_labels())
    )
    print("\nMilestones:")
    for s in sorted(b.sprints.values(), key=lambda x: x.order):
        print(f"  · {s.title} ({b.sprint_range(s)})")
    print(
        f"\nIssues en orden de creación ({len(b.creation_order())}; en un repo vacío, tarea N = issue #N):"
    )
    for entity in b.creation_order():
        if isinstance(entity, Epic):
            print(f"  #{nums.map[entity.id]:<3} {entity.full_title}")
            continue
        deps = (
            f" · requiere {', '.join(entity.depends_on)}" if entity.depends_on else ""
        )
        print(
            f"  #{nums.map[entity.id]:<3} {entity.full_title}\n"
            f"       {entity.sprint} · {entity.points} pts · {entity.priority} · hace "
            f"{', '.join(b.handles(entity.owners))} · revisa {', '.join(b.handles(entity.reviewers))}{deps}"
        )
    f = b.board.get("fields", {})
    print(
        "\nProject: campo Status = "
        + " · ".join(o["name"] for o in b.board.get("status_options", []))
    )
    print(
        f"         campos nuevos = {f.get('points')} (número), {f.get('reviewer')} (selección), "
        f"{f.get('start')}/{f.get('end')} (fecha)"
    )
    print("         vistas = " + ", ".join(v["name"] for v in b.board.get("views", [])))
    print("\n(Simulación: no se ha llamado a GitHub.)")
    for iid in show:
        key = iid.zfill(2) if iid.isdigit() else iid
        entity = b.items.get(key) or b.epics.get(key)
        if not entity:
            print(f"\n{iid}: no existe")
            continue
        body = (
            render_epic_body(b, entity, nums)
            if isinstance(entity, Epic)
            else render_item_body(b, entity, nums)
        )
        print(
            f"\n{'─' * 72}\n#{nums.map[entity.id]} {entity.full_title}\n{'─' * 72}\n{body}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--backlog", type=Path, default=DEFAULT_BACKLOG, help="ruta a backlog.yml"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="comprueba el backlog")
    rep = sub.add_parser("report", help="genera el roadmap en Markdown")
    rep.add_argument(
        "-o", "--output", type=Path, help="fichero de salida (por defecto, stdout)"
    )
    pl = sub.add_parser("plan", help="simulación offline de apply")
    pl.add_argument(
        "--show",
        nargs="*",
        default=[],
        metavar="N",
        help="muestra el cuerpo de estas tareas",
    )
    ap = sub.add_parser("apply", help="crea o completa el backlog en GitHub")
    ap.add_argument("--gh", default="gh", help="ejecutable de GitHub CLI")
    ap.add_argument("--yes", action="store_true", help="no pedir confirmación")
    ap.add_argument(
        "--invite", action="store_true", help="invitar como colaboradores a quien falte"
    )
    ap.add_argument(
        "--skip-project",
        action="store_true",
        help="no crear ni tocar el GitHub Project",
    )
    ap.add_argument(
        "--skip-views", action="store_true", help="no crear vistas del Project"
    )
    ap.add_argument(
        "--skip-dependencies",
        action="store_true",
        help="no crear dependencias nativas (blocked by)",
    )
    ap.add_argument(
        "--repo-settings",
        action="store_true",
        help="squash + merge commit, sin rebase, borrar ramas",
    )
    ap.add_argument(
        "--protect-branches",
        action="store_true",
        help="proteger develop y main (PR + 1 aprobación)",
    )
    ap.add_argument(
        "--update-bodies",
        action="store_true",
        help="reescribir título, cuerpo y labels existentes",
    )
    ap.add_argument(
        "--force-fields",
        action="store_true",
        help="reescribir campos del Project en items existentes",
    )
    ap.add_argument(
        "--throttle",
        type=float,
        default=0.8,
        help="segundos de espera tras cada escritura",
    )
    ap.add_argument(
        "-v", "--verbose", action="store_true", help="muestra cada llamada a gh"
    )
    args = parser.parse_args(argv)

    try:
        backlog = load_backlog(args.backlog)
    except BacklogError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    errors, warnings = validate(backlog)

    if args.cmd == "validate":
        n_exp = sum(1 for i in backlog.items.values() if i.type == "experimento")
        n_dec = sum(1 for i in backlog.items.values() if i.type == "decision")
        print(
            f"Backlog: {len(backlog.items)} tareas numeradas ({n_exp} experimentos, {n_dec} decisiones) · "
            f"{len(backlog.epics)} épicas · {len(backlog.sprints)} sprints"
        )
        for w in warnings:
            print(f"  ⚠ {w}")
        for e in errors:
            print(f"  ✗ {e}")
        print("✓ Sin errores" if not errors else f"✗ {len(errors)} errores")
        return 1 if errors else 0

    if errors:
        print("✗ El backlog tiene errores; ejecuta `validate`", file=sys.stderr)
        return 1

    if args.cmd == "report":
        text = render_report(backlog)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
            print(f"✓ {args.output}")
        else:
            print(text)
        return 0

    if args.cmd == "plan":
        print_plan(backlog, args.show)
        return 0

    if not args.yes:
        print("Se va a escribir en GitHub (issues, labels, milestones y Project).")
        print(
            "Los asignados recibirán notificaciones. Ejecuta antes `plan` para revisarlo."
        )
        if input("Escribe «si» para continuar: ").strip().lower() not in {
            "si",
            "sí",
            "s",
            "yes",
            "y",
        }:
            print("Cancelado.")
            return 1
    opts = ApplyOptions(
        invite=args.invite,
        skip_project=args.skip_project,
        skip_views=args.skip_views,
        skip_dependencies=args.skip_dependencies,
        repo_settings=args.repo_settings,
        protect_branches=args.protect_branches,
        update_bodies=args.update_bodies,
        force_fields=args.force_fields,
    )
    Applier(
        backlog, GH(args.gh, throttle=args.throttle, verbose=args.verbose), opts
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
