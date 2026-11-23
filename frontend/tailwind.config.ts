import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "ui-serif", "Georgia", "serif"],
      },
      colors: {
        ink: { DEFAULT: "#14213d", soft: "#3d4a63", faint: "#6b7690" },
        paper: "#fbfaf7",
        brand: { DEFAULT: "#1f5f5b", dark: "#164744", light: "#e6f0ef" },
      },
      boxShadow: { card: "0 1px 2px rgba(20,33,61,.06), 0 4px 16px rgba(20,33,61,.05)" },
    },
  },
  plugins: [],
};
export default config;
