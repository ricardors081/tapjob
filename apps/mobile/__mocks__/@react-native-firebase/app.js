// Manual Jest mock (docs/adr/0009-identity-auth-user-account.md §I): no test in this app ever
// loads the real native Firebase module -- Jest applies this automatically for every test that
// imports `@react-native-firebase/app`, directly or transitively.
const mockApp = { name: "[DEFAULT]" };

module.exports = {
  getApp: jest.fn(() => mockApp),
};
