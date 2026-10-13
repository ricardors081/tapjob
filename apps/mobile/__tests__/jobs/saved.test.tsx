import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import { endpoints } from "@tapjob/contracts";

import "../../src/i18n";
import SavedJobsScreen from "../../src/app/jobs/saved";

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
      listSavedJobs: jest.fn(),
      unsaveJob: jest.fn(),
    },
  };
});

describe("<SavedJobsScreen />", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  test("renders saved jobs list and handles unsave", async () => {
    (endpoints.listSavedJobs as jest.Mock).mockResolvedValueOnce({
      data: {
        items: [
          {
            id: "saved-id-1",
            jobId: "job-1",
            state: "saved",
            createdAt: "2026-10-10T00:00:00Z",
            job: {
              id: "job-1",
              title: "Software Architect",
              company: { id: "comp-4", name: "Alpha Corp" },
              locationRaw: "Belo Horizonte, MG",
              workplaceType: "remote",
              status: "active",
              createdAt: "2026-10-10T00:00:00Z",
            },
          },
        ],
      },
    });
    (endpoints.unsaveJob as jest.Mock).mockResolvedValueOnce({ data: {} });

    await render(<SavedJobsScreen />);

    await waitFor(() => {
      expect(screen.getByText("Software Architect")).toBeTruthy();
      expect(screen.getByText("Alpha Corp")).toBeTruthy();
    });

    fireEvent.press(screen.getByText("Remover"));

    await waitFor(() => {
      expect(endpoints.unsaveJob).toHaveBeenCalledWith(
        expect.objectContaining({ path: { job_id: "job-1" } }),
      );
      expect(screen.queryByText("Software Architect")).toBeNull();
    });
  });
});
