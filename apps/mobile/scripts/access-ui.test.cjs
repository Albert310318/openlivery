const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const React = require('react');
const { create, act } = require('react-test-renderer');
global.IS_REACT_ACT_ENVIRONMENT = true;

function fixture({ language = 'en', permissions = [], loginError, loginWait } = {}) {
  const calls = { login: [], signedIn: [], reports: [], writes: [] };
  class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
  const session = { user_id: 'operator', permissions, branding: { brand_color: '#3456ab' } };
  const api = {
    ApiError,
    normalizeServerUrl: (url) => (url.includes('://') ? url : `https://${url}`).replace(/\/$/, ''),
    signIn: async (...args) => { calls.login.push(args); await loginWait; if (loginError) throw new ApiError('Server error', loginError); return session; },
    listTeams: async () => [{ id: 'support', name: 'Support', description: '', strategy: 'round_robin', open_count: 0, unassigned_count: 0, members: [], channels: [] }],
    listMembers: async () => [],
    getReport: async (...args) => { calls.reports.push(args); return null; },
    createTeam: async (...args) => { calls.writes.push(args); },
    updateTeam: async (...args) => { calls.writes.push(args); },
  };
  const mocks = {
    react: React,
    'react-native': {
      ...Object.fromEntries(['ActivityIndicator', 'Image', 'KeyboardAvoidingView', 'Pressable', 'RefreshControl', 'ScrollView', 'Switch', 'Text', 'TextInput', 'TouchableOpacity', 'View'].map((name) => [name, name])),
      Modal: ({ visible, children }) => visible ? React.createElement('Modal', {}, children) : null,
      Platform: { OS: 'ios', select: (v) => v.ios ?? v.default }, StyleSheet: { create: (v) => v, hairlineWidth: 1 },
      Linking: { openURL: async () => {} }, Alert: { alert: () => {} },
    },
    '@expo/vector-icons': { Ionicons: 'Icon' },
    'react-native-safe-area-context': { useSafeAreaInsets: () => ({ top: 0, bottom: 0 }) },
    'expo-localization': { getLocales: () => [{ languageCode: language }] },
    '../api': api,
    '../theme': { contrastOn: () => '#fff', readableBrand: (v) => v, tint: (v) => v, useColors: () => ({}), useIsDark: () => false },
    '../privacy': { privacyUrl: () => '', supportUrl: () => '' },
    '../../assets/icon.png': 1,
  };
  const cache = new Map();
  function load(relative) {
    const filename = path.resolve(__dirname, '..', relative);
    if (cache.has(filename)) return cache.get(filename);
    const { outputText } = ts.transpileModule(readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    });
    const module = { exports: {} };
    vm.runInNewContext(outputText, { exports: module.exports, module, console, URL, AbortController, __DEV__: false, process, require: (name) => {
      if (Object.hasOwn(mocks, name)) return mocks[name];
      if (name.startsWith('../')) return load(`src/${name.slice(3)}.ts`);
      return require(name);
    } }, { filename });
    cache.set(filename, module.exports);
    return module.exports;
  }
  mocks['../brand'] = { BRAND_COLOR: '#3456ab', BRAND_NAME: 'Example', DEFAULT_SERVER: '', HOSTED: { label: 'Example' }, hostedServerFor: (value) => load('src/hostedServer.ts').resolveHostedServer(value, 'https://{workspace}.example.com') || '' };
  let tree, Component, props;
  return {
    calls,
    async mount(screen) {
      Component = load(`src/screens/${screen}.tsx`)[screen];
      props = screen === 'SignInScreen' ? { onSignedIn: (...args) => { calls.signedIn.push(args); } } : { server: 'https://demo.example.com', session, onBack: () => {} };
      await act(async () => { tree = create(React.createElement(Component, props)); });
    },
    async revoke() { props = { ...props, session: { ...session, permissions: [] } }; await act(async () => tree.update(React.createElement(Component, props))); },
    byId: (id) => tree.root.findByProps({ testID: id }),
    buttons: () => tree.root.findAllByType('Pressable'),
    texts: () => tree.root.findAllByType('Text').map((node) => node.children.join('')),
    async fill(id, value) { await act(async () => tree.root.findByProps({ testID: id }).props.onChangeText(value)); },
    async unmount() { await act(async () => tree.unmount()); },
  };
}

test('hosted sign-in accepts a pasted hostname and prevents duplicate requests', async () => {
  let finish;
  const f = fixture({ loginWait: new Promise((resolve) => { finish = resolve; }) });
  await f.mount('SignInScreen');
  await f.fill('sign-in-workspace', 'demo.example.com');
  await f.fill('sign-in-email', ' operator@example.com ');
  await f.fill('sign-in-password', 'test-password');
  const submit = f.byId('sign-in-submit').props.onPress;
  await act(async () => { void submit(); void submit(); });
  assert.deepEqual(f.calls.login, [['https://demo.example.com', 'operator@example.com', 'test-password']]);
  assert.equal(f.byId('sign-in-submit').props.disabled, true);
  await act(async () => { finish(); });
  assert.equal(f.calls.signedIn.length, 1);
  await f.unmount();
});

test('hosted sign-in never sends credentials to an unrelated pasted address', async () => {
  const f = fixture(); await f.mount('SignInScreen');
  await f.fill('sign-in-workspace', 'https://unrelated.example.net');
  await f.fill('sign-in-email', 'operator@example.com'); await f.fill('sign-in-password', 'test-password');
  await act(async () => f.byId('sign-in-submit').props.onPress());
  assert.equal(f.calls.login.length, 0);
  assert.ok(f.texts().some((text) => text.includes('workspace name')));
  await f.unmount();
});

for (const status of [401, 404]) test(`sign-in explains HTTP ${status} in Spanish`, async () => {
  const f = fixture({ language: 'es', loginError: status }); await f.mount('SignInScreen');
  await f.fill('sign-in-workspace', 'demo'); await f.fill('sign-in-email', 'operator@example.com'); await f.fill('sign-in-password', 'test-password');
  await act(async () => f.byId('sign-in-submit').props.onPress());
  assert.equal(f.calls.signedIn.length, 0);
  assert.ok(f.texts().some((text) => status === 401 ? text.includes('contraseña') : text.includes('disponible')));
  assert.equal(f.byId('sign-in-submit').props.disabled, false);
  await f.unmount();
});

test('operators without grants can read teams without report requests or management controls', async () => {
  const f = fixture(); await f.mount('WorkspaceScreen');
  assert.ok(f.texts().includes('Support'));
  assert.ok(!f.texts().includes('Reports'));
  assert.ok(!f.texts().includes('New team'));
  const team = f.buttons().find((node) => node.props.accessibilityLabel === 'Support');
  assert.equal(team.props.disabled, true);
  await act(async () => team.props.onPress());
  assert.equal(f.calls.reports.length, 0); assert.equal(f.calls.writes.length, 0);
  assert.ok(!f.texts().includes('Save team'));
  await f.unmount();
});

test('revoking report access unmounts the report and aborts its request', async () => {
  const f = fixture({ permissions: ['reports.view', 'teams.manage'] }); await f.mount('WorkspaceScreen');
  assert.ok(f.texts().includes('New team'));
  const reportTab = f.buttons().find((node) => node.props.accessibilityRole === 'tab' && node.findAllByType('Text').some((text) => text.children.includes('Reports')));
  await act(async () => reportTab.props.onPress());
  assert.equal(f.calls.reports.length, 1);
  const signal = f.calls.reports[0][2].signal;
  await f.revoke();
  assert.equal(signal.aborted, true);
  assert.ok(!f.texts().includes('Reports')); assert.ok(!f.texts().includes('New team'));
  assert.ok(f.texts().includes('Support'));
  await f.unmount();
});

test('revoking team management hides an open editor without writing', async () => {
  const f = fixture({ permissions: ['teams.manage'] }); await f.mount('WorkspaceScreen');
  await act(async () => f.buttons().find((node) => node.props.accessibilityLabel === 'Edit team: Support').props.onPress());
  assert.ok(f.texts().includes('Save team'));
  await f.revoke();
  assert.ok(!f.texts().includes('Save team')); assert.equal(f.calls.writes.length, 0);
  await f.unmount();
});
