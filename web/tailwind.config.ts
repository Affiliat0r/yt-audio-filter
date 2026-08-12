import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0a0c10",
          900: "#11141b",
          850: "#161a23",
          800: "#1c212c",
          700: "#2a3140",
          600: "#3b4455",
          400: "#7b8598",
          300: "#a4adbe",
          100: "#e7eaf0",
        },
        accent: {
          DEFAULT: "#4f9cf9",
          soft: "#1e3a5f",
        },
      },
    },
  },
  plugins: [],
};

export default config;
