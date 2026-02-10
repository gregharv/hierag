import { useQuery } from "@tanstack/react-query";

import { ChatApp } from "./components/ChatApp";
import { useApiBase } from "./hooks/useApiBase";

export default function App() {
  const apiBase = useApiBase();

  const profileBootstrap = useQuery({
    queryKey: ["profile-bootstrap"],
    queryFn: async () => {
      const response = await fetch(`${apiBase}/profile`);
      if (!response.ok) {
        throw new Error("Profile bootstrap failed");
      }
      return response.json();
    },
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
