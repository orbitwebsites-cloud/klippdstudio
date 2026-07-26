import { SignedIn, SignedOut, RedirectToSignIn } from "@clerk/clerk-react";
import { clerkEnabled } from "@/lib/klipApi";

// Wrap routes that require a signed-in user. When Clerk is not configured
// (anonymous mode) the children render as-is, preserving local dev / self-host.
export default function AuthGate({ children }) {
    if (!clerkEnabled()) return children;
    return (
        <>
            <SignedIn>{children}</SignedIn>
            <SignedOut>
                <RedirectToSignIn />
            </SignedOut>
        </>
    );
}
