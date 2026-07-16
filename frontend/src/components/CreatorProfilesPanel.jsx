import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, Dna, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
    analyzeCreatorProfile,
    apiErrorMessage,
    createCreatorProfile,
    featureAccessState,
    getCreatorProfiles,
    listProjects,
} from "@/lib/klipApi";

const emptyReference = () => ({ type: "owned_project", value: "" });

const profileList = (payload) => {
    if (Array.isArray(payload)) return payload;
    return payload?.profiles || payload?.items || [];
};

const evidenceList = (profile) => {
    const value = profile?.evidence || profile?.analysis?.evidence || profile?.signals || [];
    if (Array.isArray(value)) return value;
    return Object.entries(value || {}).map(([label, detail]) => ({ label, detail }));
};

const evidenceText = (item) => {
    if (typeof item === "string") return item;
    return item?.detail || item?.description || item?.label || item?.signal || "Style signal detected";
};

export default function CreatorProfilesPanel({ selectedProfileId, onSelectProfile }) {
    const [profiles, setProfiles] = useState([]);
    const [featureState, setFeatureState] = useState("available");
    const [loading, setLoading] = useState(true);
    const [composerOpen, setComposerOpen] = useState(false);
    const [name, setName] = useState("");
    const [references, setReferences] = useState([emptyReference()]);
    const [projects, setProjects] = useState([]);
    const [rightsConfirmed, setRightsConfirmed] = useState(false);
    const [analyzing, setAnalyzing] = useState(false);

    const loadProfiles = useCallback(async () => {
        try {
            const data = await getCreatorProfiles();
            const next = profileList(data);
            setProfiles(next);
            if (!selectedProfileId && next[0]?.id) onSelectProfile?.(next[0].id);
        } catch (error) {
            const access = featureAccessState(error);
            if (access) setFeatureState(access);
            else toast.error(apiErrorMessage(error, "Creator profiles could not be loaded"));
        } finally {
            setLoading(false);
        }
    }, [onSelectProfile, selectedProfileId]);

    useEffect(() => { loadProfiles(); }, [loadProfiles]);

    useEffect(() => {
        listProjects()
            .then((items) => setProjects(items.filter((project) => project?.id && project?.analysis)))
            .catch(() => setProjects([]));
    }, []);

    const selectedProfile = useMemo(
        () => profiles.find((profile) => String(profile.id) === String(selectedProfileId)),
        [profiles, selectedProfileId]
    );

    const updateReference = (index, patch) => {
        setReferences((current) => current.map((reference, i) => i === index ? { ...reference, ...patch } : reference));
    };

    const analyze = async () => {
        const cleanReferences = references
            .map((reference) => ({ ...reference, value: reference.value.trim() }))
            .filter((reference) => reference.value);
        if (!name.trim() || cleanReferences.length === 0) {
            toast.error("Name the profile and add at least one reference.");
            return;
        }
        if (!rightsConfirmed) {
            toast.error("Confirm your rights and consent before analysis.");
            return;
        }

        setAnalyzing(true);
        const payload = {
            name: name.trim(),
            references: cleanReferences,
            rights_attested: true,
            consent_scope: "editing_style_analysis",
        };
        try {
            const analyzed = await analyzeCreatorProfile(payload);
            const analyzedProfile = analyzed?.profile || analyzed;
            const saved = analyzedProfile?.id
                ? analyzedProfile
                : await createCreatorProfile({ ...payload, analysis: analyzedProfile });
            const savedProfile = saved?.profile || saved;
            await loadProfiles();
            if (savedProfile?.id) onSelectProfile?.(savedProfile.id);
            setComposerOpen(false);
            setName("");
            setReferences([emptyReference()]);
            setRightsConfirmed(false);
            toast.success("Creator DNA profile is ready");
        } catch (error) {
            const access = featureAccessState(error);
            if (access) setFeatureState(access);
            else toast.error(apiErrorMessage(error, "Style analysis failed"));
        } finally {
            setAnalyzing(false);
        }
    };

    if (featureState !== "available") {
        const upgrade = featureState === "upgrade";
        return (
            <section className="panel p-5" data-testid="creator-profiles-unavailable">
                <div className="font-mono text-[10px] text-[#ccff00] tracking-widest">// CREATOR DNA</div>
                <h2 className="font-display text-3xl tracking-wider mt-2">
                    {upgrade ? "CREATOR DNA NEEDS AN UPGRADE" : "WORKSPACE FEATURE UNAVAILABLE"}
                </h2>
                <p className="text-white/55 text-sm leading-6 mt-3">
                    {upgrade
                        ? "Creator DNA is enabled on the backend, but this workspace plan cannot use it yet."
                        : "Creator DNA is visible in this editor, but the backend route is not enabled for this workspace yet."}
                </p>
            </section>
        );
    }

    const confidence = selectedProfile?.confidence ?? selectedProfile?.analysis?.confidence;
    const confidencePercent = confidence == null ? null : Math.round(Number(confidence) <= 1 ? Number(confidence) * 100 : Number(confidence));
    const evidence = evidenceList(selectedProfile);

    return (
        <section className="panel p-6" data-testid="creator-profiles-panel">
            <div className="flex items-start justify-between gap-3">
                <div>
                    <div className="font-mono text-xs text-[#ccff00] tracking-widest">// PREMIUM</div>
                    <div className="font-display text-2xl tracking-wider flex items-center gap-2 mt-1">
                        <Dna className="w-5 h-5 text-[#ccff00]" /> CREATOR DNA
                    </div>
                </div>
                <button
                    type="button"
                    className="btn-ghost !p-2"
                    onClick={() => setComposerOpen((open) => !open)}
                    aria-label="Add creator profile"
                    data-testid="creator-profile-add"
                >
                    <Plus className="w-4 h-4" />
                </button>
            </div>

            <p className="text-xs text-white/50 mt-3 leading-relaxed">
                Learn pacing, captions, cuts, and visual rhythm from videos already uploaded to your Klipped workspace. Klipped copies editing patterns, not a creator&apos;s identity, footage, voice, or branding.
            </p>

            {loading ? (
                <div className="flex items-center gap-2 mt-4 text-xs font-mono text-white/40">
                    <Loader2 className="w-3 h-3 animate-spin" /> LOADING PROFILES
                </div>
            ) : profiles.length > 0 ? (
                <select
                    value={selectedProfileId || ""}
                    onChange={(event) => onSelectProfile?.(event.target.value || null)}
                    className="mt-4 w-full bg-black border border-white/20 px-3 py-2 text-sm text-white font-mono focus:border-[#ccff00] outline-none"
                    data-testid="creator-profile-select"
                >
                    <option value="">No Creator DNA</option>
                    {profiles.map((profile) => (
                        <option key={profile.id} value={profile.id}>{profile.name || "Untitled profile"}</option>
                    ))}
                </select>
            ) : (
                <div className="mt-4 border border-dashed border-white/15 p-3 text-xs text-white/40 font-mono">
                    NO SAVED DNA YET
                </div>
            )}

            {selectedProfile ? (
                <div className="mt-4 border-l-2 border-[#ccff00] pl-3" data-testid="creator-profile-evidence">
                    <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-[10px] tracking-widest text-white/40">ANALYSIS CONFIDENCE</span>
                        <span className="font-display text-xl text-[#ccff00]">{confidencePercent == null ? "—" : `${confidencePercent}%`}</span>
                    </div>
                    {confidencePercent != null ? (
                        <div className="h-1 bg-white/10 mt-1"><div className="h-1 bg-[#ccff00]" style={{ width: `${Math.max(0, Math.min(100, confidencePercent))}%` }} /></div>
                    ) : null}
                    {evidence.slice(0, 4).map((item, index) => (
                        <div key={`${evidenceText(item)}-${index}`} className="flex gap-2 mt-2 text-xs text-white/60">
                            <Check className="w-3 h-3 text-[#ccff00] mt-0.5 flex-shrink-0" />
                            <span>{evidenceText(item)}</span>
                        </div>
                    ))}
                </div>
            ) : null}

            {composerOpen ? (
                <div className="mt-5 pt-5 border-t border-white/10 space-y-3" data-testid="creator-profile-composer">
                    <input
                        value={name}
                        onChange={(event) => setName(event.target.value)}
                        placeholder="PROFILE NAME"
                        className="w-full bg-black border border-white/20 px-3 py-2 text-sm text-white font-mono placeholder:text-white/30 focus:border-[#ccff00] outline-none"
                    />
                    {references.map((reference, index) => (
                        <div key={index} className="grid grid-cols-[1fr_32px] gap-2">
                            <div className="col-span-2 font-mono text-[9px] text-white/40 tracking-widest">
                                OWNED KLIPPED PROJECT
                            </div>
                            <select
                                value={reference.value}
                                onChange={(event) => updateReference(index, { value: event.target.value })}
                                className="min-w-0 bg-black border border-white/20 px-2 py-2 text-xs text-white font-mono placeholder:text-white/25 focus:border-[#ccff00] outline-none"
                                aria-label={`Owned Klipped project ${index + 1}`}
                            >
                                <option value="">Select analyzed project...</option>
                                {projects.map((project) => (
                                    <option key={project.id} value={project.id}>
                                        {project.name || project.id}
                                    </option>
                                ))}
                            </select>
                            <button
                                type="button"
                                onClick={() => setReferences((current) => current.length === 1 ? [emptyReference()] : current.filter((_, i) => i !== index))}
                                className="border border-white/10 text-white/40 hover:text-[#ff3333]"
                                aria-label={`Remove reference ${index + 1}`}
                            ><Trash2 className="w-3 h-3 mx-auto" /></button>
                        </div>
                    ))}
                    <button type="button" className="text-[10px] font-mono tracking-widest text-[#ccff00]" onClick={() => setReferences((current) => [...current, emptyReference()])}>
                        + ADD REFERENCE
                    </button>
                    {!projects.length && (
                        <div className="border border-white/10 bg-white/[0.03] p-3 text-[11px] text-white/45 leading-relaxed">
                            Analyze at least one owned project before creating Creator DNA. Only analyzed projects can provide editing-style evidence.
                        </div>
                    )}
                    <label className="flex items-start gap-2 text-[11px] text-white/60 cursor-pointer leading-relaxed">
                        <input
                            type="checkbox"
                            checked={rightsConfirmed}
                            onChange={(event) => setRightsConfirmed(event.target.checked)}
                            className="accent-[#ccff00] mt-0.5"
                            data-testid="creator-profile-rights"
                        />
                        I own or control these Klipped projects and have permission from every featured creator to analyze them for editing-style reference. I will not use this to impersonate a creator.
                    </label>
                    <div className="flex gap-2 p-2 border border-amber-400/20 bg-amber-400/[0.04] text-[10px] text-amber-200/70 leading-relaxed">
                        <AlertTriangle className="w-3 h-3 flex-shrink-0 mt-0.5" /> Public URL importing is not supported. Only projects already owned and uploaded in this Klipped workspace can be analyzed.
                    </div>
                    <button
                        type="button"
                        className="btn-brand w-full !justify-center"
                        disabled={analyzing || !rightsConfirmed}
                        onClick={analyze}
                        data-testid="creator-profile-analyze"
                    >
                        {analyzing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Dna className="w-4 h-4" />}
                        {analyzing ? "ANALYZING DNA" : "ANALYZE + SAVE"}
                    </button>
                </div>
            ) : null}
        </section>
    );
}
