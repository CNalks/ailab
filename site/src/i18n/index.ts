import { defaultLocale, type Locale } from "./config";
import { zhCN, type MessageKey } from "./zh-cn";
import { en } from "./en";
import { fr } from "./fr";

const dictionaries: Record<Locale, Partial<Record<MessageKey, string>>> = {
  "zh-cn": zhCN,
  en,
  fr
};

export type Params = Record<string, string | number>;

function interpolate(template: string, params?: Params) {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, name: string) => (name in params ? String(params[name]) : `{${name}}`));
}

/** Translate a key, falling back to Simplified Chinese when a locale lacks it. */
export function t(locale: Locale, key: MessageKey, params?: Params): string {
  const message = dictionaries[locale][key] ?? zhCN[key];
  return interpolate(message, params);
}

/** Bind t() to one locale for use inside a component. */
export function useT(locale: Locale) {
  return (key: MessageKey, params?: Params) => t(locale, key, params);
}

/** Look up a dynamic key (e.g. "model.croston_sba"), returning a fallback if unknown. */
export function tMaybe(locale: Locale, key: string, fallback: string): string {
  if (key in zhCN) return t(locale, key as MessageKey);
  return fallback;
}

export { defaultLocale };
export type { Locale, MessageKey };
