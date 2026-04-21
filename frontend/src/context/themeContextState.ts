import { createContext } from "react";
import type { Theme } from "../theme/storage";

export type ThemeContextValue = {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
};

export const ThemeStateContext = createContext<ThemeContextValue | null>(null);
