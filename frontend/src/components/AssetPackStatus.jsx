import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Loader2, Package, RefreshCw, Sparkles } from "lucide-react";
import { getAssetPackStatus, resolveAssetPack } from "@/lib/klipApi";

const READY_STATES = new Set(["ready", "complete", "completed", "installed", "published"]);
const ACTIVE_STATES = new Set(["resolving", "searching", "downloading", "installing", "queued", "working"]);
const STARTABLE_STATES = new Set([
    "", "available", "enabled", "idle", "missing", "not_found", "not_installed", "pending", "unresolved",
]);

const cleanNiche = (value) => {
    const niche = String(value || "gaming").trim().toLowerCase();
    if (niche.includes("minecraft") || niche.includes("gaming")) return "gaming";
    return niche.replace(/[\s-]+/g, "_") || "gaming";
};

const titleCase = (value) => value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

const asCount = (...values) => {
    const counts = values
        .map((candidate) => Number(candidate))
        .filter((candidate) => Number.isFinite(candidate) && candidate >= 0);
    return counts.length ? Math.max(...counts) : 0;
};

const normalizePack = (payload, fallbackNiche) => {
    const pack = payload?.pack || payload?.data || payload || {};
    const directAssets = Array.isArray(pack.assets) ? pack.assets : [];
    const lastStatuses = Object.entries(pack.last_status || {})
        .filter(([packId]) => packId.startsWith(`${fallbackNiche}-`))
        .map(([, status]) => status);
    const installedCount = lastStatuses.reduce((total, status) => total + asCount(status?.count), 0);
    const lastStatus = lastStatuses.find((status) => status?.status === "published")?.status
        || lastStatuses[0]?.status || "";
    const sources = pack.source_summary || pack.sources || {};
    const sourceList = Array.isArray(sources) ? sources : [];
    const sourceKinds = sourceList.reduce((totals, source) => {
        const kind = String(source?.kind || source?.type || "").toLowerCase();
        if (kind.includes("generat")) totals.generated += 1;
        else if (kind.includes("cache") || source?.cached) totals.cached += 1;
        else totals.providers += 1;
        return totals;
    }, { cached: 0, providers: 0, generated: 0 });

    const directSource = String(pack.source || "").toLowerCase();
    const directCount = directAssets.length;
    const inferredStatus = pack.status || pack.state || lastStatus
        || (directCount ? "completed" : Array.isArray(pack.errors) && pack.errors.length ? "error" : "");

    return {
        niche: cleanNiche(pack.niche || fallbackNiche),
        status: String(inferredStatus).toLowerCase(),
        assetCount: asCount(
            pack.asset_count, pack.assets_count, pack.published_count, pack.total_assets,
            pack.items?.length, directCount, installedCount,
        ),
        cachedCount: asCount(
            pack.cached_count, pack.cache_count, sources.cached,
            directSource.includes("cache") ? directCount : undefined, sourceKinds.cached,
        ),
        providerCount: asCount(
            pack.provider_count, pack.downloaded_count, sources.providers,
            directSource.includes("provider") ? directCount : undefined, installedCount, sourceKinds.providers,
        ),
        generatedCount: asCount(
            pack.generated_count, sources.generated,
            directSource.includes("generated") ? directCount : undefined, sourceKinds.generated,
        ),
        message: typeof pack.message === "string" ? pack.message : "",
    };
};

const isUnsupported = (error) => [404, 405, 501].includes(error?.response?.status);

const friendlyError = (error) => {
    if (!error?.response) return "The asset service cannot be reached right now.";
    if (error.response.status >= 500) return "Automatic asset packs are temporarily unavailable.";
    return "The pack could not be finished. Your existing assets are still safe.";
};

export default function AssetPackStatus({ niche = "gaming", onResolved }) {
    const selectedNiche = cleanNiche(niche);
    const [availability, setAvailability] = useState("checking");
    const [pack, setPack] = useState(null);
    const [phase, setPhase] = useState("checking");
    const [error, setError] = useState("");
    const autoStartedRef = useRef(false);

    const install = useCallback(async () => {
        setAvailability("available");
        setPhase("resolving");
        setError("");
        try {
            const resolved = await resolveAssetPack(selectedNiche);
            let next = normalizePack(resolved, selectedNiche);
            try {
                const refreshed = normalizePack(await getAssetPackStatus(selectedNiche), selectedNiche);
                if (refreshed.assetCount > 0 || refreshed.status) next = refreshed;
            } catch (statusError) {
                if (isUnsupported(statusError)) throw statusError;
            }
            setPack(next);
            if (next.status === "error") {
                setPhase("error");
                setError("No approved pack could be installed yet. Your existing assets are still safe.");
            } else {
                setPhase(READY_STATES.has(next.status) || next.assetCount > 0 ? "ready" : next.status || "ready");
                onResolved?.();
            }
        } catch (requestError) {
            if (isUnsupported(requestError)) {
                setAvailability("unsupported");
                return;
            }
            setPhase("error");
            setError(friendlyError(requestError));
        }
    }, [onResolved, selectedNiche]);

    useEffect(() => {
        let cancelled = false;
        autoStartedRef.current = false;
        setAvailability("checking");
        setPhase("checking");
        setError("");

        getAssetPackStatus(selectedNiche)
            .then((payload) => {
                if (cancelled) return;
                const next = normalizePack(payload, selectedNiche);
                setAvailability("available");
                setPack(next);

                if (READY_STATES.has(next.status) || next.assetCount > 0) {
                    setPhase("ready");
                    return;
                }
                if (ACTIVE_STATES.has(next.status)) {
                    setPhase("resolving");
                    return;
                }
                if (STARTABLE_STATES.has(next.status) && !autoStartedRef.current) {
                    autoStartedRef.current = true;
                    install();
                    return;
                }
                setPhase(next.status === "error" ? "error" : "ready");
                if (next.status === "error") setError(next.message || "The pack needs another try.");
            })
            .catch((requestError) => {
                if (cancelled) return;
                if (isUnsupported(requestError)) {
                    setAvailability("unsupported");
                    return;
                }
                setAvailability("available");
                setPhase("error");
                setError(friendlyError(requestError));
            });

        return () => { cancelled = true; };
    }, [install, selectedNiche]);

    useEffect(() => {
        if (availability !== "available" || phase !== "resolving") return undefined;
        const timer = window.setInterval(() => {
            getAssetPackStatus(selectedNiche)
                .then((payload) => {
                    const next = normalizePack(payload, selectedNiche);
                    setPack(next);
                    if (READY_STATES.has(next.status) || next.assetCount > 0) {
                        setPhase("ready");
                        onResolved?.();
                    } else if (next.status === "error") {
                        setPhase("error");
                        setError(next.message || "The pack needs another try.");
                    }
                })
                .catch((requestError) => {
                    if (isUnsupported(requestError)) setAvailability("unsupported");
                });
        }, 2500);
        return () => window.clearInterval(timer);
    }, [availability, onResolved, phase, selectedNiche]);

    if (availability !== "available") return null;

    const isWorking = phase === "resolving";
    const isReady = phase === "ready";
    const sourceParts = [
        pack?.cachedCount ? `${pack.cachedCount} cached` : null,
        pack?.providerCount ? `${pack.providerCount} sourced` : null,
        pack?.generatedCount ? `${pack.generatedCount} made for you` : null,
    ].filter(Boolean);

    return (
        <div className="panel mb-5 overflow-hidden" data-testid="asset-pack-status" aria-live="polite">
            <div className="h-1 bg-[#CCFF00]" />
            <div className="p-4 flex flex-col md:flex-row md:items-center gap-4">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                    <div className="w-10 h-10 border border-white/15 bg-black flex items-center justify-center flex-shrink-0">
                        {isWorking ? <Loader2 className="w-5 h-5 text-[#CCFF00] animate-spin" />
                            : phase === "error" ? <AlertTriangle className="w-5 h-5 text-[#ff6b6b]" />
                            : isReady ? <Check className="w-5 h-5 text-[#CCFF00]" strokeWidth={3} />
                            : <Package className="w-5 h-5 text-white/60" />}
                    </div>
                    <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-mono text-[10px] tracking-[0.18em] text-white/40">AUTO ASSET PACK</span>
                            <span className="bg-[#CCFF00] text-black font-mono text-[10px] px-1.5 py-0.5">
                                {titleCase(selectedNiche)} / Detected
                            </span>
                        </div>
                        <div className="font-display text-xl tracking-wider mt-1">
                            {isWorking ? "FINDING + INSTALLING" : phase === "error" ? "PACK NEEDS A RETRY" : "NICHE PACK READY"}
                        </div>
                        <div className={`text-xs mt-1 ${phase === "error" ? "text-[#ff8b8b]" : "text-white/45"}`}>
                            {phase === "error" ? error
                                : isWorking ? "Looking for reusable assets, checking them, and filling any gaps."
                                : pack?.message || "The editor can pull from this pack automatically when a moment needs it."}
                        </div>
                    </div>
                </div>

                {phase !== "error" ? (
                    <div className="flex items-stretch border border-white/10 flex-shrink-0">
                        <div className="px-4 py-2 text-center border-r border-white/10 min-w-20">
                            <div className="font-display text-2xl leading-none text-[#CCFF00]">{pack?.assetCount || 0}</div>
                            <div className="font-mono text-[9px] tracking-widest text-white/35 mt-1">ASSETS</div>
                        </div>
                        <div className="px-4 py-2 min-w-36 flex flex-col justify-center">
                            <div className="flex items-center gap-1.5 font-mono text-[9px] tracking-widest text-white/35">
                                <Sparkles className="w-3 h-3" /> SOURCES
                            </div>
                            <div className="font-mono text-[10px] text-white/65 mt-1">
                                {isWorking ? "Checking best matches..." : sourceParts.join(" / ") || "Ready on demand"}
                            </div>
                        </div>
                    </div>
                ) : (
                    <button className="btn-ghost flex-shrink-0" onClick={install} data-testid="asset-pack-retry">
                        <RefreshCw className="w-4 h-4" /> Try again
                    </button>
                )}
            </div>
        </div>
    );
}
