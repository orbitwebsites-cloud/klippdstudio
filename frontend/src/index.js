import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { ClerkProvider } from "@clerk/clerk-react";
import "@/index.css";
import App from "@/App";
import { queryClient } from "@/lib/queryClient";

// Clerk is optional: with a publishable key the app runs in authenticated mode;
// without one it falls back to anonymous per-browser isolation so local dev and
// self-hosting work without a Clerk instance.
const clerkPublishableKey = process.env.REACT_APP_CLERK_PUBLISHABLE_KEY;

const tree = (
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>
);

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    {clerkPublishableKey ? (
      <ClerkProvider publishableKey={clerkPublishableKey} afterSignOutUrl="/">
        {tree}
      </ClerkProvider>
    ) : (
      tree
    )}
  </React.StrictMode>,
);
