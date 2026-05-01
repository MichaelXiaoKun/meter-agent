/**
 * Conversation list timestamps: “today” / “yesterday” via
 * ``Intl.RelativeTimeFormat``, older rows via a short calendar date in the
 * same locale (typically ``navigator.language``).
 */

export function getDefaultDateLocale(): string {
  if (typeof navigator !== "undefined" && navigator.language) {
    return navigator.language;
  }
  return "en-US";
}

/**
 * @param updatedAtSeconds - Unix seconds (conversation ``updated_at``).
 * @param locale - BCP 47 tag; defaults to ``getDefaultDateLocale()``.
 */
export function formatConversationRelativeDate(
  updatedAtSeconds: number,
  locale: string = getDefaultDateLocale(),
): string {
  const now = new Date();
  const d = new Date(updatedAtSeconds * 1000);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfConvDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffMs = startOfToday.getTime() - startOfConvDay.getTime();
  const diffDays = Math.round(diffMs / 86_400_000);

  if (diffDays < 0) {
    return shortCalendarDate(d, now, locale);
  }

  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (diffDays === 0) {
    return rtf.format(0, "day");
  }
  if (diffDays === 1) {
    return rtf.format(-1, "day");
  }

  return shortCalendarDate(d, now, locale);
}

function shortCalendarDate(d: Date, now: Date, locale: string): string {
  const y = d.getFullYear() !== now.getFullYear();
  return d.toLocaleDateString(locale, {
    month: "short",
    day: "numeric",
    ...(y ? { year: "numeric" as const } : {}),
  });
}
