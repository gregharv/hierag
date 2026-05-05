import { useEffect, useState } from "react";

export const themes = [
  "light",
  "dark",
  "cupcake",
  "bumblebee",
  "emerald",
  "corporate",
  "synthwave",
  "retro",
  "cyberpunk",
  "valentine",
  "halloween",
  "garden",
  "forest",
  "aqua",
  "lofi",
  "pastel",
  "fantasy",
  "wireframe",
  "black",
  "luxury",
  "dracula",
  "cmyk",
  "autumn",
  "business",
  "acid",
  "lemonade",
  "night",
  "coffee",
  "winter",
  "dim",
  "nord",
  "sunset",
] as const;

type ThemeName = (typeof themes)[number];

const DEFAULT_THEME: ThemeName = "light";
const THEME_STORAGE_KEY = "theme";

function isThemeName(value: string | null): value is ThemeName {
  return themes.includes(value as ThemeName);
}

function getSavedTheme(): ThemeName {
  if (typeof window === "undefined") {
    return DEFAULT_THEME;
  }

  const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
  return isThemeName(savedTheme) ? savedTheme : DEFAULT_THEME;
}

export function applySavedTheme() {
  if (typeof document === "undefined") {
    return;
  }

  document.documentElement.setAttribute("data-theme", getSavedTheme());
}

export function ThemeDropdown() {
  const [theme, setTheme] = useState<ThemeName>(() => getSavedTheme());

  useEffect(() => {
    const savedTheme = getSavedTheme();
    document.documentElement.setAttribute("data-theme", savedTheme);
    setTheme(savedTheme);
  }, []);

  function changeTheme(nextTheme: ThemeName) {
    document.documentElement.setAttribute("data-theme", nextTheme);
    window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    setTheme(nextTheme);
  }

  return (
    <div className="dropdown dropdown-end">
      <button tabIndex={0} type="button" className="btn btn-sm btn-ghost normal-case">
        Theme: {theme}
      </button>

      <ul
        tabIndex={0}
        className="dropdown-content menu bg-base-200 rounded-box z-50 mt-2 w-56 p-2 shadow"
      >
        {themes.map((name) => (
          <li key={name}>
            <button
              type="button"
              onClick={() => changeTheme(name)}
              className={theme === name ? "active" : ""}
            >
              <span
                data-theme={name}
                className="bg-base-100 text-base-content grid h-6 w-10 grid-cols-2 gap-0.5 rounded p-1"
              >
                <span className="bg-primary rounded-sm" />
                <span className="bg-secondary rounded-sm" />
                <span className="bg-accent rounded-sm" />
                <span className="bg-neutral rounded-sm" />
              </span>
              <span>{name}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
