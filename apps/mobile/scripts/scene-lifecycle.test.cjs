const { test } = require('node:test');
const assert = require('node:assert/strict');
const { configureDelegate } = require('../plugins/withSceneLifecycle');

const legacy = `import Expo
class AppDelegate: ExpoAppDelegate {
  var window: UIWindow?
  var reactNativeFactory: RCTReactNativeFactory?
  func start() {
    reactNativeFactory = factory
#if os(iOS) || os(tvOS)
    window = UIWindow(frame: UIScreen.main.bounds)
    factory.startReactNative(
      withModuleName: "main",
      in: window,
      launchOptions: launchOptions)
#endif
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }
  func openLink() { RCTLinkingManager.application(app, open: url, options: options) }
}`;

test('scene migration retains the factory and link forwarding without starting a second React root', () => {
  const migrated = configureDelegate(legacy);
  assert.match(migrated, /ExpoAppDelegate, ExpoReactNativeFactoryProvider/);
  assert.match(migrated, /reactNativeFactory = factory/);
  assert.match(migrated, /super.application/);
  assert.match(migrated, /RCTLinkingManager.application/);
  assert.doesNotMatch(migrated, /UIWindow\(frame:|factory.startReactNative\(/);
  assert.equal(configureDelegate(migrated), migrated);
});

test('unexpected native templates fail the build instead of leaving a launch-time crash', () => {
  assert.throws(() => configureDelegate(legacy.replace('ExpoAppDelegate {', 'OtherDelegate {')), /app delegate changed/);
  assert.throws(() => configureDelegate(legacy.replace('"main"', '"custom"')), /started only/);
  assert.throws(() => configureDelegate(legacy.replace('reactNativeFactory = factory', '')), /must be retained/);
});
