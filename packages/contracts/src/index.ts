// Public facade over the generated OpenAPI client (docs/adr/0003-api-contract-client-generation.md).
//
// Nothing outside this file may import from "./generated/*": this package's `exports` map in
// package.json only publishes ".", so a deep import is a module-resolution error, not just a lint
// warning. Re-export a generated name here deliberately when a consumer needs it.
import { type Client, type Config, createClient, createConfig } from "./generated/client";
import type { ErrorResponse } from "./generated/types.gen";

export type { Client } from "./generated/client";

// SDK functions (one per operationId, e.g. getHealth/getReadiness) and their request/response
// types. Call a function with `{ client }` from createApiClient below -- see Hey API's own
// per-call `client` option, which this facade relies on instead of a mutable global client.
export * as endpoints from "./generated/sdk.gen";
export type {
  DependencyName,
  DependencyStatus,
  Environment,
  ErrorCode,
  ErrorResponse,
  GetHealthData,
  GetHealthErrors,
  GetHealthResponses,
  GetReadinessData,
  GetReadinessErrors,
  GetReadinessResponses,
  HealthResponse,
  HealthStatus,
  ReadinessResponse,
  ReadinessStatus,
  ValidationErrorItem,
  // WP1.1 — identity & user account (docs/adr/0009-identity-auth-user-account.md)
  BootstrapAccountData,
  BootstrapAccountErrors,
  BootstrapAccountResponses,
  BootstrapResponse,
  CancelAccountDeletionData,
  CancelAccountDeletionErrors,
  CancelAccountDeletionResponses,
  ConsentDecision,
  ConsentRecordRequest,
  ConsentRecordResponse,
  DeletionRequestResponse,
  DeletionRequestStatus,
  DevicePlatform,
  DeviceRegistrationRequest,
  DeviceResponse,
  GetAccountDeletionRequestData,
  GetAccountDeletionRequestErrors,
  GetAccountDeletionRequestResponses,
  GetCurrentUserData,
  GetCurrentUserErrors,
  GetCurrentUserResponses,
  ListConsentsData,
  ListConsentsErrors,
  ListConsentsResponses,
  RecordConsentData,
  RecordConsentErrors,
  RecordConsentResponses,
  RegisterDeviceData,
  RegisterDeviceErrors,
  RegisterDeviceResponses,
  RequestAccountDeletionData,
  RequestAccountDeletionErrors,
  RequestAccountDeletionResponses,
  RevokeAllSessionsData,
  RevokeAllSessionsErrors,
  RevokeAllSessionsResponses,
  RevokeDeviceData,
  RevokeDeviceErrors,
  RevokeDeviceResponses,
  UpdateCurrentUserData,
  UpdateCurrentUserErrors,
  UpdateCurrentUserResponses,
  UpdateUserSettingsRequest,
  UserResponse,
  UserStatus,
  // WP1.2 — career profile & professional truth store (docs/adr/0010-career-profile-truth-
  // store.md)
  AddProfileFactData,
  AddProfileFactErrors,
  AddProfileFactRequest,
  AddProfileFactResponses,
  CareerProfileResponse,
  CertificationFactValue,
  EducationFactValue,
  EducationStatus,
  EmploymentType,
  EmploymentTypePreferenceValue,
  ExperienceFactValue,
  FactStatus,
  FactType,
  GetCareerProfileData,
  GetCareerProfileErrors,
  GetCareerProfileResponses,
  GetProfileFactData,
  GetProfileFactErrors,
  GetProfileFactResponses,
  GetTruthSnapshotData,
  GetTruthSnapshotErrors,
  GetTruthSnapshotResponses,
  LanguageFactValue,
  LinkFactValue,
  ListProfileFactsData,
  ListProfileFactsErrors,
  ListProfileFactsResponses,
  LocationPreferenceValue,
  PreferenceFactValue,
  ProfileFactResponse,
  ProjectFactValue,
  ReplaceProfileFactData,
  ReplaceProfileFactErrors,
  ReplaceProfileFactRequest,
  ReplaceProfileFactResponses,
  RetractProfileFactData,
  RetractProfileFactErrors,
  RetractProfileFactRequest,
  RetractProfileFactResponses,
  SalaryPeriod,
  SalaryPreferenceValue,
  SkillFactValue,
  TruthSnapshotResponse,
  UpdateCareerProfileData,
  UpdateCareerProfileErrors,
  UpdateCareerProfileRequest,
  UpdateCareerProfileResponses,
  WorkAuthorizationFactValue,
  WorkplaceType,
  WorkplaceTypePreferenceValue,
  YearMonth,
  // WP2.1 & WP2.3 — Jobs, Feed, Saved Jobs, Company Watchlist, and Alerts
  CompanyDto,
  CompanyWatchResponse,
  CursorPageFeedItemResponse,
  CursorPageSavedJobResponse,
  DismissJobData,
  DismissJobErrors,
  DismissJobResponses,
  FeedItemResponse,
  GetJobAlertPreferencesData,
  GetJobAlertPreferencesErrors,
  GetJobAlertPreferencesResponses,
  GetJobByIdData,
  GetJobByIdErrors,
  GetJobByIdResponses,
  GetJobFeedData,
  GetJobFeedErrors,
  GetJobFeedResponses,
  JobAlertPreferenceResponse,
  JobDetailResponse,
  JobMatchSummaryDto,
  JobOriginDto,
  JobProvenanceSummary,
  JobResponse,
  JobWorkplaceType,
  ListSavedJobsData,
  ListSavedJobsErrors,
  ListSavedJobsResponses,
  ListWatchedCompaniesData,
  ListWatchedCompaniesErrors,
  ListWatchedCompaniesResponses,
  SaveJobData,
  SaveJobErrors,
  SaveJobResponses,
  SavedJobResponse,
  UndismissJobData,
  UndismissJobErrors,
  UndismissJobResponses,
  UnsaveJobData,
  UnsaveJobErrors,
  UnsaveJobResponses,
  UnwatchCompanyData,
  UnwatchCompanyErrors,
  UnwatchCompanyResponses,
  UpdateJobAlertPreferenceRequest,
  UpdateJobAlertPreferencesData,
  UpdateJobAlertPreferencesErrors,
  UpdateJobAlertPreferencesResponses,
  WatchCompanyData,
  WatchCompanyErrors,
  WatchCompanyResponses,
} from "./generated/types.gen";

/** Options passed to `getAccessToken` on a retry attempt (docs/adr/0009-identity-auth-user-
 * account.md §"session lifecycle"/2026 independent-review round 2 finding 4). */
export interface GetAccessTokenOptions {
  /** `true` only on the single bounded retry after a backend-rejected (`AUTH_TOKEN_INVALID`)
   * token -- the implementation should force a fresh token from its provider rather than
   * returning the same (already-rejected) cached one. */
  forceRefresh?: boolean;
}

/** The two machine-readable `ErrorResponse.code`s that mean the current session can no longer be
 * treated as account-ready (docs/adr/0009-...md "invalid/revoked session lifecycle"). Deliberately
 * excludes `REAUTHENTICATION_REQUIRED` (a per-operation condition the caller handles itself via
 * `isApiError`/`.code` -- never a global sign-out) and `AUTH_TOKEN_MISSING` (there was never a
 * token to begin with; the app's own auth-state observer already reflects signed-out). */
export type SessionInvalidCode = "AUTH_TOKEN_REVOKED" | "AUTH_TOKEN_INVALID";

export interface ApiClientConfig {
  /** e.g. publicConfig.apiBaseUrl (apps/mobile/src/config/public-config.ts) -- never a literal. */
  baseUrl: string;
  /** Called before every request; returning a token attaches it as `Authorization: Bearer <token>`.
   * Also called a second time, with `{ forceRefresh: true }`, for the single bounded retry after
   * an `AUTH_TOKEN_INVALID` response (see `onSessionInvalid`) -- wired as a request interceptor
   * rather than through Hey API's per-operation `security`/`auth` config, so it does not depend on
   * which operations declare a security requirement. */
  getAccessToken?: (
    options?: GetAccessTokenOptions,
  ) => string | null | undefined | Promise<string | null | undefined>;
  /**
   * Called when a response's `ErrorResponse.code` means the current session is no longer usable:
   * `AUTH_TOKEN_REVOKED` immediately, or `AUTH_TOKEN_INVALID` after the one bounded forced-refresh
   * retry above still fails. This facade never signs anyone out itself -- the caller (e.g. the
   * app's `AuthService`) decides what "no longer usable" means.
   */
  onSessionInvalid?: (code: SessionInvalidCode) => void;
  /** Override for tests or an environment without a global `fetch` (default: globalThis.fetch). */
  fetch?: typeof fetch;
}

async function readErrorCode(response: Response): Promise<string | undefined> {
  // `.clone()` before reading: the ORIGINAL response's body must remain unread for the generated
  // client's own success/error parsing, which runs after every response interceptor returns.
  try {
    const body: unknown = await response.clone().json();
    return isApiError(body) ? body.code : undefined;
  } catch {
    return undefined; // not JSON, or no body -- not a recognizable ErrorResponse either way.
  }
}

function buildRetryRequest(
  request: Request,
  token: string,
  serializedBody: string | undefined,
): Request {
  const headers = new Headers(request.headers);
  headers.set("Authorization", `Bearer ${token}`);
  // Rebuilt from `serializedBody` (a plain, already-serialized string captured once per request
  // by the generated client, not a stream) rather than `request.clone()`: a `Request`'s body is a
  // single-use stream that the original `fetch()` call may already have consumed by this point.
  return new Request(request.url, { method: request.method, headers, body: serializedBody });
}

export function createApiClient(config: ApiClientConfig): Client {
  const clientConfig: Config = {
    baseUrl: config.baseUrl,
    // Conditional spread, not `fetch: config.fetch`: Config["fetch"] rejects an explicit
    // `undefined` under exactOptionalPropertyTypes, so the key must be absent rather than set to
    // undefined when no override was given.
    ...(config.fetch ? { fetch: config.fetch } : {}),
  };
  const client = createClient(createConfig(clientConfig));

  if (config.getAccessToken) {
    const getAccessToken = config.getAccessToken;
    client.interceptors.request.use(async (request) => {
      const token = await getAccessToken();
      if (token) {
        request.headers.set("Authorization", `Bearer ${token}`);
      }
      return request;
    });

    // Invalid/revoked session handling (2026 independent-review round 2 finding 4; refresh-throw
    // and second-response semantics corrected in round 3): branches on the canonical
    // ErrorResponse.code, never on "any 401 means logout" -- REAUTHENTICATION_REQUIRED also uses
    // 401 but must never trigger this path (docs/adr/0009-...md "sensitive action / recent auth").
    // Structurally bounded to at most one retry (at most 2 fetches total): the retried `fetchImpl`
    // call below is a plain `fetch`, never a re-entry into this same interceptor chain, so there is
    // no loop regardless of what the retry itself returns.
    client.interceptors.response.use(async (response, request, opts) => {
      if (response.status !== 401) {
        return response;
      }
      const code = await readErrorCode(response);
      if (code === "AUTH_TOKEN_REVOKED") {
        config.onSessionInvalid?.("AUTH_TOKEN_REVOKED");
        return response;
      }
      if (code !== "AUTH_TOKEN_INVALID") {
        // REAUTHENTICATION_REQUIRED, AUTH_TOKEN_MISSING, or any unrecognized 401 code: never
        // reclassified as session-ending, and never retried -- the caller sees it as-is.
        return response;
      }

      let refreshedToken: string | null | undefined;
      try {
        refreshedToken = await getAccessToken({ forceRefresh: true });
      } catch {
        // The provider itself failed to refresh (e.g. a real network/Firebase error) -- treated
        // the same as "no refreshed token available": terminal, and the caller still sees the
        // ORIGINAL canonical AUTH_TOKEN_INVALID ErrorResponse, never the provider's own error
        // shape (which could be unsafe to surface, e.g. a raw token leaked into an error object).
        config.onSessionInvalid?.("AUTH_TOKEN_INVALID");
        return response;
      }
      if (!refreshedToken) {
        config.onSessionInvalid?.("AUTH_TOKEN_INVALID");
        return response;
      }

      const fetchImpl = opts.fetch ?? globalThis.fetch;
      const retried = await fetchImpl(
        buildRetryRequest(request, refreshedToken, opts.serializedBody),
      );
      if (retried.status === 401) {
        // The retry's OWN code, never assumed to still be AUTH_TOKEN_INVALID: a security-sensitive
        // endpoint behind the retried (now valid) token can legitimately answer
        // REAUTHENTICATION_REQUIRED instead, which must never be misreported as a dead session.
        const retriedCode = await readErrorCode(retried);
        if (retriedCode === "AUTH_TOKEN_INVALID" || retriedCode === "AUTH_TOKEN_REVOKED") {
          config.onSessionInvalid?.(retriedCode);
        }
      }
      return retried;
    });
  }

  return client;
}

/** Narrows an unknown error value (e.g. from a `{ data, error }` SDK call result) to the canonical
 * ErrorResponse envelope (docs/ENGINEERING_STANDARDS.md §8). UI/business logic branches on
 * `.code`, never on `.message`. */
export function isApiError(value: unknown): value is ErrorResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Partial<ErrorResponse>).code === "string" &&
    typeof (value as Partial<ErrorResponse>).message === "string" &&
    typeof (value as Partial<ErrorResponse>).requestId === "string"
  );
}
