import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{vue,ts}"],
  theme: {
    extend: {
      colors: {
        primary: "#12b76a",
        primaryDark: "#087443",
        primarySoft: "#dff8ea",
        accent: "#e59f32",
        accentSoft: "#fff3d8",
        danger: "#d94c48",
        dangerSoft: "#ffe7e4",
        line: "#e3e8df",
        muted: "#6f796f",
        ink: "#172019"
      },
      fontFamily: {
        sans: ['"HarmonyOS Sans SC"', '"PingFang SC"', '"Microsoft YaHei UI"', "sans-serif"],
        serif: ['"Noto Serif SC"', '"Source Han Serif SC"', "Georgia", "serif"],
        mono: ['"Cascadia Code"', '"JetBrains Mono"', "Consolas", "monospace"]
      },
      boxShadow: {
        soft: "0 14px 34px rgba(26, 48, 31, 0.08)",
        float: "0 28px 70px rgba(26, 48, 31, 0.12)"
      }
    }
  },
  plugins: []
} satisfies Config;
