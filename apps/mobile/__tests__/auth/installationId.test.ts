import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";

import { getOrCreateInstallationId } from "../../src/auth/installationId";

jest.mock("expo-crypto", () => ({
  randomUUID: jest.fn(() => "11111111-1111-1111-1111-111111111111"),
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
}));

beforeEach(() => {
  jest.clearAllMocks();
});

describe("getOrCreateInstallationId", () => {
  test("returns the existing id when one is already stored", async () => {
    (SecureStore.getItemAsync as jest.Mock).mockResolvedValue("already-stored-id");

    const id = await getOrCreateInstallationId();

    expect(id).toBe("already-stored-id");
    expect(Crypto.randomUUID).not.toHaveBeenCalled();
    expect(SecureStore.setItemAsync).not.toHaveBeenCalled();
  });

  test("generates and persists a new id when none is stored yet", async () => {
    (SecureStore.getItemAsync as jest.Mock).mockResolvedValue(null);

    const id = await getOrCreateInstallationId();

    expect(id).toBe("11111111-1111-1111-1111-111111111111");
    expect(SecureStore.setItemAsync).toHaveBeenCalledWith(
      expect.stringContaining("installationId"),
      "11111111-1111-1111-1111-111111111111",
    );
  });

  test("never uses a hardware identifier -- the key is namespaced by product identity, not a device ID", async () => {
    (SecureStore.getItemAsync as jest.Mock).mockResolvedValue(null);

    await getOrCreateInstallationId();

    const [key] = (SecureStore.setItemAsync as jest.Mock).mock.calls[0];
    expect(key).toMatch(/^tapjob\./);
  });
});
