import { render, screen } from "@testing-library/react-native";

import { productConfig } from "@tapjob/product-config";

import "../src/i18n"; // AboutScreen uses react-i18next's useTranslation(), which needs an initialized instance.
import AboutScreen from "../src/app/about";

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

describe("<AboutScreen />", () => {
  test("renders the shell description with the product short name", async () => {
    await render(<AboutScreen />);

    expect(screen.getByText(new RegExp(productConfig.shortName))).toBeTruthy();
  });
});
