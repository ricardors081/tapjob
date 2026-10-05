// Manual Jest mock (docs/adr/0009-identity-auth-user-account.md §I): no real Apple provider
// network in any test.
module.exports = {
  signInAsync: jest.fn(() => Promise.resolve({ identityToken: "fake-apple-identity-token" })),
  AppleAuthenticationScope: {
    FULL_NAME: 0,
    EMAIL: 1,
  },
};
