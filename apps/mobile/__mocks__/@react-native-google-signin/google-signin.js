// Manual Jest mock (docs/adr/0009-identity-auth-user-account.md §I): no real Google provider
// network in any test.
module.exports = {
  GoogleSignin: {
    configure: jest.fn(),
    hasPlayServices: jest.fn(() => Promise.resolve(true)),
    signIn: jest.fn(() => Promise.resolve({ data: { idToken: "fake-google-id-token" } })),
  },
};
