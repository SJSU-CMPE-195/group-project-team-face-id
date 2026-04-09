import { createContext } from "react";

export const themeStorageKey = "face-ui-theme";

export const ThemeContext = createContext({
  theme: "dark",
  isDark: true,
  toggleTheme: () => {},
});
