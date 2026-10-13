import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import { endpoints } from "@tapjob/contracts";

import "../../src/i18n";
import JobAlertSettingsScreen from "../../src/app/jobs/alerts";

const mockPush = jest.fn();
const mockBack = jest.fn();

jest.mock("expo-router", () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { createElement } = require("react") as typeof import("react");
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { Text } = require("react-native") as typeof import("react-native");
  return {
    useRouter: () => ({
      push: mockPush,
      replace: jest.fn(),
      back: mockBack,
    }),
    useLocalSearchParams: () => ({}),
    Link: ({ children, ...props }: { children: React.ReactNode }) =>
      createElement(Text, props, children),
  };
});

jest.mock("@tapjob/contracts", () => {
  const actual = jest.requireActual("@tapjob/contracts");
  return {
    ...actual,
    endpoints: {
      ...actual.endpoints,
      getJobAlertPreferences: jest.fn(),
      updateJobAlertPreferences: jest.fn(),
    },
  };
});

describe("<JobAlertSettingsScreen />", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  test("loads preferences and updates them upon save", async () => {
    (endpoints.getJobAlertPreferences as jest.Mock).mockResolvedValueOnce({
      data: {
        enabled: false,
        minScore: 50,
        minEligibility: "eligible",
        deliveryChannel: "whatsapp",
      },
    });
    (endpoints.updateJobAlertPreferences as jest.Mock).mockResolvedValueOnce({
      data: {
        enabled: true,
        minScore: 70,
        minEligibility: "eligible",
        deliveryChannel: "whatsapp",
      },
    });

    await render(<JobAlertSettingsScreen />);

    await waitFor(() => {
      expect(screen.getByText("Score mínimo para alerta: 50%")).toBeTruthy();
    });

    // Change min score to 70%
    fireEvent.press(screen.getByText("70%"));

    await waitFor(() => {
      expect(screen.getByText("Score mínimo para alerta: 70%")).toBeTruthy();
    });

    // Press save
    fireEvent.press(screen.getByText("Salvar preferências"));

    await waitFor(() => {
      expect(endpoints.updateJobAlertPreferences).toHaveBeenCalledWith(
        expect.objectContaining({
          body: expect.objectContaining({
            minScore: 70,
            minEligibility: "eligible",
            deliveryChannel: "whatsapp",
          }),
        }),
      );
      expect(screen.getByText("Preferências de alerta atualizadas!")).toBeTruthy();
    });
  });
});
