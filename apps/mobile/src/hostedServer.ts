/** Resolve only workspace names or addresses belonging to the configured service. */
export function resolveHostedServer(input: string, template: string): string | null {
  const value = input.trim().toLowerCase();
  const validName = (name: string) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(name);
  if (!template.includes("{workspace}") || !value) return null;
  if (validName(value)) return template.replace("{workspace}", value);
  try {
    const address = new URL(value.includes("://") ? value : `https://${value}`);
    const sample = new URL(template.replace("{workspace}", "workspace-placeholder"));
    const [prefix, suffix] = sample.hostname.split("workspace-placeholder");
    if (suffix === undefined || address.protocol !== "https:" || sample.protocol !== "https:"
        || address.username || address.password || address.search || address.hash
        || address.port !== sample.port || address.pathname !== sample.pathname
        || !address.hostname.startsWith(prefix) || !address.hostname.endsWith(suffix)) return null;
    const name = address.hostname.slice(prefix.length, suffix ? -suffix.length : undefined);
    return validName(name) ? template.replace("{workspace}", name) : null;
  } catch { return null; }
}
