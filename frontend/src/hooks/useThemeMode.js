import { useEffect, useState } from "react";

const STORAGE_KEY = "cbpa-theme-mode";

const getInitialMode = () => {
  if (typeof window === "undefined") {
    return "light";
  }

  const savedMode = window.localStorage.getItem(STORAGE_KEY);

  if (savedMode) {
    return savedMode;
  }

  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
};

// Keep dark mode state in one place so the full app can react to it.
const useThemeMode = () => {
  const [mode, setMode] = useState(getInitialMode);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, mode);
  }, [mode]);

  const toggleMode = () => {
    setMode((currentMode) => (currentMode === "light" ? "dark" : "light"));
  };

  return { mode, toggleMode };
};

export default useThemeMode;
