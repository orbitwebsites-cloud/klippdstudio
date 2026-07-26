import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@clerk/clerk-react";
import { clerkEnabled, refreshMediaToken, clearMediaToken } from "@/lib/klipApi";

// Media/download URLs can't send an Authorization header, so authenticated mode
// authorizes them with a short-lived server-signed token appended to the URL.
// This hook fetches and refreshes that token; components pass the returned value
// into the media URL helpers so the URLs (and <video> sources) update once it's
// ready. In anonymous mode there is no token and the helpers fall back to the
// per-browser client id, so the hook stays disabled.
export function useMediaToken() {
    // clerkEnabled() is a build-time constant, so this branch is stable across
    // renders and the conditional hook call below is safe.
    if (!clerkEnabled()) {
        // eslint-disable-next-line react-hooks/rules-of-hooks
        return useDisabledMediaToken();
    }
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return useClerkMediaToken();
}

function useDisabledMediaToken() {
    return null;
}

function useClerkMediaToken() {
    const { isSignedIn, userId } = useAuth();

    // Drop any cached token the moment the user changes or signs out, so a URL
    // can never carry the previous account's credential.
    useEffect(() => {
        if (!isSignedIn) clearMediaToken();
    }, [isSignedIn, userId]);

    const { data } = useQuery({
        // Keyed by user so switching accounts refetches immediately.
        queryKey: ["media-token", userId],
        queryFn: refreshMediaToken,
        enabled: Boolean(isSignedIn),
        staleTime: 10 * 60 * 1000,
        refetchInterval: 10 * 60 * 1000,
        retry: 1,
    });
    return data || null;
}
