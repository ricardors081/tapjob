import { useEffect, useState } from "react";
import { useRouter } from "expo-router";
import { useTranslation } from "react-i18next";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";

import { endpoints, type JobAlertPreferenceResponse } from "@tapjob/contracts";
import { themes, tokens } from "@tapjob/design-tokens";

import { apiClient } from "../../auth/apiClient";

const palette = themes.light;

export default function JobAlertSettingsScreen() {
  const router = useRouter();
  const { t } = useTranslation();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Form state
  const [enabled, setEnabled] = useState(false);
  const [minScore, setMinScore] = useState(50);
  const [minEligibility, setMinEligibility] = useState<string>("eligible");

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const response = await endpoints.getJobAlertPreferences({
          client: apiClient,
        });
        if (!active) return;
        if (response.data && !response.error) {
          const pref = response.data as JobAlertPreferenceResponse;
          setEnabled(pref.enabled);
          setMinScore(pref.minScore);
          setMinEligibility(pref.minEligibility);
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

  const handleSave = async () => {
    try {
      setSaving(true);
      setError(null);
      setSuccessMessage(null);

      const response = await endpoints.updateJobAlertPreferences({
        client: apiClient,
        body: {
          enabled,
          minScore,
          minEligibility,
          deliveryChannel: "whatsapp",
        },
      });

      if (response.data && !response.error) {
        setSuccessMessage(t("jobs.alertsSavedSuccess"));
      } else {
        setError(t("jobs.errorLoading"));
      }
    } catch {
      setError(t("jobs.errorLoading"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* Header */}
      <View style={styles.header}>
        <Pressable
          accessibilityRole="button"
          onPress={() => router.back()}
          style={styles.backButton}
        >
          <Text style={styles.backButtonText}>{t("jobs.detail.back")}</Text>
        </Pressable>
        <Text style={styles.title}>{t("jobs.alertsTitle")}</Text>
        <View style={styles.placeholder} />
      </View>

      {loading ? (
        <View style={styles.centerContainer}>
          <ActivityIndicator size="large" color={palette.color.action.primary.background} />
          <Text style={styles.loadingText}>{t("jobs.loading")}</Text>
        </View>
      ) : (
        <View style={styles.formCard}>
          <Text style={styles.subtitle}>{t("jobs.alertsSubtitle")}</Text>

          {/* Toggle Switch */}
          <View style={styles.row}>
            <View style={styles.rowLabelContainer}>
              <Text style={styles.rowTitle}>{t("jobs.alertsToggle")}</Text>
            </View>
            <Switch
              value={enabled}
              onValueChange={setEnabled}
              trackColor={{
                false: palette.color.border.default,
                true: palette.color.action.primary.background,
              }}
            />
          </View>

          {/* Min Score Options */}
          <View style={styles.fieldSection}>
            <Text style={styles.fieldLabel}>
              {t("jobs.alertsMinScoreLabel", { score: minScore })}
            </Text>
            <View style={styles.pillRow}>
              {[50, 60, 70, 80].map((score) => (
                <Pressable
                  key={score}
                  accessibilityRole="button"
                  onPress={() => setMinScore(score)}
                  style={[styles.pillBtn, minScore === score && styles.pillBtnActive]}
                >
                  <Text
                    style={[styles.pillBtnText, minScore === score && styles.pillBtnTextActive]}
                  >
                    {t("jobs.percentValue", { value: score })}
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>

          {/* Min Eligibility Options */}
          <View style={styles.fieldSection}>
            <Text style={styles.fieldLabel}>
              {t("jobs.filters.eligibility", { defaultValue: "Nível de aderência" })}
            </Text>
            <View style={styles.pillRow}>
              {[
                { key: "eligible", label: t("jobs.eligibility.eligible") },
                { key: "review_required", label: t("jobs.eligibility.review_required") },
              ].map((item) => (
                <Pressable
                  key={item.key}
                  accessibilityRole="button"
                  onPress={() => setMinEligibility(item.key)}
                  style={[styles.pillBtn, minEligibility === item.key && styles.pillBtnActive]}
                >
                  <Text
                    style={[
                      styles.pillBtnText,
                      minEligibility === item.key && styles.pillBtnTextActive,
                    ]}
                  >
                    {item.label}
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>

          {/* Delivery Channel Options */}
          <View style={styles.fieldSection}>
            <Text style={styles.fieldLabel}>{t("jobs.alertsChannel")}</Text>
            <View style={styles.pillRow}>
              <View style={[styles.pillBtn, styles.pillBtnActive]}>
                <Text style={[styles.pillBtnText, styles.pillBtnTextActive]}>
                  {t("jobs.channelWhatsApp")}
                </Text>
              </View>
            </View>
          </View>

          {/* Feedback messages */}
          {error && <Text style={styles.errorText}>{error}</Text>}
          {successMessage && <Text style={styles.successText}>{successMessage}</Text>}

          {/* Save Button */}
          <Pressable
            accessibilityRole="button"
            onPress={() => void handleSave()}
            disabled={saving}
            style={[styles.saveBtn, saving && styles.saveBtnDisabled]}
          >
            {saving ? (
              <ActivityIndicator color={palette.color.text.inverse} />
            ) : (
              <Text style={styles.saveBtnText}>{t("jobs.alertsSave")}</Text>
            )}
          </Pressable>
        </View>
      )}
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
  header: {
    paddingTop: tokens.spacing.md,
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
    backgroundColor: palette.color.background.surface,
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
  formCard: {
    backgroundColor: palette.color.background.surface,
    borderRadius: tokens.radius.md,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    padding: tokens.spacing.xl,
    gap: tokens.spacing.lg,
  },
  subtitle: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
    lineHeight: 20,
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: tokens.spacing.xs,
  },
  rowLabelContainer: {
    flex: 1,
  },
  rowTitle: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.primary,
  },
  fieldSection: {
    gap: tokens.spacing.sm,
  },
  fieldLabel: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.primary,
  },
  pillRow: {
    flexDirection: "row",
    gap: tokens.spacing.sm,
  },
  pillBtn: {
    paddingVertical: tokens.spacing.xs,
    paddingHorizontal: tokens.spacing.md,
    borderRadius: tokens.radius.full,
    borderWidth: tokens.borderWidth.hairline,
    borderColor: palette.color.border.default,
    backgroundColor: palette.color.background.canvas,
  },
  pillBtnActive: {
    backgroundColor: palette.color.action.primary.background,
    borderColor: palette.color.action.primary.background,
  },
  pillBtnText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.secondary,
  },
  pillBtnTextActive: {
    color: palette.color.text.inverse,
  },
  saveBtn: {
    height: 48,
    borderRadius: tokens.radius.md,
    backgroundColor: palette.color.action.primary.background,
    justifyContent: "center",
    alignItems: "center",
    marginTop: tokens.spacing.sm,
  },
  saveBtnDisabled: {
    opacity: 0.6,
  },
  saveBtnText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.text.inverse,
  },
  centerContainer: {
    padding: tokens.spacing.xl,
    justifyContent: "center",
    alignItems: "center",
    gap: tokens.spacing.md,
  },
  loadingText: {
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.text.secondary,
    fontSize: tokens.typography.fontSize.body,
  },
  errorText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primaryRegular,
    color: palette.color.feedback.error.foreground,
  },
  successText: {
    fontSize: tokens.typography.fontSize.body,
    fontFamily: tokens.typography.fontFamily.primarySemibold,
    color: palette.color.feedback.success.foreground,
  },
});
