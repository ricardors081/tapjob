import { render, screen } from "@testing-library/react-native";

import { productConfig } from "@tapjob/product-config";

import HomeScreen from "../src/app/index";

// jest.mock factories may only reference out-of-scope values lazily via require(), not imports.
jest.mock("expo-router", () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { createElement } = require("react") as typeof import("react");
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { Text } = require("react-native") as typeof import("react-native");
  return {
    Link: ({ children, ...props }: { children: React.ReactNode }) =>
      createElement(Text, props, children),
  };
});

describe("<HomeScreen />", () => {
  test("renders product name, environment, locale and API base URL from config/tokens", async () => {
    // jest.setup.js provides EXPO_PUBLIC_APP_ENV=local and EXPO_PUBLIC_API_BASE_URL.
    await render(<HomeScreen />);

    expect(screen.getByText(`Olá, ${productConfig.productName}`)).toBeTruthy();
    expect(screen.getByText("local")).toBeTruthy();
    expect(screen.getByText("pt-BR")).toBeTruthy();
    expect(screen.getByText("http://localhost:8000")).toBeTruthy();
  });
});
