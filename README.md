# TAPLN · TA1C: detección y spoiling de clickbait en español

Trabajo práctico de *Técnicas Avanzadas de Procesamiento de Lenguaje Natural*. Desarrollamos un sistema basado en LLMs con RAG para la tarea competitiva **TA1C** (*Te Ahorré Un Click*, IberLEF 2025).

## La tarea

- **Tarea 1. Detección:** clasificación binaria (Clickbait/No) de tweets de medios que enlazan a una noticia. Métrica principal: F1.
- **Tarea 2. Spoiling:** generar un texto de como máximo 280 caracteres que responda a la curiosidad creada por el teaser, a partir del artículo. Métricas: BERTScore, BLEU-4 y ROUGE-L.

Más información en la [web oficial de TA1C](https://www.fing.edu.uy/inco/grupos/pln/ta1c/).

## Estructura

| Carpeta | Contenido |
|---|---|
| `Data/` | Corpus TA1C comprimido en tres archivos (ver sección *Datos*) |
| `Enunciado/` | Enunciado de la tarea e indicaciones de la asignatura |
| `Papers/` | Artículos de los participantes en TA1C 2025 ([CEUR Vol-4098](https://ceur-ws.org/Vol-4098/), CC BY 4.0) |
| `doc/` | Memoria, decisiones de arquitectura (`adr/`) y protocolo de benchmarks (`benchmarks/`) |
| `gestion/` | Backlog (línea base), roadmap y script que crea el tablero en GitHub |
| `.github/` | Plantillas de issues y PRs, y revisores automáticos (CODEOWNERS) |

## Datos

Hay tres conjuntos, cada uno en un `.tar.gz` dentro de `Data/`. Al descomprimirlos aparece una carpeta con tres CSV.

| Archivo | Contenido | Tarea | Fuente |
|---|---|---|---|
| `TA1C_dataset_deteccion.tar.gz` | Reparto oficial de la competición: `train` (2800), `dev_gold` (700) y `test_gold` (700). Solo trae ID, fecha, medio, país, `Teaser Text` y `Tag Value` | **T1, principal** | [pln-fing-udelar/pln-inco-resources](https://github.com/pln-fing-udelar/pln-inco-resources/tree/master/clickbait/iberlef2025/detection). Licencia no especificada por los autores |
| `TA1C_dataset_spoiler.tar.gz` | 500 spoilers escritos a mano: `train` (300), `dev_gold` (100) y `test_gold` (100). Incluye el artículo (`Article Text JSON`) y la respuesta (`Response`) | **T2** | [pln-fing-udelar/pln-inco-resources](https://github.com/pln-fing-udelar/pln-inco-resources/tree/master/clickbait/iberlef2025/spoiling). Licencia no especificada por los autores |
| `TA1C_dataset_deteccion_extra.tar.gz` | Corpus original de 2024 (3500 tweets): `train` (2100), `validation` (700) y `test` (700). Tiene 20 columnas: artículo (título, subtítulo, texto), tweet por separado, URLs y etiqueta de cada anotador | **T1, extra opcional** | [gmordecki/TA1C](https://github.com/gmordecki/TA1C), licencia MIT |

### Cómo se usan

- **Tarea 1 (detección).** Se entrena con `train`, se valida con `dev_gold` y se evalúa una sola vez con `test_gold`. Como ese reparto es el oficial, el resultado es comparable con el ranking de la competición. Solo contiene el teaser, así que el modelo de T1 trabaja a partir del teaser.
- **Tarea 2 (spoiling).** Se usan los tres CSV de `spoiler`: `train` para elegir ejemplos *few-shot*, `dev_gold` para ajustar prompts y recuperación, y `test_gold` para la evaluación final. El artículo viene incluido en cada fila.
- **`deteccion_extra` (propuesta opcional).** No es necesario para la entrega. Los IDs de `train` y `dev_gold` de `deteccion` están todos aquí (el train oficial es el train + validation de este corpus, y el dev oficial es su test), así que se pueden cruzar por `Tweet ID` para añadirles el artículo y las etiquetas por anotador. Permite probar si el artículo mejora la detección y analizar los casos con desacuerdo entre anotadores. **Solo se puede hacer en train y dev: los 700 tweets de `test_gold` no están en este corpus**, por lo que ese experimento no se puede evaluar en el test oficial.

### Obtener los CSV

Los CSV no se versionan (están en `.gitignore`). Desde la raíz del repo:

```
tar -xzf Data/TA1C_dataset_deteccion.tar.gz -C Data
tar -xzf Data/TA1C_dataset_spoiler.tar.gz -C Data
tar -xzf Data/TA1C_dataset_deteccion_extra.tar.gz -C Data
```

## Cómo trabajamos

- **Proceso** (Scrum ligero, tablero, ramas, revisión cruzada de PRs, Definition of Ready/Done): [`CONTRIBUTING.md`](CONTRIBUTING.md).
- **Plan:** [`gestion/ROADMAP.md`](gestion/ROADMAP.md) (sprints, carga por persona, mapa de decisiones y riesgos). El estado vivo está en el GitHub Project «TAPLN · TA1C».
- **Decisiones de modelo y arquitectura:** benchmarks pre-registrados de rendimiento y recursos ([`doc/benchmarks/PROTOCOLO.md`](doc/benchmarks/PROTOCOLO.md)) y ADR en [`doc/adr/`](doc/adr/README.md).

## Referencias

- Mordecki, G., Moncecchi, G., Couto, J. (2024). *Te Ahorré Un Click: A Revised Definition of Clickbait and Detection in Spanish News*. Iberamia 2024.
- Mordecki, G., Chiruzzo, L., et al. (2025). *Overview of TA1C at IberLEF 2025: Detecting and Spoiling Clickbait in Spanish-Language News*. Procesamiento del Lenguaje Natural, 75.
