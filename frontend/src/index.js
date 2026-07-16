import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ClerkProvider } from "@clerk/react";
import "@/index.css";
import App from "@/App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
  },
});

const root = ReactDOM.createRoot(document.getElementById("root"));
const publishableKey = process.env.REACT_APP_CLERK_PUBLISHABLE_KEY;

function Root() {
  const content = (
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <App authEnabled={Boolean(publishableKey)} />
      </QueryClientProvider>
    </React.StrictMode>
  );

  return publishableKey
    ? <ClerkProvider publishableKey={publishableKey}>{content}</ClerkProvider>
    : content;
}

root.render(<Root />);
