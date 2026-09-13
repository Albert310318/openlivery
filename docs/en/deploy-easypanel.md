# Deploy OpenLivery with Easypanel

Use Easypanel's **Compose service** with
[`docker-compose.easypanel.yml`](../../docker-compose.easypanel.yml). It starts
PostgreSQL, the API, web frontend, WhatsApp bridge, and Caddy gateway together.
One installation serves one agency and its clients.

The template uses the published application images, currently `linux/amd64`.
Use an x86-64 Linux server with Easypanel and Docker Compose v2. Other CPU
architectures can use the [standard source build](self-hosting.md#install).

## Prepare the configuration

Clone the public repository on a trusted computer and generate fresh secrets:

```bash
git clone https://github.com/sarrazola/openlivery.git
cd openlivery
./scripts/generate-docker-env.sh
```

For a fresh installation, copy the generated values into Easypanel's environment
editor. Do not commit `.env.docker`. For an existing installation, retain its
original secrets, especially `ENCRYPTION_KEY` and its database password.

Set `FRONTEND_URL` to the public HTTPS origin, for example
`https://agency.example.com`. The template sets `COOKIE_SECURE=true` and
`COOKIE_SAMESITE=lax` for HTTPS, independently of the local Docker example's
cookie values.

| Environment value | Purpose |
| --- | --- |
| `POSTGRES_DB` | Database name; defaults to `openlivery`. |
| `POSTGRES_USER` | Database user; defaults to `openlivery`. |
| `POSTGRES_PASSWORD` | Use the generated hexadecimal password so the database URL remains valid. |
| `SECRET_KEY` | Session-signing secret generated for this installation. |
| `ENCRYPTION_KEY` | Original encryption key; retain it across redeploys and restores. |
| `WHATSAPP_BRIDGE_TOKEN` | Shared secret used by the API and bridge. |
| `FRONTEND_URL` | Public HTTPS origin. |
| `OPENLIVERY_VERSION` | One published tag for all three application images; defaults to `latest`. Prefer a release tag or `sha-<short-commit>` for repeatable upgrades. |
| `ACCESS_TOKEN_MINUTES` | Optional session lifetime; defaults to `10080`. |

The template supplies internal database and bridge URLs. OpenLivery uses backend
JWT sessions; it does not require NextAuth variables or a Redis service. The
prebuilt frontend calls relative `/api`, so `NEXT_PUBLIC_API_URL` is not needed.
Host-port variables from `.env.docker` are unused by this template.

## Create and deploy the Compose service

Following the [official Compose service workflow](https://easypanel.io/docs/services/compose):

1. In an Easypanel project, create a **Compose** service and select **Git** as
   its source.
2. Set repository URL to `https://github.com/sarrazola/openlivery.git`, branch
   to `main`, build path to `/`, and Compose file to
   `docker-compose.easypanel.yml`. The Git checkout also supplies
   `docker/Caddyfile`; the template is not a standalone inline YAML paste.
3. Replace any automatically imported development `.env.example` values with
   the generated environment described above. Enable **Create .env file** so
   Compose can interpolate them. Keep secrets in the panel, not in the repository.
4. Deploy. The API applies Alembic migrations before starting. Check the
   deployment output and each service's health and logs.
5. Add your domain, select internal service **`proxy`**, container port **`80`**,
   and internal protocol **HTTP**. Enable the public HTTPS certificate and point
   the hostname's DNS at the server. TLS terminates at Easypanel's proxy.

Do not route the public domain directly to `web:3000`: the Caddy gateway also
routes `/api/*` to the API. Do not publish the database, API, or bridge ports.
The template has neither fixed `container_name` values nor published host ports,
so separate Compose projects do not fight over those names or ports.

For a private fork, use the service-specific SSH deploy key shown by Easypanel
and grant it read-only repository access. Public GitHub source needs no token.

## Verify the installation

- Open `https://agency.example.com/login`; confirm the certificate is valid.
- Visit `https://agency.example.com/api/auth/status`. A new installation reports
  `needs_setup: true` and `registration_open: true`.
- Create the agency owner, reload, and confirm sign-in persists. The same status
  endpoint then reports that registration is closed.
- Check that the session cookie is `HttpOnly` and `Secure` in browser developer
  tools. HTTP-only access will not support the secure session cookie.
- Upload a harmless document when configuring an agent, then redeploy and confirm
  the file is still accessible.
- Connect a WhatsApp number only when ready to test real messaging. The bridge
  stores its session in the bundled PostgreSQL's `whatsmeow` schema. Keep one
  bridge instance per store; separate live instances must not share its sessions.

## Backups and restores

`postgres_data` contains the application data, including built-in PDF and
attachment bytes. `backend_storage` preserves any files placed in the API's
storage mount by extensions; it can be empty. Preserve the original environment
separately. Follow the
[complete database and upload backup procedure](self-hosting.md#backups), using
the actual Easypanel Compose project rather than starting a second installation.

On the server, identify the API container in Easypanel's container list. Its
Compose labels show the exact project, working directory, and configuration files:

```bash
api_container=REPLACE_WITH_API_CONTAINER_ID
docker inspect "$api_container" --format '{{index .Config.Labels "com.docker.compose.project"}}'
docker inspect "$api_container" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
docker inspect "$api_container" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
```

Change into that working directory, where the Git-backed Compose file and the
panel-generated `.env` reside, and set `compose_project` to the first output.
Use this helper **instead of** the default helper in the self-hosting guide.
Set `compose_file` to the configuration path shown by the last command. If that
label lists multiple files, include each one as a separate `-f` argument in the
same order, including any panel-generated override. Do not substitute the
repository template for a different configuration used by the running service:

```bash
compose_project=REPLACE_WITH_COMPOSE_PROJECT
compose_file=REPLACE_WITH_COMPOSE_CONFIG_PATH
dc() { docker compose --project-name "$compose_project" --env-file .env -f "$compose_file" "$@"; }
```

Pause panel-triggered deployments during the backup or restore window. Easypanel
maintenance mode alone does not stop database or file writers; the backup
procedure stops `api` and `whatsapp` explicitly. Restore only into the selected
destination, with writers stopped and an empty upload volume. Keep the project
name and volume definitions stable when redeploying.

## Upgrade and troubleshoot

Back up first, then select the new published image tag in `OPENLIVERY_VERSION`
and redeploy. Keep all three application images on the same version. The API
runs migrations automatically; changing the environment without redeploying
does not update running containers. Verify sign-in and file access afterwards.

| Symptom | Check |
| --- | --- |
| `/login` works but `/api/auth/status` fails | The domain must target `proxy:80`, not the web service. Inspect proxy and API logs. |
| Login loops | Use HTTPS, keep the original `SECRET_KEY`, and verify the browser accepts the secure session cookie. |
| API does not become healthy | Check database readiness, credentials, and the API's migration output before restarting repeatedly. |
| Data disappears after a redeploy | Check that the project name and `postgres_data` volume are unchanged. For extension-managed files, also check `backend_storage` at `/app/backend/storage`. |
| WhatsApp loses its session | Verify the same database and `whatsmeow` schema are retained and only one bridge uses them. |
| Missing-variable error during deployment | Save all required generated secrets in the environment editor and enable `.env` creation. |

This guide covers the primary application domain. Client portal custom domains
need their own proxy/domain configuration; do not add the standalone Caddy
host-port override to an Easypanel deployment. See the
[custom-domain architecture](self-hosting.md#custom-domains-for-client-portals)
before configuring that optional feature.
