import { useContext } from "react";
import { ThemeStateContext } from "./context/themeContextState";

export function useTheme() {
  const ctx = useContext(ThemeStateContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
