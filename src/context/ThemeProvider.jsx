import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ThemeContext, themeStorageKey } from "./themeContext.js";

export default function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => {
    try {
      const s = localStorage.getItem(themeStorageKey);
      if (s === "light" || s === "dark") return s;
    } catch {
      /* ignore */
    }
    return "dark";
  });

  const isDark = theme === "dark";

  useEffect(() => {
    try {
      localStorage.setItem(themeStorageKey, theme);
    } catch {
      /* ignore */
    }
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  const value = useMemo(() => ({ theme, isDark, toggleTheme }), [theme, isDark, toggleTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
