<!-- Título de la PR: [NN] Descripción  ·  Base: develop  ·  Aprueba el «Revisor» de la tarea -->

## Qué cambia

<!-- 1–3 frases: qué y por qué. -->

Closes #

## Tipo

- [ ] Feature / infra
- [ ] Experimento (EXP)
- [ ] Decisión (ADR)
- [ ] Memoria / documentación
- [ ] Fix

## Cómo probarlo

```bash
uv sync
uv run tapln …   # comando exacto que reproduce el resultado
```

## Resultados (experimentos y features con métricas)

| Opción / sistema | Rendimiento (IC95) | Latencia p50 / p95 | Energía | RAM / VRAM pico | Entorno |
|:--|:--|:--|:--|:--|:--|
| | | | | | |

## Checklist del autor (Definition of Done)

- [ ] La rama sale de `develop` y está actualizada con ella.
- [ ] Todos los criterios de aceptación de la tarea están cumplidos.
- [ ] CI en verde; tests añadidos o actualizados.
- [ ] Reproducible: configuración en `configs/`, semilla fija y comando indicado arriba.
- [ ] Resultados en `results/NN/` con el esquema común (rendimiento, recursos y hardware), si aplica.
- [ ] No se toca `test_gold` (salvo en las tareas 61 y 62, con `--final-test`) y la base de conocimiento del RAG solo contiene train.
- [ ] Apto para un repo público (CONTRIBUTING §14): sin secretos, datos del corpus, material de la asignatura, datos personales, modelos ni artefactos pesados (tampoco en notebooks, `results/` o logs).
- [ ] Entrada en `Diario_de_trabajo.md` (qué, por qué, fuente y estado).
- [ ] Documentación o README actualizados si cambia el uso.

## Para el revisor

- [ ] Hace lo que pide la tarea y los criterios se cumplen de verdad.
- [ ] Sin fuga de datos; split y métricas correctos (configuración oficial, tarea 21).
- [ ] He ejecutado el comando de prueba o he revisado los logs y `results/`.
- [ ] Código legible, con tests razonables.
- [ ] Nada que no pueda hacerse público: secretos, textos del corpus, salidas de notebooks o datos personales.

<!-- Prefijos de comentario: [bloqueante] · [sugerencia] · [pregunta] · [nit]. Fusiona el autor con «Squash and merge». -->
