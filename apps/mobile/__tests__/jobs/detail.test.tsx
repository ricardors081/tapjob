import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import { endpoints } from "@tapjob/contracts";

import "../../src/i18n";
import JobDetailScreen, { isAllowedExternalJobUrl } from "../../src/app/jobs/[id]";

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
    useLocalSearchParams: () => ({ id: "test-job-uuid-1" }),
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
      getJobById: jest.fn(),
      watchCompany: jest.fn(),
      unwatchCompany: jest.fn(),
      saveJob: jest.fn(),
      unsaveJob: jest.fn(),
    },
  };
});

describe("<JobDetailScreen />", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  test("renders job detail with WP2.2 explainability, strengths, gaps and blockers", async () => {
    (endpoints.getJobById as jest.Mock).mockResolvedValueOnce({
      data: {
        id: "test-job-uuid-1",
        title: "Principal Architect",
        descriptionRaw: "We are seeking a seasoned architect with AWS and Python mastery.",
        company: { id: "comp-2", name: "Global Systems" },
        locationRaw: "Curitiba, PR",
        workplaceType: "hybrid",
        status: "active",
        createdAt: "2026-10-10T00:00:00Z",
        origins: [{ canonicalUrl: "https://example.com/apply/1" }],
        isSaved: false,
        match: {
          overallScore: 94,
          eligibility: "eligible",
          strengths: [{ name: "Domínio de Python", description: "5 anos de experiência" }],
          missingRequirements: [{ name: "Conhecimento de Terraform", description: "Desejável" }],
          blockers: [],
        },
      },
    });

    await render(<JobDetailScreen />);

    await waitFor(() => {
      expect(screen.getByText(/Principal Architect/)).toBeTruthy();
      expect(screen.getByText(/Global Systems/)).toBeTruthy();
      expect(screen.getByText(/94%/)).toBeTruthy();
      expect(screen.getByText(/Domínio de Python/)).toBeTruthy();
      expect(screen.getByText(/Conhecimento de Terraform/)).toBeTruthy();
      expect(
        screen.getByText(/We are seeking a seasoned architect with AWS and Python mastery./),
      ).toBeTruthy();
    });
  });

  test("handles watch company button click", async () => {
    (endpoints.getJobById as jest.Mock).mockResolvedValueOnce({
      data: {
        id: "test-job-uuid-1",
        title: "Frontend Lead",
        descriptionRaw: "React and React Native mastery.",
        company: { id: "comp-3", name: "Apex Technologies" },
        workplaceType: "remote",
        status: "active",
        createdAt: "2026-10-10T00:00:00Z",
        origins: [],
        isSaved: false,
        match: null,
      },
    });
    (endpoints.watchCompany as jest.Mock).mockResolvedValueOnce({ data: {} });

    await render(<JobDetailScreen />);

    await waitFor(() => {
      expect(screen.getByText("Seguir empresa")).toBeTruthy();
    });

    fireEvent.press(screen.getByText("Seguir empresa"));

    await waitFor(() => {
      expect(endpoints.watchCompany).toHaveBeenCalledWith(
        expect.objectContaining({ path: { company_id: "comp-3" } }),
      );
      expect(screen.getByText("Seguindo")).toBeTruthy();
    });
  });

  test("initializes watch company state to watched when isCompanyWatched is true", async () => {
    (endpoints.getJobById as jest.Mock).mockResolvedValueOnce({
      data: {
        id: "test-job-uuid-1",
        title: "Frontend Lead",
        company: { id: "comp-3", name: "Apex Technologies" },
        isSaved: false,
        isCompanyWatched: true,
        status: "active",
        createdAt: "2026-10-10T00:00:00Z",
      },
    });

    await render(<JobDetailScreen />);

    await waitFor(() => {
      expect(screen.getByText("Seguindo")).toBeTruthy();
    });
  });

  test("validates external URLs and rejects non-http schemes", () => {
    expect(isAllowedExternalJobUrl("https://jobs.example.com/apply")).toBe(true);
    expect(isAllowedExternalJobUrl("http://jobs.example.com/apply")).toBe(true);
    expect(isAllowedExternalJobUrl("javascript:alert(1)")).toBe(false);
    expect(isAllowedExternalJobUrl("intent://example")).toBe(false);
    expect(isAllowedExternalJobUrl("file:///etc/passwd")).toBe(false);
    expect(isAllowedExternalJobUrl("tel:123456")).toBe(false);
    expect(isAllowedExternalJobUrl("custom://app")).toBe(false);
    expect(isAllowedExternalJobUrl("")).toBe(false);
    expect(isAllowedExternalJobUrl(null)).toBe(false);
  });
});
