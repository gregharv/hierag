import { useQuery } from "@tanstack/react-query";

import { ChatApp } from "./components/ChatApp";
import { useApiBase } from "./hooks/useApiBase";

function normalizeUserId(value: string) {
  return value.replace(/[^a-z0-9]/gi, "").toUpperCase().slice(0, 6);
}

export default function App() {
  const apiBase = useApiBase();
  const storedUserId =
    typeof window === "undefined"
      ? ""
      : normalizeUserId(window.localStorage.getItem("userId") || "");

  const profileBootstrap = useQuery({
    queryKey: ["profile-bootstrap", storedUserId],
    queryFn: async () => {
      if (!storedUserId) {
        return null;
      }

      const response = await fetch(`${apiBase}/profile`, {
        headers: { "X-User-ID": storedUserId },
      });
      if (response.status === 401) {
        return null;
      }
      if (!response.ok) {
        throw new Error("Profile bootstrap failed");
      }
      return response.json();
    },
    enabled: Boolean(storedUserId),
    retry: 1,
    staleTime: 30_000,
  });

  return (
    <>
      {profileBootstrap.isError ? (
        <div className="alert alert-warning rounded-none">
          <span>Backend connection issue. The UI is still usable.</span>
        </div>
      ) : null}
      <ChatApp />
    </>
  );
}
