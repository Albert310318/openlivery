# Reportes

> Read in English: [reports.md](../en/reports.md)

**Reportes** muestra lo que costaron las respuestas de tus agentes: el total del periodo, el reparto por cliente, agente y modelo, el costo por día y cada respuesta detrás de esas cifras. Lee el uso que cada respuesta registra, así que los números son lo que cobró el proveedor, no una estimación.

## Qué registra una respuesta

Cada respuesta de IA deja una fila en `usage_records` con los tokens que usó, lo que costó según lo reportó OpenRouter (`cost_usd`), el proveedor que la sirvió (`served_by`, por ejemplo Azure o Google AI Studio), los tokens cacheados y de razonamiento, cuánto tardó el modelo (`duration_ms`), y la conversación y el mensaje a los que pertenece. Las transcripciones de notas de voz y las descripciones de imágenes también dejan fila, ligadas a la conversación, al agente y al mensaje que traía el audio o la imagen. El endpoint de audio responde sin lo que cobró el proveedor, así que el costo y el proveedor de la transcripción se leen del registro de la generación de OpenRouter un instante después; solo si ese registro no está disponible la fila se valora al precio de lista de voz a texto del catálogo y se marca como estimada.

Las filas anteriores a la versión 0.4 solo traen tokens. Reportes las valora al precio de lista del catálogo para ese modelo (`apps/api/app/services/model_catalog.py`) y las marca como **estimadas**, en la fila y en el indicador que las cuenta.

## La página

- **Periodo**: últimos 7, 30 o 90 días, o un rango personalizado (hasta un año). Los días se agrupan en la zona horaria de tu navegador.
- **Filtros**: cliente, agente, modelo. La tabla por respuesta además busca por nombre de contacto o id de conversación.
- **Indicadores**: costo total y respuestas, promedio por respuesta y tokens totales, conversaciones con al menos una respuesta, respuestas estimadas.
- **Tablas**: por cliente, por agente y por modelo, cada una con respuestas, tokens, costo y su peso en el total.
- **Cada respuesta**: fecha, conversación, contacto, cliente, agente, canal, modelo, quién la sirvió, duración, tokens y costo, de 25 en 25. **Exportar CSV** descarga todo el periodo (hasta 5.000 filas).
- La página se refresca sola cada 30 segundos.

## API

| Ruta | Devuelve |
| --- | --- |
| `GET /api/reports/costs?from=&to=&tz=&client_id=&agent_id=&model=` | totales, `by_client`, `by_agent`, `by_model`, `by_day` |
| `GET /api/reports/replies?from=&to=&q=&limit=&offset=` | una página de respuestas, la más reciente primero, con `total` |
| `GET /api/reports/replies?...&format=csv` | el periodo en CSV |

Ambas necesitan sesión de agencia y solo ven las filas de esa agencia.

## Próximos pasos

- [Proveedores de IA](ai-providers.md): de dónde sale el costo de cada respuesta.
- [Agentes](agents.md): elige el modelo con el que responde cada agente.
