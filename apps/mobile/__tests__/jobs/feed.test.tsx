import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react-native";

import { endpoints } from "@tapjob/contracts";

import "../../src/i18n";
import JobFeedScreen from "../../src/app/jobs/index";

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
      getJobFeed: jest.fn(),
      saveJob: jest.fn(),
      unsaveJob: jest.fn(),
      dismissJob: jest.fn(),
    },
  };
});

describe("<JobFeedScreen />", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  test("renders feed items with title, company, score and eligibility", async () => {
    (endpoints.getJobFeed as jest.Mock).mockResolvedValueOnce({
      data: {
        items: [
          {
            id: "test-job-uuid-1",
            title: "Senior Backend Engineer",
            titleNormalized: "senior backend engineer",
            company: {
              id: "comp-1",
              name: "Tech Corp",
              nameNormalized: "tech corp",
              domain: "techcorp.com",
              createdAt: "2026-10-10T00:00:00Z",
              updatedAt: "2026-10-10T00:00:00Z",
            },
            locationRaw: "São Paulo, SP",
            workplaceType: "remote",
            status: "active",
            matchScore: 88,
            eligibility: "eligible",
            isSaved: false,
            createdAt: "2026-10-10T00:00:00Z",
          },
        ],
        nextCursor: null,
      },
    });

    await render(<JobFeedScreen />);

    await waitFor(() => {
      expect(screen.getByText(/Senior Backend Engineer/)).toBeTruthy();
      expect(screen.getByText(/Tech Corp/)).toBeTruthy();
      expect(screen.getByText(/88%/)).toBeTruthy();
      expect(screen.getByText(/Compatível/)).toBeTruthy();
      expect(screen.getByText(/Remoto/)).toBeTruthy();
    });
  });

  test("renders empty state when feed has no items", async () => {
    (endpoints.getJobFeed as jest.Mock).mockResolvedValueOnce({
      data: { items: [], nextCursor: null },
    });

    await render(<JobFeedScreen />);

    await waitFor(() => {
      expect(
        screen.getByText(/Nenhuma vaga encontrada para os critérios selecionados/),
      ).toBeTruthy();
    });
  });

  test("renders error state and retries on press", async () => {
    (endpoints.getJobFeed as jest.Mock).mockResolvedValueOnce({
      error: { code: "ERROR", message: "Failed" },
    });

    await render(<JobFeedScreen />);

    await waitFor(() => {
      expect(screen.getByText("Não foi possível carregar as vagas.")).toBeTruthy();
    });

    (endpoints.getJobFeed as jest.Mock).mockResolvedValueOnce({
      data: { items: [], nextCursor: null },
    });

    fireEvent.press(screen.getByText("Tentar novamente"));

    await waitFor(() => {
      expect(
        screen.getByText("Nenhuma vaga encontrada para os critérios selecionados."),
      ).toBeTruthy();
    });
  });

  test("toggles save/unsave on a job card optimistically", async () => {
    (endpoints.getJobFeed as jest.Mock).mockResolvedValueOnce({
      data: {
        items: [
          {
            id: "test-job-uuid-1",
            title: "Staff Platform Engineer",
            titleNormalized: "staff platform engineer",
            company: { id: "comp-1", name: "Inovix" },
            workplaceType: "hybrid",
            status: "active",
            matchScore: 92,
            eligibility: "eligible",
            isSaved: false,
            createdAt: "2026-10-10T00:00:00Z",
          },
        ],
        nextCursor: null,
      },
    });
    (endpoints.saveJob as jest.Mock).mockResolvedValueOnce({ data: {} });

    await render(<JobFeedScreen />);

    await waitFor(() => {
      expect(screen.getByText("Staff Platform Engineer")).toBeTruthy();
    });

    const saveBtn = screen.getByText("Salvar");
    fireEvent.press(saveBtn);

    await waitFor(() => {
      expect(endpoints.saveJob).toHaveBeenCalledWith(
        expect.objectContaining({ path: { job_id: "test-job-uuid-1" } }),
      );
      expect(screen.getByText("Salva")).toBeTruthy();
    });
  });

  test("dismisses job card from feed", async () => {
    (endpoints.getJobFeed as jest.Mock).mockResolvedValueOnce({
      data: {
        items: [
          {
            id: "test-job-uuid-1",
            title: "DevOps Specialist",
            titleNormalized: "devops specialist",
            workplaceType: "remote",
            status: "active",
            matchScore: 65,
            eligibility: "review_required",
            isSaved: false,
            createdAt: "2026-10-10T00:00:00Z",
          },
        ],
        nextCursor: null,
      },
    });
    (endpoints.dismissJob as jest.Mock).mockResolvedValueOnce({ data: {} });

    await render(<JobFeedScreen />);

    await waitFor(() => {
      expect(screen.getByText("DevOps Specialist")).toBeTruthy();
    });

    fireEvent.press(screen.getByText("Ocultar"));

    await waitFor(() => {
      expect(endpoints.dismissJob).toHaveBeenCalledWith(
        expect.objectContaining({ path: { job_id: "test-job-uuid-1" } }),
      );
      expect(screen.queryByText("DevOps Specialist")).toBeNull();
    });
  });

  test("rolls back optimistic save when server returns error", async () => {
    (endpoints.getJobFeed as jest.Mock).mockResolvedValueOnce({
      data: {
        items: [
          {
            id: "test-job-uuid-1",
            title: "Senior Backend Engineer",
            company: { id: "comp-1", name: "Inovix" },
            workplaceType: "hybrid",
            status: "active",
            matchScore: 92,
            eligibility: "eligible",
            isSaved: false,
            createdAt: "2026-10-10T00:00:00Z",
          },
        ],
        nextCursor: null,
      },
    });
    (endpoints.saveJob as jest.Mock).mockResolvedValueOnce({
      error: { code: "UNAUTHORIZED", message: "Session expired" },
    });

    await render(<JobFeedScreen />);

    await waitFor(() => {
      expect(screen.getByText("Senior Backend Engineer")).toBeTruthy();
    });

    fireEvent.press(screen.getByText("Salvar"));

    await waitFor(() => {
      expect(screen.getByText("Salvar")).toBeTruthy();
      expect(screen.getByText("Não foi possível carregar as vagas.")).toBeTruthy();
    });
  });

  test("does not trigger evaluation on each keystroke (submit explícito policy)", async () => {
    (endpoints.getJobFeed as jest.Mock).mockResolvedValueOnce({
      data: { items: [], nextCursor: null },
    });

    await render(<JobFeedScreen />);

    await waitFor(() => {
      expect(
        screen.getByText("Nenhuma vaga encontrada para os critérios selecionados."),
      ).toBeTruthy();
    });

    const searchInput = screen.getByPlaceholderText("Buscar por título...");
    fireEvent.changeText(searchInput, "p");
    fireEvent.changeText(searchInput, "py");
    fireEvent.changeText(searchInput, "python");

    // While typing, feed evaluations remain 1 (0 extra evaluations)
    expect(endpoints.getJobFeed).toHaveBeenCalledTimes(1);

    (endpoints.getJobFeed as jest.Mock).mockResolvedValueOnce({
      data: {
        items: [
          {
            id: "python-job-1",
            title: "Python Engineer Found",
            company: { id: "c1", name: "PyCorp" },
            status: "active",
            createdAt: "2026-10-10T00:00:00Z",
          },
        ],
        nextCursor: null,
      },
    });

    fireEvent(searchInput, "submitEditing", { nativeEvent: { text: "python" } });

    await waitFor(() => {
      expect(screen.getByText("Python Engineer Found")).toBeTruthy();
    });
    expect(endpoints.getJobFeed).toHaveBeenCalledTimes(2);
  });
});
