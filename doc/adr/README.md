# Registro de decisiones de arquitectura (ADR)

Cada decisión de modelo o de arquitectura queda registrada en un ADR (*Architecture Decision Record*, Nygard 2011). El ADR recoge el contexto, las opciones evaluadas con sus métricas de rendimiento y de recursos, la decisión y sus consecuencias. La tabla de este fichero es la base del capítulo de decisiones de la memoria (tarea 67).

## Proceso

1. Quien hace el experimento copia [`ADR-000-plantilla.md`](ADR-000-plantilla.md) como `ADR-NNN-slug.md` y rellena el contexto, las opciones y la tabla de evidencias. Estado: **Propuesto**.
2. En la reunión de decisión, los tres aplican el criterio pre-registrado y se completan la decisión, la votación y las consecuencias. Estado: **Aceptado**.
3. La PR del ADR la aprueban los dos miembros que no la han redactado. Después se añade la fila correspondiente en la tabla de abajo.
4. Un ADR aceptado no se reescribe. Si la decisión cambia, se crea uno nuevo, el anterior pasa a **Sustituido por ADR-NNN** y se explica el motivo.

Protocolo de medición: [`doc/benchmarks/PROTOCOLO.md`](../benchmarks/PROTOCOLO.md).

## Índice de decisiones

| ADR | Decisión | Estado | Opción elegida | Alternativas evaluadas | Evidencia de rendimiento | Evidencia de recursos | Tarea | Fecha |
|:--|:--|:--|:--|:--|:--|:--|:-:|:--|
| ADR-001 | Backend de inferencia LLM | Pendiente | | | | | #17 | |
| ADR-002 | Stack de recuperación (embeddings + índice) | Pendiente | | | | | #28 | |
| ADR-003 | LLM generador para T1 y T2 | Pendiente | | | | | #33 | |
| ADR-004 | Recuperación de T2 (chunking + retrieval + reranking) | Pendiente | | | | | #43 | |
| ADR-005 | Enfoque final de T1 | Pendiente | | | | | #49 | |
| ADR-006 | Juez LLM y protocolo de evaluación | Pendiente | | | | | #51 | |
| ADR-007 | Contexto y generación de T2 | Pendiente | | | | | #53 | |
| ADR-008 | Ampliaciones opcionales | Pendiente | | | | | #54 | |
| ADR-009 | Arquitectura final congelada | Pendiente | | | | | #60 | |
