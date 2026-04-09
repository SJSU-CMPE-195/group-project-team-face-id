import { useContext } from "react";
import { ThemeContext } from "./themeContext.js";

export function useTheme() {
  return useContext(ThemeContext);
}
