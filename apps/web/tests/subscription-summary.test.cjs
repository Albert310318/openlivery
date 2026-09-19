const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

// Execute the real TS modules with controlled hooks and HTTP, without a DOM dependency.
function load(relativePath, overrides = {}) {
  const source = readFileSync(path.join(__dirname, '..', relativePath), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  });
  const module = { exports: {} };
  new Function('require', 'module', 'exports', outputText)(
    (id) => overrides[id] ?? require(id), module, module.exports,
  );
  return module.exports;
}

const { subscriptions } = load('lib/i18n/dicts/subscriptions.ts');
const validSubscription = {
  id: 'sub-1', client_id: 'client-1', status: 'ACTIVE',
  first_activated_at: null, trial_started_at: null, trial_ends_at: null, next_renewal_at: null,
  plan: { id: 'plan-1', code: 'basic', name: 'Plan Básico', description: 'Descripción del plan',
    monthly_price: '25.00', currency: 'USD', is_active: true,
    available_for_new_subscriptions: true, modules: [] },
};

for (const scenario of [
  { name: 'initial undefined remains loading', pending: true, expected: 'Cargando suscripción…' },
  { name: 'HTTP 200 JSON null completes without subscription', body: null, expected: 'No se ha asignado ninguna suscripción.' },
  { name: 'HTTP 200 subscription renders plan and status', body: validSubscription, expected: 'Plan Básico' },
  { name: 'request error renders existing error state', body: { detail: 'No se pudo cargar la suscripción' }, status: 500, expected: 'No se pudo cargar la suscripción' },
]) {
  test(scenario.name, async (t) => {
    let resolveFetch;
    const fetchMock = t.mock.method(globalThis, 'fetch', () => new Promise((resolve) => { resolveFetch = resolve; }));
    const states = [];
    const effects = [];
    let cursor = 0;
    let mounted = false;
    const { SubscriptionSummary } = load('components/subscription-summary.tsx', {
      react: { ...React,
        useState(initial) {
          const index = cursor++;
          if (!(index in states)) states[index] = initial;
          return [states[index], (value) => { states[index] = value; }];
        },
        useEffect(effect) { if (!mounted) effects.push(effect); },
      },
      '@/lib/api': load('lib/api.ts'),
      '@/lib/i18n': {
        useLanguage: () => ({ lang: 'es' }),
        useT: () => (key) => key.split('.').slice(1).reduce((value, part) => value[part], subscriptions.es),
      },
      '@/components/ui': { Alert: ({ children }) => React.createElement('div', { role: 'alert' }, children) },
    });
    const render = () => {
      cursor = 0;
      return renderToStaticMarkup(SubscriptionSummary({ endpoint: '/clients/client-1/subscription' }));
    };
    assert.match(render(), /role="status">Cargando suscripción…/);
    const cleanups = effects.map((effect) => effect());
    mounted = true;
    assert.equal(fetchMock.mock.callCount(), 1);
    if (!scenario.pending) {
      resolveFetch(new Response(JSON.stringify(scenario.body), {
        status: scenario.status ?? 200, headers: { 'Content-Type': 'application/json' },
      }));
      await new Promise(setImmediate);
    }
    const html = render();
    assert.ok(html.includes(scenario.expected), html);
    if (!scenario.pending) assert.doesNotMatch(html, /Cargando suscripción/);
    if (scenario.body === validSubscription) assert.match(html, /Activa/);
    if (scenario.status) assert.match(html, /role="alert"/);
    cleanups.forEach((cleanup) => cleanup?.());
  });
}
