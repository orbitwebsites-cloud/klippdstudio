import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { toast } from "sonner";
import {
    ArrowLeft,
    Download,
    Loader2,
    Scissors,
    Wand2,
    Volume2,
    Film,
    Zap,
    RefreshCw,
    Search,
    Check,
    Upload,
    Flame,
    Monitor,
    Smartphone,
    Square,
    AlertTriangle,
    ArrowDown,
} from "lucide-react";
import LibraryPanel from "@/components/LibraryPanel";
import CreatorProfilesPanel from "@/components/CreatorProfilesPanel";
import EditChatPanel from "@/components/EditChatPanel";
import {
    API,
    getProject,
    analyzeProject,
    brollSearch,
    uploadCustomBroll,
    extractViralClips,
    renderProject,
    mediaOriginal,
    mediaOutput,
    mediaClip,
    downloadUrl,
    apiErrorMessage,
    saveEditOptions,
} from "@/lib/klipApi";

const STATUS_LABELS = {
    uploaded: "UPLOADED",
    queued: "QUEUED",
    extracting_audio: "EXTRACTING AUDIO",
    transcribing: "TRANSCRIBING",
    analyzing: "AI ANALYZING",
    ready: "READY TO EDIT",
    queued_render: "QUEUED",
    rendering: "RENDERING",
    done: "DONE",
    error: "ERROR",
};

const IN_PROGRESS = new Set([
    "queued", "extracting_audio", "transcribing", "analyzing", "queued_render", "rendering",
]);

export default function Editor() {
    const { id } = useParams();
    const navigate = useNavigate();
    const location = useLocation();
    const [project, setProject] = useState(null);
    const projectId = project?.id;
    const projectStatus = project?.status;
    const [style, setStyle] = useState("tiktok");
    const [aspect, setAspect] = useState("16:9");
    const [renderOpts, setRenderOpts] = useState({
        remove_fillers: true, captions: true, sfx: true, zoom_ins: true, broll: true,
    });
    const [excludedFillers, setExcludedFillers] = useState(new Set());
    const [addedFillers, setAddedFillers] = useState(new Set());
    const [brollByMoment, setBrollByMoment] = useState({});
    const [customBrollByMoment, setCustomBrollByMoment] = useState({});
    const [uploadingBrollIdx, setUploadingBrollIdx] = useState(null);
    const [brollSelected, setBrollSelected] = useState({});
    const [searchingIdx, setSearchingIdx] = useState(null);
    const [renderStarting, setRenderStarting] = useState(false);
    const [viralClips, setViralClips] = useState([]);
    const [extractingClips, setExtractingClips] = useState(false);
    const [renderingClipLabel, setRenderingClipLabel] = useState(null);
    const [currentTime, setCurrentTime] = useState(0);
    const [libraryPick, setLibraryPick] = useState(null);
    const [creatorProfileId, setCreatorProfileId] = useState(null);
    const [draftLoadedFor, setDraftLoadedFor] = useState(null);
    const [showSettingsCue, setShowSettingsCue] = useState(false);
    const videoRef = useRef();
    const transcriptRef = useRef();
    const brollFileInputRef = useRef();
    const settingsRef = useRef();
    const trainingProfileId = useMemo(() => {
        try { return window.localStorage.getItem("klipped_active_training_profile") || null; }
        catch { return null; }
    }, []);

    const refresh = useCallback(async () => {
        try { setProject(await getProject(id)); }
        catch (error) {
            toast.error(apiErrorMessage(error, "Project not found"));
            navigate("/");
        }
    }, [id, navigate]);

    useEffect(() => { refresh(); }, [refresh]);

    useEffect(() => {
        if (!project) return;
        if (!IN_PROGRESS.has(project.status)) return;
        const t = setInterval(refresh, 2500);
        return () => clearInterval(t);
    }, [project, refresh]);

    useEffect(() => {
        if (project && project.status === "uploaded") {
            analyzeProject(id, { training_profile_id: trainingProfileId }).then(refresh).catch((e) =>
                toast.error(e?.response?.data?.detail || "Analysis failed to start"));
        }
    }, [project, id, refresh, trainingProfileId]);

    useEffect(() => {
        if (!location.state?.newUpload) return;
        try { window.sessionStorage.setItem(`klippd_settings_cue_${id}`, "1"); }
        catch { /* The cue is non-essential. */ }
    }, [id, location.state]);

    const words = useMemo(() => project?.transcript?.words || [], [project?.transcript?.words]);
    const analysis = project?.analysis || {};
    const autoFillers = useMemo(() => new Set(analysis.filler_indices || []), [analysis.filler_indices]);
    const emphasisSet = useMemo(() => new Set(analysis.emphasis_indices || []), [analysis.emphasis_indices]);
    const brollMoments = analysis.broll_moments || [];
    const generatedAssets = analysis.generated_assets || [];
    const resolvedPackAssets = analysis.resolved_pack_assets || [];

    const previewUrl = useCallback((value) => {
        if (!value) return "";
        if (/^https?:\/\//i.test(value)) return value;
        if (value.startsWith("/api/")) return `${API.replace(/\/api$/, "")}${value}`;
        return `${API}${value.startsWith("/") ? "" : "/"}${value}`;
    }, []);

    // Sync viral clips from server
    useEffect(() => {
        if (project?.viral_clips) setViralClips(project.viral_clips);
    }, [project?.viral_clips]);

    // Chat-applied edits are persisted by the server; mirror them into the
    // ordinary editor controls so preview, manual tweaks, and render agree.
    useEffect(() => {
        const options = project?.chat_render_options;
        if (!options) return;
        if (options.style) setStyle(options.style);
        if (options.aspect) setAspect(options.aspect);
        setRenderOpts((current) => ({
            ...current,
            ...Object.fromEntries(
                ["remove_fillers", "captions", "sfx", "zoom_ins", "broll"]
                    .filter((key) => typeof options[key] === "boolean")
                    .map((key) => [key, options[key]])
            ),
        }));
        if (Array.isArray(options.selected_broll)) {
            setBrollSelected(Object.fromEntries(
                options.selected_broll
                    .filter((item) => Number.isInteger(item?.word_index))
                    .map((item) => [item.word_index, item])
            ));
        }
        if (project.creator_profile_id) setCreatorProfileId(project.creator_profile_id);
    }, [project?.chat_render_options, project?.creator_profile_id]);

    useEffect(() => {
        if (!project || draftLoadedFor === project.id) return;
        const saved = project.edit_options || {};
        if (saved.style) setStyle(saved.style);
        if (saved.aspect) setAspect(saved.aspect);
        setRenderOpts((current) => ({
            ...current,
            ...Object.fromEntries(
                ["remove_fillers", "captions", "sfx", "zoom_ins", "broll"]
                    .filter((key) => typeof saved[key] === "boolean")
                    .map((key) => [key, saved[key]])
            ),
        }));
        setDraftLoadedFor(project.id);
    }, [project, draftLoadedFor]);

    useEffect(() => {
        if (!projectStatus || !["ready", "done"].includes(projectStatus)) return;
        try {
            if (window.sessionStorage.getItem(`klippd_settings_cue_${id}`) === "1") {
                setShowSettingsCue(true);
            }
        } catch { /* The cue is non-essential. */ }
    }, [id, projectStatus]);

    useEffect(() => {
        if (!projectId || draftLoadedFor !== projectId) return undefined;
        const timer = window.setTimeout(() => {
            saveEditOptions(id, { style, aspect, ...renderOpts })
                .catch(() => toast.error("Could not save edit settings. Try again before rendering."));
        }, 500);
        return () => window.clearTimeout(timer);
    }, [id, projectId, draftLoadedFor, style, aspect, renderOpts]);

    const effectiveFillers = useMemo(() => {
        const s = new Set(autoFillers);
        excludedFillers.forEach((i) => s.delete(i));
        addedFillers.forEach((i) => s.add(i));
        return s;
    }, [autoFillers, excludedFillers, addedFillers]);

    const activeIdx = useMemo(() => {
        if (!words.length) return -1;
        for (let i = 0; i < words.length; i++) {
            const w = words[i];
            if (w.start <= currentTime && currentTime <= w.end + 0.05) return i;
        }
        return -1;
    }, [words, currentTime]);

    useEffect(() => {
        if (activeIdx < 0) return;
        const el = document.getElementById(`w-${activeIdx}`);
        if (el && transcriptRef.current) {
            const r = el.getBoundingClientRect();
            const cr = transcriptRef.current.getBoundingClientRect();
            if (r.top < cr.top + 40 || r.bottom > cr.bottom - 40) {
                el.scrollIntoView({ behavior: "smooth", block: "center" });
            }
        }
    }, [activeIdx]);

    const toggleFiller = (i) => {
        if (autoFillers.has(i)) {
            const s = new Set(excludedFillers);
            s.has(i) ? s.delete(i) : s.add(i);
            setExcludedFillers(s);
        } else {
            const s = new Set(addedFillers);
            s.has(i) ? s.delete(i) : s.add(i);
            setAddedFillers(s);
        }
    };

    const jumpTo = (i) => {
        const w = words[i];
        if (!w || !videoRef.current) return;
        videoRef.current.currentTime = w.start;
        videoRef.current.play();
    };

    const searchBrollForMoment = async (idx, query) => {
        setSearchingIdx(idx);
        try {
            const r = await brollSearch(id, query);
            setBrollByMoment((prev) => ({ ...prev, [idx]: r.results || [] }));
        } catch (e) { toast.error(apiErrorMessage(e, "B-roll search failed")); }
        finally { setSearchingIdx(null); }
    };

    const uploadCustomBrollForMoment = async (idx, file) => {
        if (!file) return;
        const rightsAttested = window.confirm(
            "Confirm that you own this asset or have commercial rights to use it in exported videos."
        );
        if (!rightsAttested) return;
        setUploadingBrollIdx(idx);
        try {
            const r = await uploadCustomBroll(id, file, undefined, rightsAttested);
            if (!r?.ok || r?.status === "quarantined") {
                throw new Error("The asset could not be approved for editing");
            }
            setCustomBrollByMoment((prev) => ({
                ...prev,
                [idx]: [...(prev[idx] || []), r],
            }));
            // Auto-select the uploaded clip
            const wordIndex = brollMoments[idx]?.word_index;
            if (Number.isInteger(wordIndex)) {
                setBrollSelected((s) => ({ ...s, [wordIndex]: { ...r, word_index: wordIndex } }));
            }
            toast.success("Custom B-roll uploaded");
        } catch (e) {
            toast.error(apiErrorMessage(e, "B-roll upload failed"));
        } finally {
            setUploadingBrollIdx(null);
        }
    };

    const extractClips = async () => {
        setExtractingClips(true);
        try {
            const r = await extractViralClips(id);
            setViralClips(r.clips || []);
            if (!r.clips?.length) toast.info("No viral moments found — try a longer clip");
            else toast.success(`Found ${r.clips.length} viral moments`);
        } catch (e) {
            toast.error(apiErrorMessage(e, "Failed to extract clips"));
        } finally {
            setExtractingClips(false);
        }
    };

    const renderViralClip = async (clip, idx) => {
        const label = `clip_${idx + 1}_${Math.round(clip.start)}s`;
        setRenderingClipLabel(label);
        try {
            const opts = {
                style,
                aspect: "9:16",  // Viral clips default to vertical
                remove_fillers: renderOpts.remove_fillers,
                captions: renderOpts.captions,
                sfx: renderOpts.sfx,
                zoom_ins: renderOpts.zoom_ins,
                broll: false,     // Skip B-roll for viral clips (keeps it fast)
                excluded_filler_indices: [...excludedFillers],
                added_filler_indices: [...addedFillers],
                selected_broll: [],
                clip_start: clip.start,
                clip_end: clip.end,
                clip_label: label,
            };
            await renderProject(id, opts);
            toast.success(`Rendering "${clip.hook.substring(0, 40)}…"`);
            refresh();
        } catch (e) {
            toast.error(apiErrorMessage(e, "Clip render failed"));
        } finally {
            setRenderingClipLabel(null);
        }
    };

    const startRender = async () => {
        setRenderStarting(true);
        const selected_broll = Object.entries(brollSelected)
            .filter(([, v]) => v)
            .map(([wordIndex, v]) => ({
                word_index: Number.isInteger(v.word_index) ? v.word_index : Number(wordIndex),
                video_url: v.video_url,
                local_path: v.local_path,
                is_custom: v.is_custom,
                generated: v.generated,
            }))
            .filter((item) => Number.isInteger(item.word_index));
        const opts = {
            style,
            aspect,
            ...renderOpts,
            excluded_filler_indices: [...excludedFillers],
            added_filler_indices: [...addedFillers],
            selected_broll,
        };
        try {
            await renderProject(id, opts);
            toast.success("Render started");
            refresh();
        } catch (e) {
            toast.error(apiErrorMessage(e, "Render failed"));
        } finally { setRenderStarting(false); }
    };

    if (!project) {
        return (
            <div className="min-h-[70vh] flex items-center justify-center text-white/40" data-testid="editor-loading">
                <Loader2 className="w-6 h-6 animate-spin" />
            </div>
        );
    }

    const inProgress = IN_PROGRESS.has(project.status);
    const isReady = project.status === "ready" || project.status === "done" || (project.status === "error" && Boolean(project.transcript));
    // A short-form clip render also marks the project "done". Only switch the
    // main player to the final endpoint when a main output actually exists.
    const hasMainOutput = Boolean(project.output_path);

    const jumpToSettings = () => {
        settingsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        setShowSettingsCue(false);
        try { window.sessionStorage.removeItem(`klippd_settings_cue_${id}`); }
        catch { /* The cue is non-essential. */ }
    };

    const shouldShowSettingsCue = isReady && showSettingsCue;

    return (
        <div className="min-h-[calc(100vh-72px)] px-4 md:px-8 py-8" data-testid="editor-page">
            <div className="flex items-start justify-between mb-6 gap-4">
                <button onClick={() => navigate("/")} className="btn-ghost !px-3 sm:!px-6" data-testid="back-btn">
                    <ArrowLeft className="w-4 h-4" /> Back
                </button>
                <div className="text-right">
                    <div className="font-display text-xl sm:text-2xl md:text-3xl tracking-wider line-clamp-2 max-w-md">
                        {analysis.title || project.name}
                    </div>
                    <div className="font-mono text-xs text-white/40 mt-1">
                        {Math.round(project.duration)}s · {project.width}x{project.height} ·
                        <span className={`ml-2 ${project.status === "error" ? "text-[#ff3333]" : "text-[#ccff00]"}`}>
                            {STATUS_LABELS[project.status] || project.status?.toUpperCase()}
                        </span>
                    </div>
                </div>
            </div>

            {inProgress && (
                <div className="panel p-12 mb-6 trace-border" data-testid="processing-panel">
                    <div className="flex items-center gap-6">
                        <Loader2 className="w-10 h-10 text-[#ccff00] animate-spin flex-shrink-0" />
                        <div className="flex-1">
                            <div className="font-display text-4xl md:text-5xl tracking-wider text-[#ccff00]">
                                {STATUS_LABELS[project.status]}
                            </div>
                            <div className="text-white/60 text-sm mt-2">{project.status_message}</div>
                            <div className="h-1 bg-white/10 mt-4">
                                <div
                                    className="h-1 bg-[#ccff00] transition-all"
                                    style={{ width: `${project.progress || 0}%` }}
                                />
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {project.status === "error" && (
                <div className="panel p-6 mb-6" style={{ borderColor: "rgba(255,51,51,0.4)" }} data-testid="error-panel">
                    <div className="font-display text-2xl text-[#ff3333] flex items-center gap-2"><AlertTriangle className="w-5 h-5" /> EDIT STOPPED</div>
                    <div className="text-white/70 text-sm mt-2">{project.status_message}</div>
                    <button className="btn-ghost mt-4" onClick={() => {
                        const retry = project.transcript ? startRender() : analyzeProject(id).then(refresh);
                        Promise.resolve(retry).catch((error) => toast.error(apiErrorMessage(error, "Retry failed")));
                    }} data-testid="retry-btn">
                        <RefreshCw className="w-4 h-4" /> {project.transcript ? "Retry Final Render" : "Retry Analysis"}
                    </button>
                </div>
            )}

            {shouldShowSettingsCue && (
                <button
                    type="button"
                    onClick={jumpToSettings}
                    className="lg:hidden w-full mb-6 border border-[#ccff00] bg-[#ccff00]/10 px-5 py-4 text-left flex items-center justify-between gap-4"
                    data-testid="settings-cue"
                >
                    <span>
                        <span className="block font-heading text-2xl tracking-wider text-[#ccff00]">YOUR EDIT IS READY</span>
                        <span className="block mt-1 text-sm text-white/70">Choose your format, style, and edit options below.</span>
                    </span>
                    <ArrowDown className="w-7 h-7 shrink-0 text-[#ccff00] animate-bounce" aria-hidden="true" />
                </button>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 space-y-6">
                    <div className="panel">
                        <video
                            ref={videoRef}
                            src={hasMainOutput ? mediaOutput(project.id) : mediaOriginal(project.id)}
                            controls
                            className="w-full aspect-video bg-black"
                            onTimeUpdate={(e) => setCurrentTime(e.target.currentTime)}
                            data-testid="video-player"
                        />
                        {hasMainOutput && (
                            <div className="p-4 flex items-center justify-between border-t border-white/10">
                                <div className="font-mono text-xs text-[#ccff00] tracking-widest">
                                    ✓ EDITED VERSION LOADED
                                </div>
                                <a
                                    href={downloadUrl(project.id)}
                                    className="btn-brand"
                                    data-testid="download-btn"
                                    download
                                >
                                    <Download className="w-4 h-4" /> Download MP4
                                </a>
                            </div>
                        )}
                    </div>

                    {isReady && (
                        <EditChatPanel
                            projectId={id}
                            creatorProfileId={creatorProfileId}
                            onApplied={refresh}
                        />
                    )}

                    {isReady && words.length > 0 && (
                        <div className="panel">
                            <div className="px-6 py-4 border-b border-white/10 flex items-center justify-between">
                                <div>
                                    <div className="font-display text-xl tracking-wider">TRANSCRIPT</div>
                                    <div className="font-mono text-[10px] text-white/40 tracking-widest">
                                        {words.length} WORDS · {effectiveFillers.size} FLAGGED FOR CUT · CLICK WORDS TO TOGGLE
                                    </div>
                                </div>
                                <div className="font-mono text-xs text-white/50">
                                    {Math.round(currentTime)}s
                                </div>
                            </div>
                            <div
                                ref={transcriptRef}
                                className="p-6 max-h-[420px] overflow-y-auto text-lg md:text-xl leading-relaxed"
                                data-testid="transcript-area"
                            >
                                {words.map((w, i) => {
                                    const isFiller = effectiveFillers.has(i);
                                    const isEmph = emphasisSet.has(i);
                                    const isActive = i === activeIdx;
                                    let cls = "word-clickable ";
                                    if (isFiller) cls += "word-filler ";
                                    if (isEmph && !isFiller) cls += "word-emphasis ";
                                    if (isActive) cls += "word-active ";
                                    return (
                                        <span
                                            key={i}
                                            id={`w-${i}`}
                                            className={cls}
                                            onClick={() => toggleFiller(i)}
                                            onDoubleClick={() => jumpTo(i)}
                                            title={`${w.start?.toFixed(2)}s · click to toggle cut · dbl-click to jump`}
                                        >
                                            {w.word}{" "}
                                        </span>
                                    );
                                })}
                            </div>
                        </div>
                    )}
                </div>

                {isReady && (
                <aside ref={settingsRef} className="space-y-6 scroll-mt-24" data-testid="editor-sidebar">
                    <CreatorProfilesPanel
                        selectedProfileId={creatorProfileId}
                        onSelectProfile={setCreatorProfileId}
                    />

                    <div className="panel p-6">
                        <div className="font-mono text-xs text-white/40 tracking-widest mb-3">// ASPECT</div>
                        <div className="grid grid-cols-3 gap-2">
                            {[
                                { val: "16:9", label: "16:9", Icon: Monitor, sub: "YouTube" },
                                { val: "9:16", label: "9:16", Icon: Smartphone, sub: "TikTok" },
                                { val: "1:1", label: "1:1", Icon: Square, sub: "Feed" },
                            ].map(({ val, label, Icon, sub }) => (
                                <button
                                    key={val}
                                    onClick={() => setAspect(val)}
                                    data-testid={`aspect-${val.replace(":", "-")}`}
                                    className="style-pill flex-col !py-3"
                                    style={{
                                        background: aspect === val ? "#CCFF00" : "transparent",
                                        color: aspect === val ? "#000" : "rgba(255,255,255,0.6)",
                                        borderColor: aspect === val ? "#CCFF00" : "rgba(255,255,255,0.1)",
                                    }}
                                >
                                    <Icon className="w-4 h-4 mb-1" />
                                    <span className="text-xs">{label}</span>
                                    <span className="text-[9px] opacity-70 font-mono">{sub}</span>
                                </button>
                            ))}
                        </div>
                    </div>

                    <div className="panel p-6">
                        <div className="font-mono text-xs text-white/40 tracking-widest mb-3">// STYLE</div>
                        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                            <button
                                className={`style-pill ${style === "tiktok" ? "active-tiktok" : ""}`}
                                onClick={() => setStyle("tiktok")}
                                data-testid="style-tiktok"
                            >
                                TIKTOK
                            </button>
                            <button
                                className={`style-pill ${style === "youtube" ? "active-youtube" : ""}`}
                                onClick={() => setStyle("youtube")}
                                data-testid="style-youtube"
                            >
                                YOUTUBE
                            </button>
                            <button
                                className={`style-pill col-span-2 sm:col-span-1 ${style === "luxury" ? "active-luxury" : ""}`}
                                onClick={() => setStyle("luxury")}
                                data-testid="style-luxury"
                            >
                                LUXURY
                            </button>
                        </div>
                        <div className="text-xs text-white/50 mt-3">
                            {style === "tiktok"
                                ? "Bold Impact font. Pink emphasis. Bounce animations. Aggressive."
                                : style === "luxury"
                                ? "Editorial white captions. Gold keywords. Smooth slide-in motion."
                                : "Clean Arial. Yellow emphasis. Subtle. Studio-look."}
                        </div>
                    </div>

                    <div className="panel p-6 space-y-4">
                        <div className="font-mono text-xs text-white/40 tracking-widest">// FEATURES</div>
                        <Toggle
                            icon={Scissors}
                            label="Cut fillers"
                            sub={`${effectiveFillers.size} words flagged`}
                            checked={renderOpts.remove_fillers}
                            onChange={(v) => setRenderOpts((o) => ({ ...o, remove_fillers: v }))}
                            testid="toggle-fillers"
                        />
                        <Toggle
                            icon={Wand2}
                            label="Animated captions"
                            sub="Word-by-word pop"
                            checked={renderOpts.captions}
                            onChange={(v) => setRenderOpts((o) => ({ ...o, captions: v }))}
                            testid="toggle-captions"
                        />
                        <Toggle
                            icon={Zap}
                            label="Emphasis zoom"
                            sub={`${emphasisSet.size} moments`}
                            checked={renderOpts.zoom_ins}
                            onChange={(v) => setRenderOpts((o) => ({ ...o, zoom_ins: v }))}
                            testid="toggle-zoom"
                        />
                        <Toggle
                            icon={Volume2}
                            label="SFX (whoosh on cuts)"
                            sub="Auto-mixed"
                            checked={renderOpts.sfx}
                            onChange={(v) => setRenderOpts((o) => ({ ...o, sfx: v }))}
                            testid="toggle-sfx"
                        />
                        <Toggle
                            icon={Film}
                            label="B-roll overlays"
                            sub={`${Object.values(brollSelected).filter(Boolean).length} selected`}
                            checked={renderOpts.broll}
                            onChange={(v) => setRenderOpts((o) => ({ ...o, broll: v }))}
                            testid="toggle-broll"
                        />
                    </div>

                    <button
                        onClick={startRender}
                        disabled={renderStarting || project.status === "rendering"}
                        className="btn-brand w-full !justify-center text-lg"
                        data-testid="render-btn"
                    >
                        {renderStarting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-5 h-5" />}
                        {hasMainOutput ? "Re-Render" : "Render Final"}
                    </button>

                    <div className="text-center font-mono text-[10px] text-white/30 tracking-widest">
                        KLIPPED STUDIO · AI-GUIDED EDITING
                    </div>
                </aside>
                )}
            </div>

            {isReady && brollMoments.length > 0 && renderOpts.broll && (
                <section className="mt-8" data-testid="broll-section">
                    <div className="font-mono text-xs text-white/40 tracking-widest mb-2">// B-ROLL SUGGESTIONS</div>
                    <div className="font-display text-3xl tracking-wider mb-6">DROP-INS</div>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {brollMoments.map((m, idx) => {
                            const results = brollByMoment[idx] || [];
                            const customs = customBrollByMoment[idx] || [];
                            const generated = generatedAssets.filter((asset) => asset.word_index === m.word_index);
                            const matchedPack = resolvedPackAssets.filter((asset) => asset.word_index === m.word_index);
                            const combined = [...matchedPack, ...generated, ...customs, ...results];
                            const selected = brollSelected[m.word_index];
                            const isUploading = uploadingBrollIdx === idx;
                            return (
                                <div key={idx} className="panel p-5" data-testid={`broll-moment-${idx}`}>
                                    <div className="font-mono text-[10px] text-white/40 tracking-widest">
                                        @ WORD #{m.word_index}
                                    </div>
                                    <div className="font-display text-xl tracking-wider mt-1">
                                        &quot;{(m.query || "").toUpperCase()}&quot;
                                    </div>
                                    <div className="text-white/50 text-xs mt-1">{m.reason}</div>
                                    <div className="flex flex-wrap gap-2 mt-3">
                                        <button
                                            onClick={() => searchBrollForMoment(idx, m.query)}
                                            disabled={searchingIdx === idx}
                                            className="btn-ghost !text-xs !py-1.5"
                                            data-testid={`broll-search-${idx}`}
                                        >
                                            {searchingIdx === idx ? (
                                                <Loader2 className="w-3 h-3 animate-spin" />
                                            ) : (
                                                <Search className="w-3 h-3" />
                                            )}
                                            {results.length ? "Refresh Pack" : "Approved Pack"}
                                        </button>
                                        <label
                                            className="btn-ghost !text-xs !py-1.5 cursor-pointer"
                                            data-testid={`broll-upload-${idx}`}
                                        >
                                            {isUploading ? (
                                                <Loader2 className="w-3 h-3 animate-spin" />
                                            ) : (
                                                <Upload className="w-3 h-3" />
                                            )}
                                            Upload yours
                                            <input
                                                type="file"
                                                accept="video/*,.mp4,.mov,.mkv,.webm"
                                                className="hidden"
                                                onChange={(e) => {
                                                    const f = e.target.files?.[0];
                                                    if (f) uploadCustomBrollForMoment(idx, f);
                                                    e.target.value = "";
                                                }}
                                            />
                                        </label>
                                    </div>
                                    {combined.length > 0 && (
                                        <div className="grid grid-cols-2 gap-2 mt-3">
                                            {combined.slice(0, 6).map((r) => {
                                                const active = selected?.id === r.id;
                                                const isCustom = r.is_custom;
                                                const isGenerated = r.generated;
                                                return (
                                                    <div
                                                        key={r.id}
                                                        className="relative cursor-pointer border"
                                                        style={{
                                                            borderColor: active ? "#CCFF00" : "rgba(255,255,255,0.1)",
                                                        }}
                                                        onClick={() =>
                                                            setBrollSelected((s) => ({
                                                                ...s,
                                                                [m.word_index]: active ? undefined : { ...r, word_index: m.word_index },
                                                            }))
                                                        }
                                                        data-testid={`broll-clip-${idx}-${r.id}`}
                                                    >
                                                        {r.thumbnail ? (
                                                            <img
                                                                src={previewUrl(r.thumbnail)}
                                                                alt=""
                                                                className="w-full h-20 object-cover"
                                                            />
                                                        ) : (
                                                            <div className="w-full h-20 bg-[#111] flex items-center justify-center">
                                                                <Film className="w-6 h-6 text-white/30" />
                                                            </div>
                                                        )}
                                                        {(isCustom || isGenerated) && (
                                                            <div className="absolute top-1 left-1 bg-[#CCFF00] text-black text-[9px] font-mono px-1.5">
                                                                {isGenerated ? "AI MADE" : "YOURS"}
                                                            </div>
                                                        )}
                                                        {active && (
                                                            <div className="absolute inset-0 bg-[#ccff00]/20 flex items-center justify-center">
                                                                <Check className="w-6 h-6 text-[#ccff00]" strokeWidth={3} />
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    )}
                                    {combined.length === 0 && (
                                        <div className="mt-3 border border-dashed border-white/10 p-4 text-center text-xs text-white/40">
                                            Search stock, upload your own, or use an AI-made graphic when one is available.
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </section>
            )}

            {/* VIRAL CLIPS SECTION */}
            {isReady && (
                <LibraryPanel
                    activeSelection={libraryPick}
                    niche={analysis?.quality_review?.profile || analysis?.profile || "gaming"}
                    onPickAsset={(asset) => {
                        // If B-roll moments exist, assign to the first unassigned one
                        if (brollMoments.length > 0) {
                            const firstFree = brollMoments.findIndex((moment) => !brollSelected[moment.word_index]);
                            const idx = firstFree === -1 ? 0 : firstFree;
                            const wordIndex = brollMoments[idx].word_index;
                            setBrollSelected((s) => ({ ...s, [wordIndex]: { ...asset, word_index: wordIndex } }));
                            setLibraryPick(asset);
                            toast.success(`Assigned to moment #${idx + 1}${firstFree === -1 ? " (replaced)" : ""}`);
                        } else {
                            setLibraryPick(libraryPick?.id === asset.id ? null : asset);
                        }
                    }}
                />
            )}

            {isReady && (
                <section className="mt-12" data-testid="viral-section">
                    <div className="flex items-end justify-between mb-6 flex-wrap gap-3">
                        <div>
                            <div className="font-mono text-xs text-white/40 tracking-widest mb-2">// AI HIGHLIGHT REEL</div>
                            <div className="font-display text-3xl tracking-wider">VIRAL CLIPS</div>
                            <div className="text-white/50 text-sm mt-1">
                                AI finds the punchiest 20-60s moments and cuts them as 9:16 shorts
                            </div>
                        </div>
                        <button
                            onClick={extractClips}
                            disabled={extractingClips}
                            className="btn-brand"
                            data-testid="extract-clips-btn"
                        >
                            {extractingClips ? (
                                <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                                <Flame className="w-4 h-4" />
                            )}
                            {viralClips.length ? "Re-extract" : "Find Viral Clips"}
                        </button>
                    </div>

                    {viralClips.length > 0 && (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                            {viralClips.map((c, idx) => {
                                const label = `clip_${idx + 1}_${Math.round(c.start)}s`;
                                const rendered = (project.viral_renders || {})[label];
                                const isRendering = renderingClipLabel === label;
                                return (
                                    <div key={idx} className="panel p-5" data-testid={`viral-clip-${idx}`}>
                                        <div className="flex items-start justify-between gap-2">
                                            <div className="font-mono text-[10px] text-white/40 tracking-widest">
                                                #{idx + 1} · {c.start}s → {c.end}s ({c.duration}s)
                                            </div>
                                            <div
                                                className="font-mono text-xs px-1.5 py-0.5"
                                                style={{
                                                    background: c.score >= 80 ? "#CCFF00"
                                                        : c.score >= 60 ? "rgba(204,255,0,0.3)"
                                                        : "rgba(255,255,255,0.1)",
                                                    color: c.score >= 80 ? "#000" : "#fff",
                                                }}
                                            >
                                                {c.score}
                                            </div>
                                        </div>
                                        <div className="font-display text-lg tracking-wider mt-2 leading-tight">
                                            &quot;{c.hook}&quot;
                                        </div>
                                        <div className="text-white/70 text-sm mt-2">{c.caption}</div>
                                        <div className="text-white/40 text-xs mt-1 italic">{c.reason}</div>

                                        {rendered ? (
                                            <div className="mt-4 space-y-2">
                                                <video
                                                    src={mediaClip(id, label)}
                                                    controls
                                                    className="w-full bg-black"
                                                    style={{ aspectRatio: "9/16", maxHeight: 400 }}
                                                    data-testid={`viral-video-${idx}`}
                                                />
                                                <a
                                                    href={downloadUrl(id, label)}
                                                    download
                                                    className="btn-brand w-full !justify-center !text-xs !py-2"
                                                    data-testid={`viral-download-${idx}`}
                                                >
                                                    <Download className="w-3 h-3" /> Download Short
                                                </a>
                                            </div>
                                        ) : (
                                            <button
                                                onClick={() => renderViralClip(c, idx)}
                                                disabled={isRendering}
                                                className="btn-ghost w-full !justify-center mt-4"
                                                data-testid={`viral-render-${idx}`}
                                            >
                                                {isRendering ? (
                                                    <Loader2 className="w-3 h-3 animate-spin" />
                                                ) : (
                                                    <Zap className="w-3 h-3" />
                                                )}
                                                Render as 9:16 Short
                                            </button>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    {!viralClips.length && !extractingClips && (
                        <div className="panel p-8 text-center text-white/40 font-mono text-sm">
                            Click &quot;Find Viral Clips&quot; to have the AI extract the most punchy moments.
                        </div>
                    )}
                </section>
            )}
        </div>
    );
}

function Toggle({ icon: Icon, label, sub, checked, onChange, testid }) {
    return (
        <label
            className="flex items-center gap-3 cursor-pointer group"
            data-testid={testid}
        >
            <div
                className="w-10 h-5 relative flex-shrink-0 border"
                style={{
                    background: checked ? "#CCFF00" : "transparent",
                    borderColor: checked ? "#CCFF00" : "rgba(255,255,255,0.25)",
                }}
            >
                <div
                    className="absolute top-0.5 w-4 h-4 transition-all"
                    style={{
                        left: checked ? "calc(100% - 1.125rem)" : "0.125rem",
                        background: checked ? "#000" : "rgba(255,255,255,0.5)",
                    }}
                />
                <input type="checkbox" className="opacity-0 absolute inset-0"
                    checked={checked} onChange={(e) => onChange(e.target.checked)} />
            </div>
            <Icon className={`w-4 h-4 ${checked ? "text-[#ccff00]" : "text-white/40"} flex-shrink-0`} />
            <div className="flex-1 min-w-0">
                <div className={`text-sm font-semibold ${checked ? "text-white" : "text-white/60"}`}>
                    {label}
                </div>
                <div className="text-[10px] font-mono text-white/40 tracking-wider truncate">{sub}</div>
            </div>
        </label>
    );
}
