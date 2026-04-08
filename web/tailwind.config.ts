import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "sans-serif"],
      },
      colors: {
        brand: {
          bg: "#050505",
          panel: "#0A0A0A",
          border: "#1A1A1A",
          primary: "#2C7A94",
          accent: "#FE9330",
          success: "#38B2AC",
          text: "#FFFFFF",
          muted: "#8A8A8A",
        },
      },
    },
  },
  plugins: [],
};

export default config;
