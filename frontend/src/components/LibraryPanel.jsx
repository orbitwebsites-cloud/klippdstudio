import { useCallback, useEffect, useRef, useState } from "react";
import { Upload, Loader2, Trash2, Image as ImageIcon, Film, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import api, { API, apiErrorMessage } from "@/lib/klipApi";
import AssetPackStatus from "@/components/AssetPackStatus";

export default function LibraryPanel({ onPickAsset, activeSelection, niche = "gaming", standalone = false }) {
    const [items, setItems] = useState([]);
    const [uploading, setUploading] = useState(false);
    const [rightsConfirmed, setRightsConfirmed] = useState(false);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState(false);
    const inputRef = useRef();

    const refresh = useCallback(async () => {
        try {
            const { data } = await api.get("/library");
            setItems(data.items || []);
            setLoadError(false);
        } catch (e) {
            console.error("library fetch failed", e);
            setLoadError(true);
        } finally { setLoading(false); }
    }, []);

    useEffect(() => { refresh(); }, [refresh]);

    const handleFiles = async (files) => {
        if (!files || !files.length) return;
        if (!rightsConfirmed) {
            toast.error("Confirm that you own the assets or have commercial rights first.");
            return;
        }
        setUploading(true);
        let completed = 0;
        try {
            for (const f of files) {
                const fd = new FormData();
                fd.append("file", f);
                fd.append("rights_status", "user_owned_attested");
                fd.append("rights_attestation", "I own or have commercial rights to this asset");
                fd.append("license_id", "user-attestation-v1");
                try {
                    const { data } = await api.post("/library/upload", fd, {
                        timeout: 0,
                    });
                    if (!data?.ok || data?.status === "quarantined") {
                        throw new Error("The asset could not be approved for editing");
                    }
                    completed += 1;
                } catch (e) {
                    toast.error(`${f.name}: ${apiErrorMessage(e, "upload failed")}`);
                }
            }
            if (completed) toast.success(`Uploaded ${completed} asset${completed === 1 ? "" : "s"}`);
            await refresh();
        } finally { setUploading(false); }
    };

    const assetUrl = (value) => {
        if (!value) return "";
        if (/^https?:\/\//i.test(value)) return value;
        if (value.startsWith("/api/")) return `${API.replace(/\/api$/, "")}${value}`;
        return `${API}${value.startsWith("/") ? "" : "/"}${value}`;
    };

    const deleteItem = async (name) => {
        if (!confirm(`Delete "${name}"?`)) return;
        try {
            await api.delete(`/library/${encodeURIComponent(name)}`);
            refresh();
        } catch (e) { toast.error("delete failed"); }
    };

    return (
        <section className="mt-12" data-testid="library-section">
            <div className="flex items-end justify-between mb-6 flex-wrap gap-3">
                <div>
                    <div className="font-mono text-xs text-white/40 tracking-widest mb-2">// PERSONAL VAULT</div>
                    <div className="font-display text-3xl tracking-wider">MY LIBRARY</div>
                    <div className="text-white/50 text-sm mt-1">
                        {standalone
                            ? "Your own logos, cutaways, memes, clips, and approved B-roll assets."
                            : "Your own logos, cutaways, memes, clips. Pick an asset for the selected B-roll moment."}
                    </div>
                </div>
                <label
                    className={`btn-brand ${rightsConfirmed ? "cursor-pointer" : "cursor-not-allowed opacity-50"}`}
                    data-testid="library-upload-btn"
                >
                    {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                    {uploading ? "Uploading..." : "Add Assets"}
                    <input
                        ref={inputRef}
                        type="file"
                        multiple
                        accept="video/*,image/*,.mp4,.mov,.webm,.gif,.png,.jpg,.jpeg,.webp"
                        className="hidden"
                        disabled={!rightsConfirmed}
                        onChange={(e) => {
                            handleFiles([...(e.target.files || [])]);
                            e.target.value = "";
                        }}
                    />
                </label>
            </div>

            <label className="mb-5 flex items-center gap-2 text-xs text-white/60 font-mono cursor-pointer">
                <input
                    type="checkbox"
                    checked={rightsConfirmed}
                    onChange={(event) => setRightsConfirmed(event.target.checked)}
                    className="accent-[#CCFF00]"
                    data-testid="library-rights-confirmation"
                />
                I own these assets or have commercial rights to use them.
            </label>

            <AssetPackStatus niche={niche} onResolved={refresh} />

            {loading ? (
                <div className="panel p-8 text-center text-white/40 font-mono text-sm">Loading...</div>
            ) : loadError ? (
                <div className="panel p-8 text-center text-white/50 font-mono text-sm" role="alert">
                    Your asset library could not be loaded.
                    <button className="btn-ghost mx-auto mt-4" onClick={() => { setLoading(true); refresh(); }}>
                        <RefreshCw className="w-4 h-4" /> Try again
                    </button>
                </div>
            ) : items.length === 0 ? (
                <div
                    className="panel p-12 text-center cursor-pointer hover:bg-white/[0.02] transition-colors border-dashed"
                    onClick={() => inputRef.current?.click()}
                    data-testid="library-empty"
                >
                    <Upload className="w-8 h-8 mx-auto text-white/30 mb-3" />
                    <div className="font-display text-2xl tracking-wider">DROP YOUR ASSETS</div>
                    <div className="text-white/40 text-sm mt-2 font-mono">
                        VIDEOS · LOGOS · IMAGES · GIFS · MEMES
                    </div>
                </div>
            ) : (
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
                    {items.map((it) => {
                        const selected = activeSelection?.id === it.id;
                        return (
                            <article
                                key={it.id}
                                className="panel relative group overflow-hidden"
                                style={{ borderColor: selected ? "#CCFF00" : "rgba(255,255,255,0.1)" }}
                                data-testid={`library-item-${it.id}`}
                            >
                                <button
                                    type="button"
                                    disabled={!onPickAsset}
                                    className={`block w-full text-left ${onPickAsset ? "cursor-pointer" : "cursor-default"}`}
                                    aria-label={onPickAsset ? `Use ${it.name} as B-roll` : `${it.name} library asset`}
                                    aria-pressed={onPickAsset ? selected : undefined}
                                    onClick={() => onPickAsset?.(it)}
                                >
                                    <div className="aspect-square bg-black flex items-center justify-center overflow-hidden">
                                        {it.kind === "image" ? (
                                            <img
                                                src={assetUrl(it.url)}
                                                alt={it.name}
                                                className="w-full h-full object-cover"
                                            />
                                        ) : (
                                            <video
                                                src={assetUrl(it.url)}
                                                muted
                                                loop
                                                playsInline
                                                onMouseEnter={(e) => e.target.play?.().catch(() => {})}
                                                onMouseLeave={(e) => e.target.pause?.()}
                                                className="w-full h-full object-cover"
                                            />
                                        )}
                                    </div>
                                    <div className="px-2 py-1.5 flex items-center gap-1.5">
                                        {it.kind === "image" ? (
                                            <ImageIcon className="w-3 h-3 text-white/40 flex-shrink-0" />
                                        ) : (
                                            <Film className="w-3 h-3 text-white/40 flex-shrink-0" />
                                        )}
                                        <div className="font-mono text-[10px] text-white/60 truncate flex-1">
                                            {it.name}
                                        </div>
                                    </div>
                                </button>
                                <button
                                    onClick={(e) => { e.stopPropagation(); deleteItem(it.name); }}
                                    className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 bg-black/70 p-1 hover:bg-[#ff3333] transition-[opacity,background-color] duration-150"
                                    data-testid={`library-delete-${it.id}`}
                                    aria-label={`Delete ${it.name}`}
                                >
                                    <Trash2 className="w-3 h-3" />
                                </button>
                                {selected && (
                                    <div className="absolute inset-0 border-2 border-[#CCFF00] pointer-events-none" />
                                )}
                            </article>
                        );
                    })}
                </div>
            )}
            {items.length > 0 && activeSelection && (
                <div className="mt-4 p-3 panel font-mono text-xs text-[#CCFF00] text-center">
                    ✓ &quot;{activeSelection.name}&quot; is assigned to a B-roll moment.
                </div>
            )}
        </section>
    );
}
