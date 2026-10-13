import { useEffect, useState } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useTranslation } from "react-i18next";
import {
  ActivityIndicator,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { endpoints, type JobDetailResponse } from "@tapjob/contracts";
import { themes, tokens } from "@tapjob/design-tokens";

import { apiClient } from "../../auth/apiClient";

const palette = themes.light;

function getScoreColor(score: number | null | undefined): string {
  if (score == null) return palette.color.text.secondary;
  if (score >= 70) return palette.color.feedback.success.foreground;
  if (score >= 50) return palette.color.feedback.warning.foreground;
  return palette.color.feedback.error.foreground;
}

export function isAllowedExternalJobUrl(url: string | null | undefined): boolean {
  if (!url || typeof url !== "string") return false;
  const trimmed = url.trim();
  if (!trimmed) return false;
  try {
    const parsed = new URL(trimmed);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

export default function JobDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { t } = useTranslation();

  const [job, setJob] = useState<JobDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSaved, setIsSaved] = useState(false);
  const [isWatchingCompany, setIsWatchingCompany] = useState(false);

  useEffect(() => {
    let active = true;
    if (!id) return;

    void (async () => {
      try {
        const response = await endpoints.getJobById({
          client: apiClient,
          path: { job_id: id },
        });

        if (!active) return;
        if (response.data) {
          const data = response.data as JobDetailResponse;
          setJob(data);
          setIsSaved(Boolean(data.isSaved));
          setIsWatchingCompany(Boolean(data.isCompanyWatched));
        } else {
          setError(t("jobs.errorLoading"));
        }
      } catch {
        if (!active) return;
        setError(t("jobs.errorLoading"));
      } finally {
        if (active) setLoading(false);
      }
    })();

    return () => {
      active = false;
    };
  }, [id, t]);

  const handleToggleSave = async () => {
    if (!id) return;
    const previous = isSaved;
    setIsSaved(!previous);

    try {
      const response = previous
        ? await endpoints.unsaveJob({
            client: apiClient,
            path: { job_id: id },
          })
        : await endpoints.saveJob({
            client: apiClient,
            path: { job_id: id },
          });

      if (response.error) {
        setIsSaved(previous);
        setError(t("jobs.errorLoading"));
      }
    } catch {
      setIsSaved(previous);
      setError(t("jobs.errorLoading"));
    }
  };

  const handleToggleWatchCompany = async () => {
    if (!job?.company?.id) return;
    const companyId = job.company.id;
    const previous = isWatchingCompany;
    setIsWatchingCompany(!previous);

    try {
      const response = previous
        ? await endpoints.unwatchCompany({
            client: apiClient,
            path: { company_id: companyId },
          })
        : await endpoints.watchCompany({
            client: apiClient,
            path: { company_id: companyId },
          });

      if (response.error) {
        setIsWatchingCompany(previous);
        setError(t("jobs.errorLoading"));
      }
    } catch {
      setIsWatchingCompany(previous);
      setError(t("jobs.errorLoading"));
    }
  };

  const handleOpenExternal = () => {
    const targetUrl = job?.origins?.[0]?.canonicalUrl || job?.origins?.[0]?.sourceUrl;
    if (isAllowedExternalJobUrl(targetUrl)) {
      void Linking.openURL(targetUrl!);
    }
  };

  if (loading) {
    return (
      <View style={styles.centerContainer}>
        <ActivityIndicator size="large" color={palette.color.action.primary.background} />
        <Text style={styles.loadingText}>{t("jobs.loading")}</Text>
      </View>
    );
  }

  if (error || !job) {
    return (
      <View style={styles.centerContainer}>
        <Text style={styles.errorText}>{error ?? t("jobs.errorLoading")}</Text>
        <Pressable
          accessibilityRole="button"
          onPress={() => {
            setLoading(true);
            setError(null);
            void (async () => {
              try {
                if (!id) return;
                const response = await endpoints.getJobById({
                  client: apiClient,
                  path: { job_id: id },
                });
                if (response.data) {
                  const data = response.data as JobDetailResponse;
                  setJob(data);
                  setIsSaved(Boolean(data.isSaved));
                  setIsWatchingCompany(Boolean(data.isCompanyWatched));
                } else {
                  setError(t("jobs.errorLoading"));
                }
              } catch {
                setError(t("jobs.errorLoading"));
              } finally {
                setLoading(false);
              }
            })();
          }}
          style={styles.primaryBtn}
        >
          <Text style={styles.primaryBtnText}>{t("jobs.retry")}</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          onPress={() => router.back()}
          style={styles.secondaryBtn}
        >
          <Text style={styles.secondaryBtnText}>{t("jobs.detail.back")}</Text>
        </Pressable>
      </View>
    );
  }

  const match = job.match;
  const externalUrl = job.origins?.[0]?.canonicalUrl || job.origins?.[0]?.sourceUrl;

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.topBar}>
        <Pressable
          accessibilityRole="button"
          onPress={() => router.back()}
          style={styles.backButton}
        >
          <Text style={styles.backButtonText}>{t("jobs.detail.back")}</Text>
        </Pressable>
      </View>

      <View style={styles.titleSection}>
        <Text style={styles.title}>{job.title}</Text>
        <View style={styles.companyRow}>
          <Text style={styles.companyName}>
            {job.company
              ? job.company.name
              : t("jobs.companyFallback", { defaultValue: "Empresa" })}
          </Text>
          {job.company && (
            <Pressable
              accessibilityRole="button"
              onPress={() => void handleToggleWatchCompany()}
              style={[styles.watchBtn, isWatchingCompany && styles.watchBtnActive]}
            >
              <Text style={[styles.watchBtnText, isWatchingCompany && styles.watchBtnTextActive]}>
                {isWatchingCompany ? t("jobs.unwatchCompany") : t("jobs.watchCompany")}
              </Text>
            </Pressable>
          )}
        </View>

        <View style={styles.metaRow}>
          {job.workplaceType && (
            <Text style={styles.metaTag}>
              {t(`jobs.workplace.${job.workplaceType}`, { defaultValue: job.workplaceType })}
            </Text>
          )}
          {job.locationRaw && (
            <Text style={styles.metaText}>
              {t("jobs.locationPrefix")} {job.locationRaw}
            </Text>
          )}
        </View>
      </View>

      <View style={styles.actionRow}>
        <Pressable
          accessibilityRole="button"
          onPress={() => void handleToggleSave()}
          style={[styles.actionBtn, isSaved && styles.actionBtnSaved]}
        >
          <Text style={[styles.actionBtnText, isSaved && styles.actionBtnTextSaved]}>
            {isSaved ? t("jobs.saved") : t("jobs.save")}
          </Text>
        </Pressable>
        {externalUrl && (
          <Pressable
            accessibilityRole="button"
            onPress={handleOpenExternal}
            style={styles.applyBtn}
          >
            <Text style={styles.applyBtnText}>{t("jobs.detail.applyExternal")}</Text>
          </Pressable>
        )}
      </View>

      {match && (
        <View style={styles.matchCard}>
          <View style={styles.matchHeader}>
            <View>
              <Text style={styles.matchTitle}>{t("jobs.detail.compatibility")}</Text>
              <Text
                style={[
                  styles.eligibilityLabel,
                  match.eligibility === "eligible"
                    ? styles.eligibilityEligible
                    : match.eligibility === "review_required"
                      ? styles.eligibilityReview
                      : styles.eligibilityIneligible,
                ]}
              >
                {t(`jobs.eligibility.${match.eligibility}`, { defaultValue: match.eligibility })}
              </Text>
            </View>
            <View
              style={[styles.largeScoreBadge, { borderColor: getScoreColor(match.overallScore) }]}
            >
              <Text style={[styles.largeScoreText, { color: getScoreColor(match.overallScore) }]}>
                {t("jobs.percentValue", { value: match.overallScore })}
              </Text>
            </View>
          </View>

          {match.blockers && match.blockers.length > 0 && (
            <View style={styles.blockerCard}>
              <Text style={styles.blockerTitle}>{t("jobs.detail.hardBlockers")}</Text>
              {match.blockers.map((blocker: { message?: string; code?: string }, idx: number) => (
                <Text key={idx} style={styles.blockerItem}>
                  {blocker.message || blocker.code || t("jobs.detail.hardBlockers")}
                </Text>
              ))}
            </View>
          )}

          {match.strengths && match.strengths.length > 0 && (
            <View style={styles.matchSection}>
              <Text style={styles.sectionSubtitle}>{t("jobs.detail.strengths")}</Text>
              {match.strengths.map((str: { name?: string; description?: string }, idx: number) => (
                <Text key={idx} style={styles.bulletItem}>
                  {str.name || str.description || t("jobs.detail.strengths")}
                </Text>
              ))}
            </View>
          )}

          {match.missingRequirements && match.missingRequirements.length > 0 && (
            <View style={styles.matchSection}>
              <Text style={styles.sectionSubtitle}>{t("jobs.detail.missingRequirements")}</Text>
              {match.missingRequirements.map(
                (req: { name?: string; description?: string }, idx: number) => (
                  <Text key={idx} style={styles.bulletItem}>
                    {req.name || req.description || t("jobs.detail.missingRequirements")}
                  </Text>
                ),
              )}
            </View>
          )}
        </View>
      )}

      <View style={styles.descriptionCard}>
        <Text style={styles.sectionTitle}>{t("jobs.detail.description")}</Text>
        <Text style={styles.descriptionText}>{job.descriptionRaw}</Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: palette.color.background.canvas,
  },
  content: {
    padding: tokens.spacing.lg,
    gap: tokens.spacing.lg,
  },
  topBar: {
    flexDirection: "row",
    alignItems: "center",
    paddingTop: tokens.spacing.md,
  },
  backButton: {
    paddingVertical: tokens.spacing.xs,
    paddingHorizontal: tokens.spacing.sm,
    borderRadius: tokens.radius.sm,
    backgroundColor: palette.color.background.surface,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
  },
  backButtonText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.primary,
  },
  titleSection: {
    backgroundColor: palette.color.background.surface,
    borderRadius: tokens.radius.md,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    padding: tokens.spacing.lg,
    gap: tokens.spacing.sm,
  },
  title: {
    fontSize: tokens.typography.fontSize.heading,
    fontFamily: tokens.typography.fontFamily.primaryBold,
    color: palette.color.text.primary,
  },
  companyRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  companyName: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.secondary,
  },
  watchBtn: {
    paddingVertical: tokens.spacing.xs,
    paddingHorizontal: tokens.spacing.sm,
    borderRadius: tokens.radius.sm,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    backgroundColor: palette.color.background.canvas,
  },
  watchBtnActive: {
    backgroundColor: palette.color.background.surface,
    borderColor: palette.color.feedback.success.foreground,
  },
  watchBtnText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.secondary,
  },
  watchBtnTextActive: {
    color: palette.color.feedback.success.foreground,
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
  actionRow: {
    flexDirection: "row",
    gap: tokens.spacing.md,
  },
  actionBtn: {
    flex: 1,
    paddingVertical: tokens.spacing.sm,
    justifyContent: "center",
    alignItems: "center",
    borderRadius: tokens.radius.md,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    backgroundColor: palette.color.background.surface,
  },
  actionBtnSaved: {
    backgroundColor: palette.color.action.primary.background,
    borderColor: palette.color.action.primary.background,
  },
  actionBtnText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    fontSize: tokens.typography.fontSize.body,
    color: palette.color.text.primary,
  },
  actionBtnTextSaved: {
    color: palette.color.text.inverse,
  },
  applyBtn: {
    flex: 2,
    paddingVertical: tokens.spacing.sm,
    justifyContent: "center",
    alignItems: "center",
    borderRadius: tokens.radius.md,
    backgroundColor: palette.color.brand.accent,
  },
  applyBtnText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    fontSize: tokens.typography.fontSize.body,
    color: palette.color.text.inverse,
  },
  matchCard: {
    backgroundColor: palette.color.background.surface,
    borderRadius: tokens.radius.md,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    padding: tokens.spacing.lg,
    gap: tokens.spacing.md,
  },
  matchHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  matchTitle: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryBold,
    color: palette.color.text.primary,
  },
  eligibilityLabel: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    marginTop: tokens.spacing.xs,
    paddingHorizontal: tokens.spacing.xs,
    paddingVertical: tokens.spacing.xs,
    borderRadius: tokens.radius.sm,
    alignSelf: "flex-start",
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
  largeScoreBadge: {
    borderWidth: tokens.borderWidth.hairline,
    borderRadius: tokens.radius.full,
    paddingHorizontal: tokens.spacing.md,
    paddingVertical: tokens.spacing.xs,
  },
  largeScoreText: {
    fontSize: tokens.typography.fontSize.title,
    fontFamily: tokens.typography.fontFamily.primaryBold,
  },
  blockerCard: {
    backgroundColor: palette.color.background.canvas,
    borderRadius: tokens.radius.sm,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.feedback.error.foreground,
    padding: tokens.spacing.md,
    gap: tokens.spacing.xs,
  },
  blockerTitle: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryBold,
    color: palette.color.feedback.error.foreground,
  },
  blockerItem: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.feedback.error.foreground,
  },
  matchSection: {
    gap: tokens.spacing.xs,
  },
  sectionSubtitle: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.primary,
  },
  bulletItem: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
  },
  descriptionCard: {
    backgroundColor: palette.color.background.surface,
    borderRadius: tokens.radius.md,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    padding: tokens.spacing.lg,
    gap: tokens.spacing.md,
  },
  sectionTitle: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryBold,
    color: palette.color.text.primary,
  },
  descriptionText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
  },
  centerContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: tokens.spacing.xl,
    gap: tokens.spacing.md,
    backgroundColor: palette.color.background.canvas,
  },
  loadingText: {
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
    fontSize: tokens.typography.fontSize.body,
  },
  errorText: {
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.feedback.error.foreground,
    fontSize: tokens.typography.fontSize.body,
    textAlign: "center",
  },
  primaryBtn: {
    paddingVertical: tokens.spacing.sm,
    paddingHorizontal: tokens.spacing.lg,
    backgroundColor: palette.color.action.primary.background,
    borderRadius: tokens.radius.md,
  },
  primaryBtnText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.inverse,
    fontSize: tokens.typography.fontSize.body,
  },
  secondaryBtn: {
    paddingVertical: tokens.spacing.sm,
    paddingHorizontal: tokens.spacing.lg,
    borderRadius: tokens.radius.md,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
  },
  secondaryBtnText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.secondary,
    fontSize: tokens.typography.fontSize.body,
  },
});
