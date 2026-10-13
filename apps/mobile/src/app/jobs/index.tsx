import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useRouter } from "expo-router";
import { useTranslation } from "react-i18next";
import {
  ActivityIndicator,
  FlatList,
  Modal,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { endpoints, type FeedItemResponse, type JobWorkplaceType } from "@tapjob/contracts";
import { themes, tokens } from "@tapjob/design-tokens";

import { apiClient } from "../../auth/apiClient";

const palette = themes.light;

function getScoreColor(score: number | null | undefined): string {
  if (score == null) return palette.color.text.secondary;
  if (score >= 70) return palette.color.feedback.success.foreground;
  if (score >= 50) return palette.color.feedback.warning.foreground;
  return palette.color.feedback.error.foreground;
}

interface AppliedFilters {
  query: string;
  company?: string;
  location?: string;
  workplaceType?: JobWorkplaceType;
  minScore?: number;
  savedOnly?: boolean;
}

export default function JobFeedScreen() {
  const { t } = useTranslation();
  const router = useRouter();

  const [items, setItems] = useState<FeedItemResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Search draft state
  const [searchDraft, setSearchDraft] = useState("");

  // Applied filters state (single source of truth for queries)
  const [appliedFilters, setAppliedFilters] = useState<AppliedFilters>({ query: "" });

  // Filter modal draft state
  const [filterModalVisible, setFilterModalVisible] = useState(false);
  const [draftCompany, setDraftCompany] = useState("");
  const [draftLocation, setDraftLocation] = useState("");
  const [draftWorkplaceType, setDraftWorkplaceType] = useState<JobWorkplaceType | undefined>(
    undefined,
  );
  const [draftMinScore, setDraftMinScore] = useState<number | undefined>(undefined);
  const [draftSavedOnly, setDraftSavedOnly] = useState(false);

  // Stale request guard ref & mount lifecycle ref
  const requestSeqRef = useRef(0);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const handleSearchSubmit = (e?: any) => {
    const trimmed = (e?.nativeEvent?.text ?? searchDraft).trim();
    setAppliedFilters((prev) => {
      if (prev.query === trimmed) return prev;
      return { ...prev, query: trimmed };
    });
  };

  const fetchFeed = useCallback(
    async (cursor: string | null = null, isRefresh = false) => {
      const currentSeq = cursor ? requestSeqRef.current : ++requestSeqRef.current;
      await Promise.resolve();
      try {
        setError(null);
        if (cursor) setLoadingMore(true);
        else if (!isRefresh) setLoading(true);

        const response = await endpoints.getJobFeed({
          client: apiClient,
          query: {
            q: appliedFilters.query || undefined,
            company: appliedFilters.company,
            location: appliedFilters.location,
            workplaceType: appliedFilters.workplaceType,
            minScore: appliedFilters.minScore,
            savedOnly: appliedFilters.savedOnly,
            cursor: cursor || undefined,
            limit: 20,
          },
        });

        // Guard against stale response mixing or unmounted component
        if (!isMountedRef.current || currentSeq !== requestSeqRef.current) return;

        if (response.data && !response.error) {
          const newItems = response.data.items as FeedItemResponse[];
          if (cursor) {
            setItems((prev) => [...prev, ...newItems]);
          } else {
            setItems(newItems);
          }
          setNextCursor(response.data.nextCursor ?? null);
        } else {
          setError(t("jobs.errorLoading"));
        }
      } catch {
        if (isMountedRef.current && currentSeq === requestSeqRef.current) {
          setError(t("jobs.errorLoading"));
        }
      } finally {
        if (isMountedRef.current && currentSeq === requestSeqRef.current) {
          setLoading(false);
          if (isRefresh) setRefreshing(false);
          if (cursor) setLoadingMore(false);
        }
      }
    },
    [appliedFilters, t],
  );

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchFeed(null);
  }, [fetchFeed]);

  const onRefresh = () => {
    setRefreshing(true);
    void fetchFeed(null, true);
  };

  const onEndReached = () => {
    if (nextCursor && !loadingMore && !loading) {
      void fetchFeed(nextCursor);
    }
  };

  const handleToggleSave = async (job: FeedItemResponse) => {
    const isCurrentlySaved = job.isSaved;
    setItems((prev) =>
      prev.map((it) => (it.id === job.id ? { ...it, isSaved: !isCurrentlySaved } : it)),
    );

    try {
      const response = isCurrentlySaved
        ? await endpoints.unsaveJob({
            client: apiClient,
            path: { job_id: job.id },
          })
        : await endpoints.saveJob({
            client: apiClient,
            path: { job_id: job.id },
          });

      if (response.error) {
        // Rollback optimistic state
        setItems((prev) =>
          prev.map((it) => (it.id === job.id ? { ...it, isSaved: isCurrentlySaved } : it)),
        );
        setError(t("jobs.errorLoading"));
      }
    } catch {
      setItems((prev) =>
        prev.map((it) => (it.id === job.id ? { ...it, isSaved: isCurrentlySaved } : it)),
      );
      setError(t("jobs.errorLoading"));
    }
  };

  const handleDismiss = async (job: FeedItemResponse) => {
    setItems((prev) => prev.filter((it) => it.id !== job.id));

    try {
      const response = await endpoints.dismissJob({
        client: apiClient,
        path: { job_id: job.id },
      });
      if (response.error) {
        // Rollback optimistic removal
        setItems((prev) => [job, ...prev]);
        setError(t("jobs.errorLoading"));
      }
    } catch {
      setItems((prev) => [job, ...prev]);
      setError(t("jobs.errorLoading"));
    }
  };

  const handleOpenFilterModal = () => {
    setDraftCompany(appliedFilters.company || "");
    setDraftLocation(appliedFilters.location || "");
    setDraftWorkplaceType(appliedFilters.workplaceType);
    setDraftMinScore(appliedFilters.minScore);
    setDraftSavedOnly(Boolean(appliedFilters.savedOnly));
    setFilterModalVisible(true);
  };

  const handleApplyFilters = () => {
    setFilterModalVisible(false);
    setAppliedFilters((prev) => ({
      ...prev,
      company: draftCompany.trim() || undefined,
      location: draftLocation.trim() || undefined,
      workplaceType: draftWorkplaceType,
      minScore: draftMinScore,
      savedOnly: draftSavedOnly,
    }));
  };

  const handleClearFilters = () => {
    setDraftCompany("");
    setDraftLocation("");
    setDraftWorkplaceType(undefined);
    setDraftMinScore(undefined);
    setDraftSavedOnly(false);
    setFilterModalVisible(false);
    setAppliedFilters((prev) => ({ query: prev.query }));
  };

  const hasActiveFilters = Boolean(
    appliedFilters.company ||
    appliedFilters.location ||
    appliedFilters.workplaceType ||
    appliedFilters.minScore ||
    appliedFilters.savedOnly,
  );

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>{t("jobs.feedTitle")}</Text>
        <View style={styles.headerActions}>
          <Link href="/jobs/saved" asChild>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={t("jobs.savedTitle")}
              style={styles.navButton}
            >
              <Text style={styles.navButtonText}>{t("jobs.savedTitle")}</Text>
            </Pressable>
          </Link>
          <Link href="/jobs/alerts" asChild>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={t("jobs.alertsTitle")}
              style={styles.navButton}
            >
              <Text style={styles.navButtonText}>{t("jobs.alertsTitle")}</Text>
            </Pressable>
          </Link>
        </View>
      </View>

      <View style={styles.searchSection}>
        <TextInput
          style={styles.searchInput}
          placeholder={t("jobs.searchPlaceholder")}
          placeholderTextColor={palette.color.text.secondary}
          value={searchDraft}
          onChangeText={setSearchDraft}
          onSubmitEditing={handleSearchSubmit}
          returnKeyType="search"
        />
        <Pressable
          accessibilityRole="button"
          onPress={handleOpenFilterModal}
          style={[styles.filterButton, hasActiveFilters && styles.filterButtonActive]}
        >
          <Text
            style={[styles.filterButtonText, hasActiveFilters && styles.filterButtonTextActive]}
          >
            {hasActiveFilters
              ? t("jobs.filterButton") + t("jobs.filterActiveSuffix")
              : t("jobs.filterButton")}
          </Text>
        </Pressable>
      </View>

      {loading ? (
        <View style={styles.centerContainer}>
          <ActivityIndicator size="large" color={palette.color.action.primary.background} />
          <Text style={styles.loadingText}>{t("jobs.loading")}</Text>
        </View>
      ) : error && items.length === 0 ? (
        <View style={styles.centerContainer}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable
            accessibilityRole="button"
            onPress={() => {
              setLoading(true);
              void fetchFeed(null);
            }}
            style={styles.retryButton}
          >
            <Text style={styles.retryButtonText}>{t("jobs.retry")}</Text>
          </Pressable>
        </View>
      ) : (
        <>
          {error ? (
            <View style={styles.inlineErrorContainer}>
              <Text style={styles.errorText}>{error}</Text>
            </View>
          ) : null}
          <FlatList
            data={items}
            keyExtractor={(item) => item.id}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
            onEndReached={onEndReached}
            onEndReachedThreshold={0.5}
            contentContainerStyle={
              items.length === 0 ? styles.emptyListContent : styles.listContent
            }
            ListEmptyComponent={
              <View style={styles.centerContainer}>
                <Text style={styles.emptyText}>{t("jobs.emptyFeed")}</Text>
              </View>
            }
            ListFooterComponent={
              loadingMore ? (
                <View style={styles.footerLoader}>
                  <ActivityIndicator size="small" color={palette.color.action.primary.background} />
                </View>
              ) : null
            }
            renderItem={({ item }) => (
              <Pressable
                accessibilityRole="button"
                onPress={() => router.push(`/jobs/${item.id}`)}
                style={styles.jobCard}
              >
                <View style={styles.cardHeader}>
                  <View style={styles.titleArea}>
                    <Text style={styles.jobTitle} numberOfLines={2}>
                      {item.title}
                    </Text>
                    <Text style={styles.companyName}>
                      {item.company
                        ? item.company.name
                        : t("jobs.companyFallback", { defaultValue: "Empresa" })}
                    </Text>
                  </View>
                  {item.matchScore != null && (
                    <View
                      style={[styles.scoreBadge, { borderColor: getScoreColor(item.matchScore) }]}
                    >
                      <Text style={[styles.scoreText, { color: getScoreColor(item.matchScore) }]}>
                        {t("jobs.percentValue", { value: item.matchScore })}
                      </Text>
                    </View>
                  )}
                </View>

                <View style={styles.metaRow}>
                  {item.workplaceType && (
                    <Text style={styles.metaTag}>
                      {t(`jobs.workplace.${item.workplaceType}`, {
                        defaultValue: item.workplaceType,
                      })}
                    </Text>
                  )}
                  {item.locationRaw && (
                    <Text style={styles.metaText} numberOfLines={1}>
                      {t("jobs.locationPrefix")} {item.locationRaw}
                    </Text>
                  )}
                  {item.eligibility && (
                    <Text
                      style={[
                        styles.eligibilityTag,
                        item.eligibility === "eligible"
                          ? styles.eligibilityEligible
                          : item.eligibility === "review_required"
                            ? styles.eligibilityReview
                            : styles.eligibilityIneligible,
                      ]}
                    >
                      {t(`jobs.eligibility.${item.eligibility}`, {
                        defaultValue: item.eligibility,
                      })}
                    </Text>
                  )}
                </View>

                <View style={styles.cardActions}>
                  <Pressable
                    accessibilityRole="button"
                    onPress={(e) => {
                      e.stopPropagation();
                      void handleToggleSave(item);
                    }}
                    style={[styles.actionBtn, item.isSaved && styles.actionBtnSaved]}
                  >
                    <Text style={[styles.actionBtnText, item.isSaved && styles.actionBtnTextSaved]}>
                      {item.isSaved ? t("jobs.saved") : t("jobs.save")}
                    </Text>
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    onPress={(e) => {
                      e.stopPropagation();
                      void handleDismiss(item);
                    }}
                    style={styles.actionBtn}
                  >
                    <Text style={styles.actionBtnText}>{t("jobs.dismiss")}</Text>
                  </Pressable>
                </View>
              </Pressable>
            )}
          />
        </>
      )}

      <Modal
        visible={filterModalVisible}
        animationType="slide"
        transparent
        onRequestClose={() => setFilterModalVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>{t("jobs.filterModalTitle")}</Text>

            <TextInput
              style={styles.modalInput}
              placeholder={t("jobs.filterCompanyPlaceholder")}
              placeholderTextColor={palette.color.text.secondary}
              value={draftCompany}
              onChangeText={setDraftCompany}
            />

            <TextInput
              style={styles.modalInput}
              placeholder={t("jobs.filterLocationPlaceholder")}
              placeholderTextColor={palette.color.text.secondary}
              value={draftLocation}
              onChangeText={setDraftLocation}
            />

            <Text style={styles.filterSectionTitle}>{t("jobs.filterButton")}</Text>
            <View style={styles.pillRow}>
              {(["remote", "hybrid", "on_site"] as JobWorkplaceType[]).map((type) => (
                <Pressable
                  key={type}
                  accessibilityRole="button"
                  onPress={() =>
                    setDraftWorkplaceType((prev) => (prev === type ? undefined : type))
                  }
                  style={[
                    styles.pillButton,
                    draftWorkplaceType === type && styles.pillButtonActive,
                  ]}
                >
                  <Text
                    style={[
                      styles.pillButtonText,
                      draftWorkplaceType === type && styles.pillButtonTextActive,
                    ]}
                  >
                    {t(`jobs.workplace.${type}`)}
                  </Text>
                </Pressable>
              ))}
            </View>

            <Text style={styles.filterSectionTitle}>{t("jobs.filterMinScoreLabel")}</Text>
            <View style={styles.pillRow}>
              {[50, 70, 85].map((score) => (
                <Pressable
                  key={score}
                  accessibilityRole="button"
                  onPress={() => setDraftMinScore((prev) => (prev === score ? undefined : score))}
                  style={[styles.pillButton, draftMinScore === score && styles.pillButtonActive]}
                >
                  <Text
                    style={[
                      styles.pillButtonText,
                      draftMinScore === score && styles.pillButtonTextActive,
                    ]}
                  >
                    {t("jobs.scorePlus", { score })}
                  </Text>
                </Pressable>
              ))}
            </View>

            <Pressable
              accessibilityRole="button"
              onPress={() => setDraftSavedOnly((prev) => !prev)}
              style={styles.checkboxRow}
            >
              <Text style={styles.checkboxLabel}>
                {draftSavedOnly ? t("jobs.checked") : t("jobs.unchecked")}{" "}
                {t("jobs.filterSavedOnlyLabel")}
              </Text>
            </Pressable>

            <View style={styles.modalActions}>
              <Pressable
                accessibilityRole="button"
                onPress={handleClearFilters}
                style={styles.modalSecondaryBtn}
              >
                <Text style={styles.modalSecondaryBtnText}>{t("jobs.filterClear")}</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                onPress={handleApplyFilters}
                style={styles.modalPrimaryBtn}
              >
                <Text style={styles.modalPrimaryBtnText}>{t("jobs.filterApply")}</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.color.background.canvas,
  },
  header: {
    paddingHorizontal: tokens.spacing.lg,
    paddingTop: tokens.spacing.xl,
    paddingBottom: tokens.spacing.md,
    backgroundColor: palette.color.background.surface,
    borderBottomWidth: tokens.borderWidth.hairline,
    borderBottomColor: palette.color.border.default,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  title: {
    fontSize: tokens.typography.fontSize.heading,
    fontFamily: tokens.typography.fontFamily.primaryBold,
    color: palette.color.text.primary,
  },
  headerActions: {
    flexDirection: "row",
    gap: tokens.spacing.sm,
  },
  navButton: {
    paddingVertical: tokens.spacing.xs,
    paddingHorizontal: tokens.spacing.sm,
    borderRadius: tokens.radius.sm,
    backgroundColor: palette.color.background.canvas,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
  },
  navButtonText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.secondary,
  },
  searchSection: {
    flexDirection: "row",
    paddingHorizontal: tokens.spacing.lg,
    paddingVertical: tokens.spacing.md,
    gap: tokens.spacing.sm,
    backgroundColor: palette.color.background.surface,
  },
  searchInput: {
    flex: 1,
    backgroundColor: palette.color.background.canvas,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    borderRadius: tokens.radius.md,
    paddingHorizontal: tokens.spacing.md,
    paddingVertical: tokens.spacing.xs,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.primary,
    fontSize: tokens.typography.fontSize.body,
  },
  filterButton: {
    paddingHorizontal: tokens.spacing.md,
    paddingVertical: tokens.spacing.xs,
    justifyContent: "center",
    alignItems: "center",
    borderRadius: tokens.radius.md,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    backgroundColor: palette.color.background.canvas,
  },
  filterButtonActive: {
    borderColor: palette.color.brand.accent,
    backgroundColor: palette.color.background.surface,
  },
  filterButtonText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    fontSize: tokens.typography.fontSize.body,
    color: palette.color.text.secondary,
  },
  filterButtonTextActive: {
    color: palette.color.brand.accent,
  },
  listContent: {
    padding: tokens.spacing.lg,
    gap: tokens.spacing.md,
  },
  emptyListContent: {
    flexGrow: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: tokens.spacing.lg,
  },
  jobCard: {
    backgroundColor: palette.color.background.surface,
    borderRadius: tokens.radius.md,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    padding: tokens.spacing.lg,
    gap: tokens.spacing.sm,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: tokens.spacing.md,
  },
  titleArea: {
    flex: 1,
  },
  jobTitle: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryBold,
    color: palette.color.text.primary,
  },
  companyName: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
    marginTop: tokens.spacing.xs,
  },
  scoreBadge: {
    borderWidth: tokens.borderWidth.hairline,
    borderRadius: tokens.radius.full,
    paddingHorizontal: tokens.spacing.sm,
    paddingVertical: tokens.spacing.xs,
    alignItems: "center",
    justifyContent: "center",
  },
  scoreText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryBold,
  },
  metaRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    gap: tokens.spacing.xs,
    marginTop: tokens.spacing.xs,
  },
  metaTag: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
    backgroundColor: palette.color.background.canvas,
    paddingHorizontal: tokens.spacing.xs,
    paddingVertical: tokens.spacing.xs,
    borderRadius: tokens.radius.sm,
  },
  metaText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
  },
  eligibilityTag: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    paddingHorizontal: tokens.spacing.xs,
    paddingVertical: tokens.spacing.xs,
    borderRadius: tokens.radius.sm,
  },
  eligibilityEligible: {
    backgroundColor: palette.color.background.canvas,
    color: palette.color.feedback.success.foreground,
  },
  eligibilityReview: {
    backgroundColor: palette.color.background.canvas,
    color: palette.color.feedback.warning.foreground,
  },
  eligibilityIneligible: {
    backgroundColor: palette.color.background.canvas,
    color: palette.color.feedback.error.foreground,
  },
  cardActions: {
    flexDirection: "row",
    justifyContent: "flex-end",
    gap: tokens.spacing.sm,
    marginTop: tokens.spacing.sm,
    borderTopWidth: tokens.borderWidth.hairline,
    borderTopColor: palette.color.border.default,
    paddingTop: tokens.spacing.sm,
  },
  actionBtn: {
    paddingVertical: tokens.spacing.xs,
    paddingHorizontal: tokens.spacing.sm,
    borderRadius: tokens.radius.sm,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
  },
  actionBtnSaved: {
    backgroundColor: palette.color.action.primary.background,
    borderColor: palette.color.action.primary.background,
  },
  actionBtnText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.secondary,
  },
  actionBtnTextSaved: {
    color: palette.color.text.inverse,
  },
  centerContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: tokens.spacing.xl,
    gap: tokens.spacing.md,
  },
  loadingText: {
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
    fontSize: tokens.typography.fontSize.body,
  },
  emptyText: {
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
    fontSize: tokens.typography.fontSize.body,
    textAlign: "center",
  },
  inlineErrorContainer: {
    paddingHorizontal: tokens.spacing.lg,
    paddingVertical: tokens.spacing.xs,
    backgroundColor: palette.color.background.surface,
  },
  errorText: {
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.feedback.error.foreground,
    fontSize: tokens.typography.fontSize.body,
    textAlign: "center",
  },
  retryButton: {
    paddingVertical: tokens.spacing.sm,
    paddingHorizontal: tokens.spacing.lg,
    backgroundColor: palette.color.action.primary.background,
    borderRadius: tokens.radius.md,
  },
  retryButtonText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.inverse,
    fontSize: tokens.typography.fontSize.body,
  },
  footerLoader: {
    paddingVertical: tokens.spacing.md,
    alignItems: "center",
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: palette.color.background.canvas,
    justifyContent: "flex-end",
  },
  modalContent: {
    backgroundColor: palette.color.background.surface,
    borderTopLeftRadius: tokens.radius.lg,
    borderTopRightRadius: tokens.radius.lg,
    padding: tokens.spacing.xl,
    gap: tokens.spacing.md,
  },
  modalTitle: {
    fontSize: tokens.typography.fontSize.heading,
    fontFamily: tokens.typography.fontFamily.primaryBold,
    color: palette.color.text.primary,
  },
  modalInput: {
    backgroundColor: palette.color.background.canvas,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    borderRadius: tokens.radius.md,
    paddingHorizontal: tokens.spacing.md,
    paddingVertical: tokens.spacing.xs,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.primary,
    fontSize: tokens.typography.fontSize.body,
  },
  filterSectionTitle: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.primary,
    fontSize: tokens.typography.fontSize.body,
  },
  pillRow: {
    flexDirection: "row",
    gap: tokens.spacing.sm,
  },
  pillButton: {
    paddingVertical: tokens.spacing.xs,
    paddingHorizontal: tokens.spacing.md,
    borderRadius: tokens.radius.full,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    backgroundColor: palette.color.background.canvas,
  },
  pillButtonActive: {
    backgroundColor: palette.color.action.primary.background,
    borderColor: palette.color.action.primary.background,
  },
  pillButtonText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    fontSize: tokens.typography.fontSize.body,
    color: palette.color.text.secondary,
  },
  pillButtonTextActive: {
    color: palette.color.text.inverse,
  },
  checkboxRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: tokens.spacing.sm,
    paddingVertical: tokens.spacing.xs,
  },
  checkboxLabel: {
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.primary,
    fontSize: tokens.typography.fontSize.body,
  },
  modalActions: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: tokens.spacing.md,
    marginTop: tokens.spacing.sm,
  },
  modalSecondaryBtn: {
    flex: 1,
    paddingVertical: tokens.spacing.sm,
    justifyContent: "center",
    alignItems: "center",
    borderRadius: tokens.radius.md,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
  },
  modalSecondaryBtnText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.secondary,
    fontSize: tokens.typography.fontSize.body,
  },
  modalPrimaryBtn: {
    flex: 1,
    paddingVertical: tokens.spacing.sm,
    justifyContent: "center",
    alignItems: "center",
    borderRadius: tokens.radius.md,
    backgroundColor: palette.color.action.primary.background,
  },
  modalPrimaryBtnText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.inverse,
    fontSize: tokens.typography.fontSize.body,
  },
});
