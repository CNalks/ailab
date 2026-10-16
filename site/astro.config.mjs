import { defineConfig } from "astro/config";

// Served at https://www.cnalks.cn/ai4health/lab/ (zh default), /en/, /fr/ — static files only.
export default defineConfig({
  site: "https://www.cnalks.cn",
  base: "/ai4health/lab",
  trailingSlash: "always",
  output: "static",
  build: { assetsPrefix: undefined },
  i18n: {
    defaultLocale: "zh-cn",
    locales: ["zh-cn", "en", "fr"],
    routing: { prefixDefaultLocale: false }
  }
});
