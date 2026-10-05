// Manual Jest mock (docs/adr/0009-identity-auth-user-account.md §I): no test in this app ever
// contacts a real Firebase project. Dedicated auth tests configure these jest.fn()s per case via
// `.mockImplementation`/`.mockResolvedValueOnce` rather than re-mocking the module.
const mockAuth = { currentUser: null };

module.exports = {
  getAuth: jest.fn(() => mockAuth),
  // Real Firebase fires once immediately with the current (initially signed-out) user, the same
  // way the real SDK replays its last-known auth state to a freshly-registered listener.
  onAuthStateChanged: jest.fn((_auth, listener) => {
    listener(mockAuth.currentUser);
    return () => {};
  }),
  signInWithEmailAndPassword: jest.fn(() => Promise.resolve()),
  createUserWithEmailAndPassword: jest.fn(() => Promise.resolve()),
  signOut: jest.fn(() => Promise.resolve()),
  signInWithCredential: jest.fn(() => Promise.resolve()),
  getIdToken: jest.fn(() => Promise.resolve("fake-id-token")),
  GoogleAuthProvider: {
    credential: jest.fn((idToken) => ({ providerId: "google.com", token: idToken })),
  },
  OAuthProvider: jest.fn().mockImplementation((providerId) => ({
    credential: jest.fn((options) => ({ providerId, token: options?.idToken })),
  })),
};
