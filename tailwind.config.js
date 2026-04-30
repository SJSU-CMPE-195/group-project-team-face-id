export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', "system-ui", "sans-serif"],
      },
      colors: {
        dna: {
          bg: "var(--dna-bg)",
          sidebar: "var(--dna-sidebar)",
          surface: "var(--dna-surface)",
          surfaceHover: "var(--dna-surface-hover)",
        },
      },
      boxShadow: {
        panel: "0 0 0 1px rgba(139, 92, 246, 0.12), 0 24px 48px -12px rgba(0, 0, 0, 0.5)",
        "panel-inset": "inset 0 1px 0 0 rgba(255, 255, 255, 0.05)",
        tile: "0 4px 24px -8px rgba(0, 0, 0, 0.45)",
      },
    },
  },
  plugins: [],
};
