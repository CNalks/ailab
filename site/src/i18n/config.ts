export const locales = ["zh-cn", "en", "fr"] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "zh-cn";

/** Deploy base, e.g. "/ai4health/lab" (no trailing slash). */
export const BASE: string = import.meta.env.BASE_URL.replace(/\/$/, "");

/** BCP 47 tags for <html lang>, hreflang and Intl. */
export const htmlLang: Record<Locale, string> = { "zh-cn": "zh-CN", en: "en", fr: "fr" };
export const ogLocale: Record<Locale, string> = { "zh-cn": "zh_CN", en: "en_US", fr: "fr_FR" };
export const localeName: Record<Locale, string> = { "zh-cn": "简体中文", en: "English", fr: "Français" };
export const localeShort: Record<Locale, string> = { "zh-cn": "中文", en: "EN", fr: "FR" };
/** gate / policy messages in the exported JSON use these keys */
export const msgKey: Record<Locale, "zh" | "en" | "fr"> = { "zh-cn": "zh", en: "en", fr: "fr" };

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (locales as readonly string[]).includes(value);
}

/** Strip the deploy base and a locale prefix from a pathname, returning the locale-neutral path ("/" for the page). */
export function stripLocale(pathname: string): string {
  let p = pathname.startsWith(BASE) ? pathname.slice(BASE.length) || "/" : pathname;
  for (const l of locales) {
    if (l === defaultLocale) continue;
    if (p === `/${l}` || p === `/${l}/`) return "/";
    if (p.startsWith(`/${l}/`)) return p.slice(l.length + 1);
  }
  return p;
}

/** Build a localized, base-prefixed path from a locale-neutral path such as "/". */
export function localizePath(path: string, locale: Locale): string {
  const clean = path.startsWith("/") ? path : `/${path}`;
  return `${BASE}${locale === defaultLocale ? "" : `/${locale}`}${clean}`;
}

/** Prefix a site-root-relative asset path (e.g. "/data/manifest.json") with the deploy base. */
export function asset(path: string): string {
  return `${BASE}${path.startsWith("/") ? path : `/${path}`}`;
}
