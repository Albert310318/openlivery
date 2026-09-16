const { withAppDelegate, withInfoPlist } = require("expo/config-plugins");

/** Use Expo's scene lifecycle, including its linking and app-event forwarding. */
function configureDelegate(contents) {
  if (!contents.includes("ExpoReactNativeFactoryProvider")) {
    const declaration = "class AppDelegate: ExpoAppDelegate {";
    if (!contents.includes(declaration)) {
      throw new Error("The generated app delegate changed; review scene lifecycle configuration before building.");
    }
    contents = contents.replace(declaration, "class AppDelegate: ExpoAppDelegate, ExpoReactNativeFactoryProvider {");
  }
  // ExpoAppSceneDelegate creates a window from the connecting UIWindowScene.
  // Starting React here as well would create a second root outside that scene.
  contents = contents.replace(
    /#if os\(iOS\) \|\| os\(tvOS\)\s+window = UIWindow\(frame: UIScreen\.main\.bounds\)\s+factory\.startReactNative\(\s+withModuleName: "main",\s+in: window,\s+launchOptions: launchOptions\)\s+#endif/,
    "// ExpoAppSceneDelegate creates the window and starts the retained factory.",
  );
  if (contents.includes("factory.startReactNative(") || !contents.includes("reactNativeFactory = factory")) {
    throw new Error("The React Native factory must be retained and started only by the scene delegate.");
  }
  return contents;
}

module.exports = function withSceneLifecycle(config) {
  config = withInfoPlist(config, (mod) => {
    mod.modResults.UIApplicationSceneManifest = {
      UIApplicationSupportsMultipleScenes: false,
      UISceneConfigurations: {
        UIWindowSceneSessionRoleApplication: [{
          UISceneConfigurationName: "Default Configuration",
          // This is the Objective-C runtime name exported by Expo SDK 57.
          UISceneDelegateClassName: "EXExpoAppSceneDelegate",
        }],
      },
    };
    return mod;
  });
  return withAppDelegate(config, (mod) => {
    if (mod.modResults.language !== "swift") throw new Error("The scene lifecycle requires a Swift app delegate.");
    mod.modResults.contents = configureDelegate(mod.modResults.contents);
    return mod;
  });
};

module.exports.configureDelegate = configureDelegate;
