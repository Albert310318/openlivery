# VENDIQ — Fase 1A.2: promociones de suscripción

Implementación aditiva sobre Fase 1A y las reglas aprobadas en la solicitud.
No se aplica `0023`, ni se hace rebuild o reinicio. No hay integración con proveedores,
cobros, confirmación de pago, activación, vencimiento del trial o el módulo `payments`.

## Archivos de esta fase

Nuevos:
- `apps/api/app/models_promotions.py`
- `apps/api/app/schemas_promotions.py`
- `apps/api/app/services/promotions.py`
- `apps/api/app/routers/promotions.py`
- `apps/api/migrations/versions/0023_subscription_promotions.py`
- `apps/api/tests/test_promotions.py`
- `docs/vendiq-phase1a2.md`

Modificados (preservando los cambios anteriores del workspace):
- `apps/api/app/models.py`: atributo `User.is_vendiq_admin` y registro de los modelos nuevos.
- `apps/api/app/main.py`: registro del router nuevo.

No hay cambios de frontend: TypeScript y ESLint no corresponden a esta fase.

## API

Todas las rutas llevan `/api`. No existen endpoints de escritura de cargos o canjes,
ni de borrado de promociones. Se deshabilita una promoción mediante PUT completo.

| Método / ruta | Acceso |
| --- | --- |
| POST `/subscription-promotions` | Administrador general VENDIQ; crear |
| PUT `/subscription-promotions/{id}` | Administrador general VENDIQ; reemplazar condiciones |
| GET `/subscription-promotions` | Administrador general VENDIQ; listado paginado |
| POST `/clients/{client_id}/subscription-promotions/preview` | Administrador general VENDIQ; empresa explícita |
| GET `/clients/{client_id}/subscription-promotion-redemptions` | Administrador general VENDIQ; historial paginado |
| POST `/portal/{slug}/subscription-promotions/preview` | Sesión de portal vigente y verificada, exclusivamente su empresa |
| GET `/portal/{slug}/subscription-promotion-redemptions` | Misma protección; exclusivamente su historial |

`is_vendiq_admin` se consulta en base de datos; no se deriva del rol `admin`, correo,
agencia ni datos enviados por el navegador. Default y server default son false.
No se modifica registro/login ni existe endpoint para conceder este privilegio.
Su concesión inicial requiere una operación explícita posterior autorizada.

El preview solo acepta `code` y `plan_id`, rechazando campos extra. No reserva usos.
Obtiene empresa desde la sesión del portal, precio y moneda del plan, condiciones de
la promoción y conteos de canjes confirmados desde el servidor. Es una estimación
para un ciclo mensual de pago futuro, nunca un descuento aplicado al trial.

Ejemplo de creación (cantidades decimales como strings):

```json
{
  "code": " bienvenida ",
  "discount_type": "PERCENTAGE",
  "value": "25.00",
  "plan_scope": "ALL",
  "duration": "FIRST_PAID_MONTH",
  "cycles": 1,
  "max_uses_per_client": 1
}
```

El código se convierte a `BIENVENIDA`. `SELECTED` exige `plan_ids` no vacíos;
`ALL` exige una lista vacía. `FIXED` requiere moneda de tres letras mayúsculas.
`NEXT_N_RENEWALS` requiere ciclos enteros positivos y representa los siguientes N
ciclos mensuales de pago, excluyendo el trial. `FIRST_PAID_MONTH` exige exactamente
un ciclo y rechaza empresas con cargos PAID previos. Límites nulos significan sin límite;
si se especifican, deben ser enteros positivos. Inicio inclusivo y vencimiento exclusivo,
ambos opcionales y con timezone. Los precios sin configurar y planes no disponibles se rechazan.

Numeric(12,2)/Decimal, redondeo comercial HALF_UP al céntimo y descuento limitado al
precio original. Un descuento del 100% o fijo superior al precio produce total 0.
Ningún preview cambia estados, fechas, canjes, ciclos ni empresas.

## Migración pendiente

`0023_subscription_promotions` depende de `0022_client_subscriptions`.
El DDL queda congelado dentro de la migración, sin importar modelos de aplicación.

1. Añade `users.is_vendiq_admin BOOLEAN NOT NULL DEFAULT false`; no eleva usuarios.
2. Crea `subscription_promotions`: código único normalizado, tipo/valor/moneda,
   alcance, duración/ciclos, vigencia timestamptz, límites, restricción por empresa,
   habilitación y fecha de creación. CHECKs validan condiciones y límites.
3. Crea `subscription_promotion_plans`: PK compuesta promoción/plan. Triggers
   diferidos comprueban que SELECTED conserve al menos un plan y ALL no tenga lista.
4. Crea `subscription_promotion_redemptions`: identidad de empresa, suscripción,
   plan y promoción; snapshot JSON de todas las condiciones (decimales como strings),
   ciclos concedidos/consumidos, vigencia del beneficio, fecha de confirmación y
   clave idempotente única. Índice parcial único: un beneficio vigente por suscripción.
5. Crea `subscription_charges`: empresa/suscripción/plan, número de ciclo positivo,
   período timestamptz, moneda, precio original/descuento/total Numeric(12,2),
   promoción/canje opcionales, estado ISSUED/PAID/VOID, emisión/pago y clave idempotente.
   Unicidad por suscripción/ciclo; FK compuesta obliga a coincidir con la identidad
   del canje. CHECKs impiden importes negativos, totales inconsistentes o descuentos
   sin canje; validan período y fecha de pago. Un solo canje por cargo.
6. Triggers impiden alterar snapshots e importes históricos o borrar historia,
   reiniciar canjes, modificar cargos finalizados y asociar historia a otra empresa.
   Los períodos de cargos no pueden comenzar antes del final del trial registrado.
7. Downgrade bloqueado si existen canjes o cargos: requiere un procedimiento de
   archivo expresamente autorizado. No borra silenciosamente historia económica.

La inserción futura de un canje representa una contratación confirmada por servidor;
`confirmed_at` es obligatorio. Esta fase no expone ni ejecuta esa inserción.
`granted_snapshot` prepara una copia serializable de las condiciones; editar la
promoción o el precio del plan no reescribe ningún registro histórico.

## Relaciones que requieren tratamiento especial

Todas las FK desde cargos/canjes usan RESTRICT, sin cascadas ORM. La única cascada
nueva elimina asociaciones de planes al borrar una promoción sin historia.

Ya existen `agencies → clients` y `clients → client_subscriptions` con CASCADE.
RESTRICT en la nueva historia bloquea esas eliminaciones indirectas cuando hay
historial. Será necesario tratar archivado y errores de integridad en los flujos
futuros de borrado; no se cambian esos flujos en esta fase. Un plan o promoción con
historia tampoco se podrá borrar. Se recomienda deshabilitarlos.

## Validación y límites pendientes

Los tests reutilizan explícitamente la fixture SQLite en memoria de Fase 1A,
reemplazando la fixture global destructiva de PostgreSQL. No ejecutan Alembic.
Se verifican permisos, aislamiento, validación, Decimal, límites, no stacking,
conservación del trial, snapshots, FK restrictivas, claves idempotentes y estructura DDL.

La ejecución de triggers PostgreSQL y su concurrencia queda pendiente de autorización
para una base desechable. Los previews no garantizan disponibilidad futura ni reservan
cupos: la futura contratación deberá revalidar todo bajo bloqueo de promoción y
suscripción, guardar snapshot/cargo y resolver idempotencia en una sola transacción.
No se ha implementado consumo, transición a PAID ni integración de pago.

El nuevo atributo de User necesita 0023 antes de desplegar este código. No reiniciar
la API con estos archivos y el esquema anterior. Ningún servicio se modifica aquí.

Resultado final: **58 tests passed** (promociones y suscripciones, 25.48 s),
ejecutados con la imagen API existente en contenedor desechable `--network none`,
filesystem de solo lectura y SQLite en memoria. Sintaxis Python de los seis archivos
nuevos de código validada; `git diff --check` sin errores. TypeScript/ESLint: no aplica.
No se aplicó 0023, no se ejecutó Alembic y no hubo rebuild ni reinicio de servicios.
