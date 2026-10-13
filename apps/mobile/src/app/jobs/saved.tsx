import { useCallback, useEffect, useState } from "react";
import { useRouter } from "expo-router";
import { useTranslation } from "react-i18next";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { endpoints, type SavedJobResponse } from "@tapjob/contracts";
import { themes, tokens } from "@tapjob/design-tokens";

import { apiClient } from "../../auth/apiClient";

const palette = themes.light;

export default function SavedJobsScreen() {
  const router = useRouter();
  const { t } = useTranslation();

  const [items, setItems] = useState<SavedJobResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchSaved = useCallback(async () => {
    try {
      const response = await endpoints.listSavedJobs({
        client: apiClient,
        query: { limit: 50 },
      });

      if (response.data && !response.error) {
        setItems(response.data.items as SavedJobResponse[]);
        setError(null);
      } else {
        setError(t("jobs.errorLoading"));
      }
    } catch {
      setError(t("jobs.errorLoading"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [t]);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const response = await endpoints.listSavedJobs({
          client: apiClient,
          query: { limit: 50 },
        });
        if (!active) return;
        if (response.data && !response.error) {
          setItems(response.data.items as SavedJobResponse[]);
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
  }, [t]);

  const onRefresh = () => {
    setRefreshing(true);
    void fetchSaved();
  };

  const handleUnsave = async (savedJob: SavedJobResponse) => {
    // Optimistic removal
    setItems((prev) => prev.filter((it) => it.id !== savedJob.id));

    try {
      const response = await endpoints.unsaveJob({
        client: apiClient,
        path: { job_id: savedJob.jobId },
      });
      if (response.error) {
        // Rollback on error
        setItems((prev) => [savedJob, ...prev]);
        setError(t("jobs.errorLoading"));
      }
    } catch {
      // Rollback on throw
      setItems((prev) => [savedJob, ...prev]);
      setError(t("jobs.errorLoading"));
    }
  };

  return (
    <View style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <Pressable
          accessibilityRole="button"
          onPress={() => router.back()}
          style={styles.backButton}
        >
          <Text style={styles.backButtonText}>{t("jobs.detail.back")}</Text>
        </Pressable>
        <Text style={styles.title}>{t("jobs.savedTitle")}</Text>
        <View style={styles.placeholder} />
      </View>

      {/* Main List */}
      {loading ? (
        <View style={styles.centerContainer}>
          <ActivityIndicator size="large" color={palette.color.action.primary.background} />
          <Text style={styles.loadingText}>{t("jobs.loading")}</Text>
        </View>
      ) : error ? (
        <View style={styles.centerContainer}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable
            accessibilityRole="button"
            onPress={() => void fetchSaved()}
            style={styles.primaryBtn}
          >
            <Text style={styles.primaryBtnText}>{t("jobs.retry")}</Text>
          </Pressable>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => item.id}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          contentContainerStyle={items.length === 0 ? styles.emptyListContent : styles.listContent}
          ListEmptyComponent={
            <View style={styles.centerContainer}>
              <Text style={styles.emptyText}>{t("jobs.emptySaved")}</Text>
            </View>
          }
          renderItem={({ item }) => {
            const job = item.job;
            return (
              <Pressable
                accessibilityRole="button"
                onPress={() => router.push(`/jobs/${item.jobId}`)}
                style={styles.card}
              >
                <View style={styles.cardInfo}>
                  <Text style={styles.jobTitle} numberOfLines={2}>
                    {job.title}
                  </Text>
                  <Text style={styles.companyName}>
                    {job.company ? job.company.name : t("jobs.companyDefault")}
                  </Text>
                  {job.locationRaw && (
                    <Text style={styles.locationText} numberOfLines={1}>
                      {t("jobs.locationPrefix")} {job.locationRaw}
                    </Text>
                  )}
                </View>
                <Pressable
                  accessibilityRole="button"
                  onPress={(e) => {
                    e.stopPropagation();
                    void handleUnsave(item);
                  }}
                  style={styles.unsaveBtn}
                >
                  <Text style={styles.unsaveBtnText}>{t("jobs.removeSaved")}</Text>
                </Pressable>
              </Pressable>
            );
          }}
        />
      )}
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
    alignItems: "center",
    justifyContent: "space-between",
  },
  backButton: {
    paddingVertical: tokens.spacing.xs,
    paddingHorizontal: tokens.spacing.sm,
    borderRadius: tokens.radius.sm,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    backgroundColor: palette.color.background.canvas,
  },
  backButtonText: {
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    fontSize: tokens.typography.fontSize.body,
    color: palette.color.text.primary,
  },
  title: {
    fontSize: tokens.typography.fontSize.heading,
    fontFamily: tokens.typography.fontFamily.primaryBold,
    color: palette.color.text.primary,
  },
  placeholder: {
    width: 60,
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
  card: {
    backgroundColor: palette.color.background.surface,
    borderRadius: tokens.radius.md,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    padding: tokens.spacing.lg,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: tokens.spacing.md,
  },
  cardInfo: {
    flex: 1,
    gap: tokens.spacing.xs,
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
  },
  locationText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
  },
  unsaveBtn: {
    paddingVertical: tokens.spacing.xs,
    paddingHorizontal: tokens.spacing.sm,
    borderRadius: tokens.radius.sm,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    backgroundColor: palette.color.background.canvas,
  },
  unsaveBtnText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.secondary,
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
});
