# Diario de Trabajo

Registro de las decisiones del grupo, las técnicas probadas o descartadas y sus fuentes. Cada entrada lleva fecha y, cuando corresponde, el estado: **adoptado**, **descartado** o **pendiente**.

---

## 2026-10-21 · Corpus inicial

- Se añade al repo el corpus etiquetado de TA1C que se tenía en local. **Adoptado.**
- Los CSV extraídos no se versionan; solo los comprimidos originales.

## 2026-10-22 · Fuentes originales y protocolo de datos

- Se analiza el corpus local: es el corpus de 2024 (3500 tweets, 20 columnas, con artículo y etiqueta por anotador), idéntico (mismo hash) al de [gmordecki/TA1C](https://github.com/gmordecki/TA1C) (MIT).
- Se localizan en [pln-fing-udelar/pln-inco-resources](https://github.com/pln-fing-udelar/pln-inco-resources) (carpeta `clickbait/iberlef2025`) el reparto oficial de la T1 (2800 train / 700 dev / 700 test, con etiquetas de test) y los 500 spoilers de la T2 (300 / 100 / 100). Licencia no especificada por los autores.
- Se reorganizan los datos en tres comprimidos: `deteccion`, `spoiler` y `deteccion_extra`. **Adoptado.**
- **Protocolo T1:** entrenar con `detection_train`, validar con `detection_dev_gold` y evaluar una sola vez con `detection_test_gold`. Al ser el reparto oficial, el resultado es comparable con el ranking (4.º puesto: macro-F1 0,8048). **Adoptado.**
- **Protocolo T2:** `spoiling_train` para elegir ejemplos *few-shot*, `spoiling_dev_gold` para ajustar prompts y recuperación, `spoiling_test_gold` para la evaluación final. **Adoptado.**
- **`deteccion_extra`:** su train + validation coincide con `detection_train` y su test con `detection_dev_gold` (mismos IDs). Se puede cruzar por `Tweet ID` para añadir el artículo en train y dev, pero no en test. Uso opcional. **Pendiente.**
- Se descarta usar los splits locales del corpus 2024 como train/test: su «test» es el dev oficial, así que no es un test limpio. **Descartado.**

## 2026-10-24 · Entorno de trabajo

- **Gestor de dependencias: uv** (`pyproject.toml` + `uv.lock` + `.python-version`, Python 3.13). **Adoptado.**
  - Motivo: el lockfile fija todas las versiones (también las indirectas) y es multiplataforma, así que todos los miembros del grupo tienen el mismo entorno con `uv sync`. Además gestiona la versión de Python y es mucho más rápido que pip.
  - Alternativa considerada: `pip` + `venv` + `requirements.txt`. **Descartada** como herramienta principal porque, sin `pip-tools`, no fija las dependencias indirectas y las versiones acaban divergiendo entre miembros.
  - Para la entrega se exportará un `requirements.txt` (`uv export --format requirements-txt`), de modo que el sistema se pueda instalar también con `pip` sin necesidad de uv.
  - Las dependencias se añaden a medida que se necesiten (`uv add <paquete>`), no todas de golpe.
  - Fuente: documentación de uv, <https://docs.astral.sh/uv/>.
- **Versionado de datos con DVC: descartado.**
  - Los datos son pequeños (~8 MB en total) y estáticos (reparto oficial de los organizadores), y ya están en git.
  - DVC necesita un almacenamiento remoto al que deberían acceder todo el grupo y el evaluador, lo que añade complicación sin beneficio claro.
  - Los artefactos pesados (embeddings, índices vectoriales) se regeneran desde el código y no se versionan.
  - Si se usa el corpus sin etiquetar (~990 MB) como base del RAG, se descargará con un script que verifique su hash SHA-256.
  - Fuente: documentación de DVC, <https://dvc.org/doc>.

## 2026-10-26 · Metodología de trabajo y gestión del proyecto

- **Marco de trabajo: Scrum ligero con tablero Kanban en GitHub Projects.** Sprints de 2 semanas (S1 26-oct → S4 18-dic, más la defensa), columnas Backlog → Ready → In progress → In review → Done con WIP ≤ 2 por persona, Definition of Ready/Done y ceremonias (planning, standup asíncrono, review + retro), todo documentado en `CONTRIBUTING.md`. **Pendiente** (se adopta en la tarea 02).
  - Motivo: la práctica es abierta y obliga a probar muchas alternativas; los sprints cortos permiten re-priorizar con los resultados, y el tablero enlazado a las PRs deja trazabilidad de quién hizo y revisó qué para el Diario y la memoria.
  - Alternativas consideradas: un tablero fuera de GitHub (**descartado**: no se enlaza con issues ni PRs) y crear el backlog a mano (**descartado**: ~75 issues, propenso a errores). Se usa un backlog versionado (`gestion/backlog.yml`) y un script idempotente (`gestion/bootstrap_github.py`) que lo vuelca a GitHub.
  - Fuentes: Scrum Guide (2020), <https://scrumguides.org/scrum-guide.html>; GitHub Docs, Projects, <https://docs.github.com/en/issues/planning-and-tracking-with-projects>.
- **Reparto rotatorio, sin roles.** Los tres trabajamos en todas las áreas (T1, T2, plataforma y evaluación, memoria) con carga equilibrada por sprint; una tarea y la siguiente que depende de ella casi nunca las hace la misma persona. El revisor rota (cada uno revisa a los otros dos por igual). Solo hay portavoz (@GuillermoBlancoNunez), que lo exige el enunciado. **Adoptado.**
  - Descartado: un dueño fijo por área, porque concentra el conocimiento y en la defensa se evalúa la comprensión de todo el sistema.
- **Tareas numeradas (01–75) en orden de ejecución:** cada una indica qué tareas deben estar terminadas antes de empezar; en el repo, la tarea N es el issue #N. Repo nuevo creado por @GuillermoBlancoNunez con el contenido actual. **Adoptado.**
- **Flujo de cambios:** una rama por issue desde `develop`, PR con `Closes #N`, 1 aprobación obligatoria (2 en los ADR) y *squash merge*; release `develop → main` con tag al final de cada sprint; commits con Conventional Commits. **Pendiente** (tarea 02).
- **Protocolo de decisiones de arquitectura:** cada decisión de modelo o arquitectura (9 en el plan, ADR-001 a ADR-009) se toma con benchmarks pre-registrados que miden rendimiento (con IC95) y recursos (tiempo, latencia, RAM/VRAM y energía con CodeCarbon), en una reunión de los tres, y queda en un ADR en `doc/adr/` que alimenta la memoria (`doc/benchmarks/PROTOCOLO.md`). **Pendiente** (tarea 02).
  - Fuentes: Nygard (2011), <https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions>; Henderson et al. (2020), JMLR 21(248); Dror et al. (2018), ACL; CodeCarbon, <https://mlco2.github.io/codecarbon/>.
- **Cómputo disponible:** portátiles (solo CPU) y Colab/Kaggle (GPU gratuita), sin APIs de pago. En consecuencia: modelos ≤12B cuantizados; todas las opciones de una decisión se miden en el mismo entorno; la energía de CPU puede ser una estimación por TDP, y se declara el método. **Adoptado.**

## 2026-10-31 · Repositorio público al acabar

- **El repo se hará público al acabar la asignatura**, como portfolio de los tres. **Pendiente** (se revisa en la tarea 02).
  - Desde ya, nada que no pueda ser público entra en git: reglas en `CONTRIBUTING.md` §14, una línea más en la Definition of Done de todas las tareas y en la plantilla de PR.
  - **Los datos de TA1C dejan de versionarse** (no tienen licencia explícita): se quitan del historial en la tarea 01, mientras solo hay un commit, y se descargan de las fuentes oficiales con verificación SHA-256 (tareas 01 y 11). Sustituye a «solo se versionan los comprimidos» (2026-10-21).
  - Barreras automáticas: `gitleaks` y `nbstripout` en pre-commit y en la CI (tarea 10); claves de API solo por variables de entorno (tarea 27).
  - Alternativa considerada: limpiar el historial al final. **Descartada:** reescribir cientos de commits cambia los SHA registrados en `results/` y en la memoria y obliga a todos a volver a clonar.
- **Nueva fase «Publicación» (21-dic → 10-ene, épica E10)**, después de la entrega y sin competir con ella: API de inferencia en Docker (76), encoder en Hugging Face Hub (77), README bilingüe (78) y auditoría antes de cambiar la visibilidad (79). **Pendiente.**
  - Alternativa considerada: meterlo en el Sprint 4. **Descartada:** es el sprint con más riesgo (R9) y la publicación no forma parte de la entrega.
