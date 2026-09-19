# VENDIQ — Fase 1A

Implementación aditiva de catálogo de planes/módulos y suscripción por empresa (`Client`).
No conecta la suscripción con autorización operativa, WhatsApp, IA, leads ni verificación de correo.

## API

Todas las rutas llevan prefijo `/api`.

| Método y ruta | Acceso |
| --- | --- |
| `GET /plans` | Sesión administrativa; planes activos y disponibles para nuevas contrataciones. |
| `GET /clients/{client_id}/subscription` | Sesión administrativa, empresa de su agencia. Devuelve `null` si no hay suscripción. |
| `PUT /clients/{client_id}/subscription` | Rol `admin`, empresa de su agencia; asignación explícita o actualización. |
| `GET /portal/{slug}/subscription` | Sesión del portal verificada y vigente, exclusivamente su empresa. |
| `GET /portal/{slug}/plans` | Misma protección del portal; catálogo de planes disponibles. |

Ejemplo de cuerpo de PUT (reemplazar el UUID por un plan existente):

```json
{
  "plan_id": "00000000-0000-0000-0000-000000000000",
  "status": "TRIAL",
  "next_renewal_at": null
}
```

Los estados permitidos son `TRIAL`, `ACTIVE`, `PAYMENT_PENDING`, `SUSPENDED`, `CANCELLED`.
`next_renewal_at`, cuando se informa, requiere timezone. PUT reemplaza los campos editables;
la próxima renovación queda nula si se omite. No acepta campos adicionales, módulos ni fechas de activación/trial.
No existe ruta de escritura para el portal, ni una ruta pública que exponga suscripciones.

El catálogo de planes es global. Los administradores de agencia pueden asignar planes disponibles a sus empresas,
pero no editar el catálogo global y afectar a otras agencias. La carga comercial de planes queda fuera de esta fase:
no se inventan nombres, precios ni moneda, ni se incluyen planes por defecto.
`monthly_price` usa decimal `Numeric(12,2)`, permite nulo para precio sin configurar y exige valor no negativo.
La moneda se configura por plan. Un plan retirado sigue siendo visible en suscripciones existentes y permite
actualizar su estado; no se permite asignarlo a nuevas suscripciones ni cambiar hacia él desde otro plan.

## Integridad y compatibilidad

- Una suscripción por empresa, sin `plan_id` en `clients`.
- Módulos derivados exclusivamente del plan. `is_available` distingue disponibilidad real de inclusión en el catálogo.
- La migración registra 13 módulos; únicamente `ai_agent`, `leads` y `advisor_handoff` se marcan disponibles inicialmente.
- `payments` representa pagos de clientes de la PYME, no el cobro de suscripciones VENDIQ.
- `TRIAL` puede existir sin fechas: pendiente de primera activación. Ningún endpoint de Fase 1A inicia el trial.
- Las fechas de activación/trial deben aparecer juntas; el inicio coincide con la primera activación y el fin es exactamente 72 horas después.
- La primera activación, una vez informada, es inmutable. La migración incluye una protección de integridad PostgreSQL;
  no es un disparador de activación y no asigna fechas.
- No hay tareas, hooks de mensajes, cambios automáticos de estado ni bloqueos por vencimiento.
- `PAYMENT_PENDING` se reserva para un pago pendiente de verificación. En Fase 1B, un trial vencido sin pago confirmado
  pasará a `SUSPENDED`; Fase 1A no ejecuta esa transición.
- Las consultas no crean suscripciones ni modifican empresas. No se incorporan empresas existentes automáticamente.

## Interfaz

`/clients/[id]` añade una pestaña informativa «Plan y suscripción». La administración se expone por la API protegida.
`/portal/[slug]?view=plan` muestra «Mi plan» y sus fechas; «Mejorar plan» abre `?view=plans`, de consulta únicamente.
Los precios provienen de la API. Las nuevas etiquetas están traducidas a español e inglés.

## Migración y validación

`0022_client_subscriptions` depende de `0021_portal_email_verification`.
Solo crea las cuatro tablas nuevas, restricciones/índice, una función de integridad y los registros de módulos.
No actualiza tablas existentes. La migración queda pendiente de aplicación.

Los tests enfocados están en `apps/api/tests/test_subscriptions.py`. Reemplazan la fixture destructiva del directorio
por SQLite en memoria y comprueban permisos, aislamiento, estados, unicidad, módulos, compatibilidad y fechas.
No ejecutan Alembic. El DDL específico de PostgreSQL se comprueba por compilación; su ejecución real y la concurrencia
entre procesos deberán validarse en una base desechable al autorizar la siguiente etapa.
