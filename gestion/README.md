# Gestión del proyecto

| Fichero | Qué es |
|:--|:--|
| [`backlog.yml`](backlog.yml) | 75 tareas numeradas en orden de ejecución, con criterios de aceptación, quién la hace, revisor, sprint, puntos, prioridad y tareas requeridas. Es la línea base del plan. |
| [`ROADMAP.md`](ROADMAP.md) | Lista de tareas, calendario, reparto por persona y área, quién revisa a quién, mapa de decisiones y riesgos. |
| [`bootstrap_github.py`](bootstrap_github.py) | Valida el backlog y lo vuelca a GitHub (issues + GitHub Project). |

Tras el bootstrap, la fuente de verdad es el GitHub Project.

## Bootstrap (tarea 01 · lo ejecuta quien crea el repo)

1. **Crear el repo vacío** en GitHub, sin README ni licencia. Copiar el contenido actual, hacer un commit en `main` y crear `develop`:
   ```powershell
   git init -b main; git add .; git commit -m "chore: importación inicial"
   git remote add origin https://github.com/<usuario>/<repo>.git
   git push -u origin main; git switch -c develop; git push -u origin develop
   ```
2. **Invitar a los otros dos** (Settings → Collaborators) y esperar a que acepten.
3. **Volcar el backlog** antes de abrir ninguna PR o issue, para que la tarea N sea el issue #N:
   ```powershell
   gh auth refresh -s project
   uv run --with pyyaml python gestion/bootstrap_github.py validate
   uv run --with pyyaml python gestion/bootstrap_github.py plan
   uv run --with pyyaml python gestion/bootstrap_github.py apply --repo-settings --protect-branches
   ```
   Tarda 10–15 minutos y se puede relanzar sin duplicar nada. Si alguien no había aceptado aún la invitación, se relanza `apply` cuando acepte.
4. **Pasos manuales** que imprime al final: acceso al Project para los otros dos, workflows (auto-add, cerrado → Done, PR fusionada → Done) y fechas de la vista Roadmap.

El repo se detecta del remoto `origin`, y el Project se crea bajo la cuenta del dueño del repo. Si el repo ya tenía issues, se avisa: los títulos llevan `[NN]` y las referencias entre tareas se ajustan solas.

## Problemas frecuentes

| Mensaje | Solución |
|:--|:--|
| `Al token le falta el scope «project»` | `gh auth refresh -s project` |
| `… no es colaborador` | Invitarlo y relanzar `apply` cuando acepte |
| `No se pudo proteger main (Upgrade to GitHub Pro…)` | Repo privado en cuenta Free: activar GitHub Pro (gratis con GitHub Education) o seguir la norma de CONTRIBUTING §7 |
| `Algunas vistas no se pudieron crear` | Crearlas a mano con lo que imprime el script |
| `Límite de peticiones de GitHub` | Esperar y relanzar |

Regenerar el roadmap: `uv run --with pyyaml python gestion/bootstrap_github.py report -o gestion/ROADMAP.md`
