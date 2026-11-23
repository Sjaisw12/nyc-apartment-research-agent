import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "ui-serif", "Georgia", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        // Stoop design tokens (navy real-estate intelligence). Change colours here, not in components.
        ink: { DEFAULT: "#14253D", soft: "#3E4C63", faint: "#5E6B80" },
        paper: { DEFAULT: "#F4F7FC", deep: "#E8EEF7" },
        line: { DEFAULT: "#DDE4EF", strong: "#C7D2E3" },
        navy: { DEFAULT: "#14253D", 900: "#0D1A2D", 800: "#1B2F4C", 700: "#24406A" },
        brand: { DEFAULT: "#2563EB", dark: "#1D4ED8", light: "#DBEAFE", 50: "#EFF6FF" },
        accent: { DEFAULT: "#60A5FA", soft: "#BFDBFE" },
        caution: { DEFAULT: "#B45309", light: "#FEF3C7" },
        danger: { DEFAULT: "#B91C1C", light: "#FEE2E2" },
      },
      maxWidth: { site: "1240px" },
      boxShadow: {
        card: "0 1px 2px rgba(20,37,61,.05), 0 4px 12px -2px rgba(20,37,61,.05)",
        lift: "0 2px 4px rgba(20,37,61,.04), 0 12px 32px -8px rgba(20,37,61,.14)",
        hero: "0 30px 60px -20px rgba(20,37,61,.25), 0 10px 20px -10px rgba(20,37,61,.12)",
      },
      keyframes: {
        "fade-up": { from: { opacity: "0", transform: "translateY(6px)" }, to: { opacity: "1", transform: "none" } },
      },
      animation: { "fade-up": "fade-up .35s ease-out both" },
    },
  },
  plugins: [],
};
export default config;
