// Runs before every test file is loaded. Provides a valid baseline for the required public
// config values (src/config/public-config.ts) so component tests can render screens without each
// one wiring EXPO_PUBLIC_* itself. public-config.test.ts overrides/deletes these per test case to
// exercise the module's own validation.
process.env.EXPO_PUBLIC_APP_ENV ??= "local";
process.env.EXPO_PUBLIC_API_BASE_URL ??= "http://localhost:8000";
// Enable React act() environment for Testing Library
global.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
jest.setTimeout(15000);
