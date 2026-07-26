import { useAuth } from "@clerk/clerk-react";
import { clerkEnabled } from "@/lib/klipApi";

// Whether the app is authorized to make user-scoped API calls.
//
// In anonymous mode (no Clerk) there is no sign-in, so the app is always
// authorized. clerkEnabled() is a build-time constant, so the branch below
// never changes between renders and the conditional hook call is stable.
export function useSignedIn() {
    if (!clerkEnabled()) {
        return { ready: true, signedIn: true };
    }
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const { isLoaded, isSignedIn } = useAuth();
    return { ready: isLoaded, signedIn: Boolean(isSignedIn) };
}
