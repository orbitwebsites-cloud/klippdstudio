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
    AudioLines,
    Music,
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
import EditorialTeamPanel from "@/components/EditorialTeamPanel";
import EditorTimeline from "@/components/EditorTimeline";
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
    uploadMusic,
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
        remove_fillers: true, remove_silences: false, silence_threshold: 0.8, captions: true, sfx: true, zoom_ins: true, broll: true, background_music: false, background_music_volume: 0.16,
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
    const [rangeStart, setRangeStart] = useState("");
    const [rangeEnd, setRangeEnd] = useState("");
    const [libraryPick, setLibraryPick] = useState(null);
    const [libraryTargetMoment, setLibraryTargetMoment] = useState("");
    const [creatorProfileId, setCreatorProfileId] = useState(null);
    const [draftLoadedFor, setDraftLoadedFor] = useState(null);
    const [showSettingsCue, setShowSettingsCue] = useState(false);
    const [musicUploading, setMusicUploading] = useState(false);
    const [musicName, setMusicName] = useState("");
    const [teamPrompt, setTeamPrompt] = useState("");
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
                ["remove_fillers", "remove_silences", "captions", "sfx", "zoom_ins", "broll", "background_music"]
                    .filter((key) => typeof options[key] === "boolean")
                    .map((key) => [key, options[key]])
            ),
        }));
        if (Number.isFinite(options.silence_threshold)) setRenderOpts((current) => ({ ...current, silence_threshold: Number(options.silence_threshold) }));
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
                ["remove_fillers", "remove_silences", "captions", "sfx", "zoom_ins", "broll", "background_music"]
                    .filter((key) => typeof saved[key] === "boolean")
                    .map((key) => [key, saved[key]])
            ),
        }));
        if (Number.isFinite(saved.silence_threshold)) setRenderOpts((current) => ({ ...current, silence_threshold: Number(saved.silence_threshold) }));
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

    const [transcriptQuery, setTranscriptQuery] = useState("");
    const transcriptMatches = useMemo(() => {
        const query = transcriptQuery.trim().toLowerCase();
        if (!query) return [];
        return words.reduce((matches, word, index) => {
            if (String(word.word || "").toLowerCase().includes(query)) matches.push(index);
            return matches;
        }, []);
    }, [transcriptQuery, words]);

    const cutTranscriptMatches = () => {
        if (!transcriptMatches.length) return;
        setAddedFillers((current) => new Set([...current, ...transcriptMatches]));
        setExcludedFillers((current) => new Set([...current].filter((index) => !transcriptMatches.includes(index))));
        toast.success(`Marked ${transcriptMatches.length} matching word${transcriptMatches.length === 1 ? "" : "s"} for cut`);
    };

    const activeIdx = useMemo(() => {
        if (!words.length) return -1;
        for (let i = 0; i < words.length; i++) {
            const w = words[i];
            if (w.start <= currentTime && currentTime <= w.end + 0.05) return i;
        }
        return -1;
    }, [words, currentTime]);

    const mediaDuration = Number(project.duration || videoRef.current?.duration || 0);
    const currentTimeRef = useRef(currentTime);
    const rangeEndRef = useRef(rangeEnd);
    const mediaDurationRef = useRef(mediaDuration);
    currentTimeRef.current = currentTime;
    rangeEndRef.current = rangeEnd;
    mediaDurationRef.current = mediaDuration;
    const normalizedRange = useMemo(() => {
        const start = Number(rangeStart);
        const end = Number(rangeEnd);
        if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) return null;
        if (mediaDuration > 0 && end > mediaDuration) return null;
        return { start, end };
    }, [rangeStart, rangeEnd, mediaDuration]);

    const setRangePoint = useCallback((point) => {
        const value = Math.max(0, Number(Number(currentTimeRef.current).toFixed(2)));
        if (point === "start") {
            setRangeStart(String(value));
            if (rangeEndRef.current && Number(rangeEndRef.current) <= value) setRangeEnd("");
        } else {
            setRangeEnd(String(value));
        }
    }, []);

    useEffect(() => {
        const onKeyDown = (event) => {
            const tag = event.target?.tagName?.toLowerCase();
            if (tag === "input" || tag === "textarea" || tag === "select" || event.target?.isContentEditable) return;
            if (event.key === " ") {
                event.preventDefault();
                if (videoRef.current?.paused) videoRef.current.play();
                else videoRef.current?.pause();
            } else if (event.key === "ArrowLeft") {
                event.preventDefault();
                if (videoRef.current) videoRef.current.currentTime = Math.max(0, videoRef.current.currentTime - (event.shiftKey ? 1 : 0.1));
            } else if (event.key === "ArrowRight") {
                event.preventDefault();
                if (videoRef.current) videoRef.current.currentTime = Math.min(mediaDurationRef.current || Infinity, videoRef.current.currentTime + (event.shiftKey ? 1 : 0.1));
            } else if (event.key.toLowerCase() === "i") {
                event.preventDefault();
                setRangePoint("start");
            } else if (event.key.toLowerCase() === "o") {
                event.preventDefault();
                setRangePoint("end");
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [setRangePoint]);

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
            ...(normalizedRange ? { clip_start: normalizedRange.start, clip_end: normalizedRange.end, clip_label: `range_${Math.round(normalizedRange.start)}_${Math.round(normalizedRange.end)}s` } : {}),
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

    const addMusic = async (file) => {
        if (!file) return;
        const rightsAttested = window.confirm("Confirm that you own this track or have commercial rights to use it in exported videos.");
        if (!rightsAttested) return;
        setMusicUploading(true);
        try {
            const result = await uploadMusic(id, file, rightsAttested);
            setMusicName(result.name || file.name);
            setRenderOpts((current) => ({ ...current, background_music: true }));
            toast.success("Music bed attached and ducking enabled");
        } catch (error) {
            toast.error(apiErrorMessage(error, "Music upload failed"));
        } finally { setMusicUploading(false); }
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
                                    className="h-1 bg-[#ccff00] transition-[width] duration-200"
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
                        <div className="border-t border-white/10 p-4 space-y-3" data-testid="range-editor">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                                <div>
                                    <div className="font-mono text-xs tracking-widest text-white/55">// RANGE EDIT</div>
                                    <div className="mt-1 text-xs text-white/40">Set an in/out range for a focused export. Space plays; ←/→ steps; I/O set points.</div>
                                </div>
                                <button type="button" className="btn-ghost !px-3 !py-2 text-xs" onClick={() => { setRangeStart(""); setRangeEnd(""); }} disabled={!rangeStart && !rangeEnd} data-testid="clear-range">
                                    Clear range
                                </button>
                            </div>
                            <div className="grid grid-cols-2 sm:grid-cols-[1fr_auto_1fr_auto] gap-2 items-end">
                                <label className="font-mono text-[10px] uppercase text-white/45">
                                    In point (s)
                                    <input type="number" min="0" step="0.01" value={rangeStart} onChange={(event) => setRangeStart(event.target.value)} className="mt-1 w-full bg-black border border-white/15 px-3 py-2 text-sm text-white outline-none focus:border-[#ccff00]" placeholder="0.00" data-testid="range-start" />
                                </label>
                                <button type="button" className="btn-ghost !px-3 !py-2 text-xs" onClick={() => setRangePoint("start")} data-testid="set-range-start">Set at playhead</button>
                                <label className="font-mono text-[10px] uppercase text-white/45">
                                    Out point (s)
                                    <input type="number" min="0" step="0.01" value={rangeEnd} onChange={(event) => setRangeEnd(event.target.value)} className="mt-1 w-full bg-black border border-white/15 px-3 py-2 text-sm text-white outline-none focus:border-[#ccff00]" placeholder={mediaDuration ? mediaDuration.toFixed(2) : "0.00"} data-testid="range-end" />
                                </label>
                                <button type="button" className="btn-ghost !px-3 !py-2 text-xs" onClick={() => setRangePoint("end")} data-testid="set-range-end">Set at playhead</button>
                            </div>
                            {(rangeStart || rangeEnd) && !normalizedRange && <div className="text-xs text-[#ff9b9b]" role="alert">Choose an out point after the in point{mediaDuration ? ` and keep it within ${mediaDuration.toFixed(2)} seconds` : ""}.</div>}
                            {normalizedRange && <div className="text-xs text-[#ccff00]">Focused export: {normalizedRange.start.toFixed(2)}s–{normalizedRange.end.toFixed(2)}s ({(normalizedRange.end - normalizedRange.start).toFixed(2)}s)</div>}
                        </div>
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

                    <EditorTimeline
                        project={project}
                        currentTime={currentTime}
                        onSeek={(time) => {
                            if (videoRef.current) videoRef.current.currentTime = time;
                            setCurrentTime(time);
                        }}
                    />

                    {isReady && (
                        <EditorialTeamPanel
                            projectId={id}
                            onUsePrompt={(prompt) => {
                                setTeamPrompt(prompt);
                                document.querySelector('[data-testid="edit-chat-panel"]')?.scrollIntoView({ behavior: "smooth", block: "start" });
                            }}
                        />
                    )}

                    {isReady && (
                        <EditChatPanel
                            projectId={id}
                            creatorProfileId={creatorProfileId}
                            onApplied={refresh}
                            suggestedPrompt={teamPrompt}
                        />
                    )}

                    {isReady && words.length > 0 && (
                        <div className="panel">
                            <div className="px-6 py-4 border-b border-white/10 flex flex-col gap-3">
                                <div className="flex items-center justify-between">
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
                                <div className="flex flex-col sm:flex-row gap-2">
                                    <label className="relative flex-1">
                                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-white/35" aria-hidden="true" />
                                        <input value={transcriptQuery} onChange={(event) => setTranscriptQuery(event.target.value)} className="w-full bg-black border border-white/15 pl-9 pr-3 py-2 text-sm text-white outline-none focus:border-[#ccff00]" placeholder="Search transcript to find a word or phrase" aria-label="Search transcript" />
                                    </label>
                                    <button type="button" className="btn-ghost !px-3 !py-2 text-xs" onClick={cutTranscriptMatches} disabled={!transcriptMatches.length}>
                                        <Scissors className="w-3 h-3" /> Cut {transcriptMatches.length || "matches"}
                                    </button>
                                </div>
                                {transcriptQuery && <div className="font-mono text-[10px] text-white/40">{transcriptMatches.length} match{transcriptMatches.length === 1 ? "" : "es"} · click a word to toggle it · double-click to preview from that moment</div>}
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
                                        <button
                                            type="button"
                                            key={i}
                                            id={`w-${i}`}
                                            className={`${cls} inline bg-transparent border-0 p-0 text-left`}
                                            onClick={() => toggleFiller(i)}
                                            onDoubleClick={() => jumpTo(i)}
                                            aria-pressed={isFiller}
                                            aria-label={`${isFiller ? "Keep" : "Cut"} word ${w.word} at ${Number(w.start || 0).toFixed(1)} seconds. Double click to jump.`}
                                            title={`${w.start?.toFixed(2)}s · click to toggle cut · dbl-click to jump`}
                                        >
                                            {w.word}{" "}
                                        </button>
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
                            <button
                                className={`style-pill ${style === "marketing" ? "active-marketing" : ""}`}
                                onClick={() => setStyle("marketing")}
                                data-testid="style-marketing"
                            >
                                MARKETING
                            </button>
                            <button
                                className={`style-pill ${style === "editorial" ? "active-editorial" : ""}`}
                                onClick={() => setStyle("editorial")}
                                data-testid="style-editorial"
                            >
                                EDITORIAL
                            </button>
                        </div>
                        <div className="text-xs text-white/50 mt-3">
                            {style === "tiktok"
                                ? "High-contrast captions with one deliberate emphasis color. Fast, legible, and built for vertical viewing."
                                : style === "marketing"
                                ? "Bright, structured captions for hooks, proof, and CTAs. Built for product, education, and growth content."
                                : style === "editorial"
                                ? "Clean serif-led captions with restrained emphasis for premium explainers, real estate, and thoughtful storytelling."
                                : style === "luxury"
                                ? "Editorial white captions. Gold keywords. Smooth slide-in motion."
                                : "Clean sans-serif captions. Yellow emphasis. Subtle motion for a studio look."}
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
                            icon={AudioLines}
                            label="Trim dead air"
                            sub={renderOpts.remove_silences ? `Cuts pauses over ${renderOpts.silence_threshold.toFixed(1)}s` : "Keep natural pauses"}
                            checked={renderOpts.remove_silences}
                            onChange={(v) => setRenderOpts((o) => ({ ...o, remove_silences: v }))}
                            testid="toggle-silences"
                        />
                        {renderOpts.remove_silences && (
                            <label className="block pl-14 -mt-2 text-[10px] font-mono text-white/45">
                                PAUSE THRESHOLD
                                <input type="range" min="0.3" max="2.0" step="0.1" value={renderOpts.silence_threshold} onChange={(event) => setRenderOpts((o) => ({ ...o, silence_threshold: Number(event.target.value) }))} className="w-full accent-[#ccff00] mt-2" aria-label="Pause threshold" />
                                <span className="text-[#ccff00]">{renderOpts.silence_threshold.toFixed(1)}s</span>
                            </label>
                        )}
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
                        <div className="border-t border-white/10 pt-4">
                            <div className="flex items-center gap-3">
                                <Music className={`w-4 h-4 ${renderOpts.background_music ? "text-[#ccff00]" : "text-white/40"}`} />
                                <div className="flex-1 min-w-0">
                                    <div className="text-sm font-semibold text-white">Background music</div>
                                    <div className="text-[10px] font-mono text-white/40 truncate">{musicName || "Attach a licensed music bed"}</div>
                                </div>
                                <label className="btn-ghost !px-2 !py-1.5 text-[10px] cursor-pointer">
                                    {musicUploading ? <Loader2 className="w-3 h-3 animate-spin" /> : "Upload"}
                                    <input type="file" accept="audio/*" className="hidden" disabled={musicUploading} onChange={(event) => { addMusic(event.target.files?.[0]); event.target.value = ""; }} />
                                </label>
                            </div>
                            {musicName && <div className="pl-7 mt-3 space-y-2">
                                <label className="block text-[10px] font-mono text-white/45">MUSIC LEVEL <input type="range" min="0" max="0.35" step="0.01" value={renderOpts.background_music_volume} onChange={(event) => setRenderOpts((o) => ({ ...o, background_music_volume: Number(event.target.value) }))} className="w-full accent-[#ccff00] mt-2" aria-label="Background music level" /><span className="text-[#ccff00]">{Math.round(renderOpts.background_music_volume * 100)}%</span></label>
                                <div className="flex gap-2">
                                    <button type="button" className="btn-ghost !px-2 !py-1.5 text-[10px]" onClick={() => setRenderOpts((current) => ({ ...current, background_music: !current.background_music }))} data-testid="toggle-background-music">{renderOpts.background_music ? "Disable music" : "Enable music"}</button>
                                    <button type="button" className="btn-ghost !px-2 !py-1.5 text-[10px]" onClick={() => { setMusicName(""); setRenderOpts((current) => ({ ...current, background_music: false })); }} data-testid="remove-background-music">Remove</button>
                                </div>
                            </div>}
                        </div>
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
                                                    <button
                                                        type="button"
                                                        key={r.id}
                                                        className="relative cursor-pointer border text-left"
                                                        style={{
                                                            borderColor: active ? "#CCFF00" : "rgba(255,255,255,0.1)",
                                                        }}
                                                        aria-pressed={active}
                                                        aria-label={`${active ? "Remove" : "Select"} B-roll ${r.name || r.id} for ${m.query || "this moment"}`}
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
                                                    </button>
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
                <>
                {brollMoments.length > 0 && (
                    <section className="mt-12 panel p-5" data-testid="library-target-selector">
                        <div className="font-mono text-xs text-white/40 tracking-widest mb-2">// LIBRARY ASSIGNMENT TARGET</div>
                        <label className="block text-sm text-white/60 mb-2" htmlFor="library-broll-target">
                            Choose the exact B-roll moment before assigning a library asset.
                        </label>
                        <select
                            id="library-broll-target"
                            value={libraryTargetMoment}
                            onChange={(event) => setLibraryTargetMoment(event.target.value)}
                            className="w-full bg-black border border-white/20 px-3 py-3 text-sm text-white font-mono focus:border-[#ccff00] outline-none"
                        >
                            <option value="">Select a moment...</option>
                            {brollMoments.map((moment, index) => (
                                <option key={`${moment.word_index}-${index}`} value={String(moment.word_index)}>
                                    Moment #{index + 1}: {moment.query || `word ${moment.word_index}`}
                                </option>
                            ))}
                        </select>
                    </section>
                )}
                <LibraryPanel
                    activeSelection={libraryPick}
                    niche={analysis?.quality_review?.profile || analysis?.profile || "gaming"}
                    onPickAsset={(asset) => {
                        if (brollMoments.length > 0) {
                            if (!libraryTargetMoment) {
                                toast.error("Choose a B-roll moment before assigning a library asset.");
                                return;
                            }
                            const wordIndex = Number(libraryTargetMoment);
                            const idx = brollMoments.findIndex((moment) => Number(moment.word_index) === wordIndex);
                            setBrollSelected((s) => ({ ...s, [wordIndex]: { ...asset, word_index: wordIndex } }));
                            setLibraryPick(asset);
                            toast.success(`Assigned to moment #${idx + 1}`);
                        } else {
                            setLibraryPick(libraryPick?.id === asset.id ? null : asset);
                        }
                    }}
                />
                </>
            )}

            {isReady && (
                <section className="mt-12" data-testid="viral-section">
                    <div className="flex items-end justify-between mb-6 flex-wrap gap-3">
                        <div>
                            <div className="font-mono text-xs text-white/40 tracking-widest mb-2">// AI HIGHLIGHT REEL</div>
                            <div className="font-display text-3xl tracking-wider">VIRAL CLIPS</div>
                            <div className="text-white/50 text-sm mt-1">
                                Ranked from story, hook, audio energy, pacing, and clarity signals
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
                                                className="font-mono text-xs px-1.5 py-0.5 whitespace-nowrap"
                                                style={{
                                                    background: c.score >= 80 ? "#CCFF00"
                                                        : c.score >= 60 ? "rgba(204,255,0,0.3)"
                                                        : "rgba(255,255,255,0.1)",
                                                    color: c.score >= 80 ? "#000" : "#fff",
                                                }}
                                            >
                                                {c.score} {c.score_label ? `· ${c.score_label}` : ""}
                                            </div>
                                        </div>
                                        <div className="font-display text-lg tracking-wider mt-2 leading-tight">
                                            &quot;{c.hook}&quot;
                                        </div>
                                        <div className="text-white/70 text-sm mt-2">{c.caption}</div>
                                        <div className="text-white/40 text-xs mt-1 italic">{c.reason}</div>

                                        {c.score_breakdown && (
                                            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mt-4 border-t border-white/10 pt-3">
                                                {Object.entries(c.score_breakdown).map(([name, value]) => (
                                                    <div key={name} className="min-w-0">
                                                        <div className="font-mono text-xs uppercase text-white/45 truncate">{name}</div>
                                                        <div className="font-mono text-sm text-white/80 mt-0.5">{value}</div>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                        {c.score_signals?.length > 0 && (
                                            <div className="mt-3 space-y-1">
                                                {c.score_signals.slice(0, 3).map((signal) => (
                                                    <div key={signal} className="flex gap-2 text-xs text-white/50 leading-snug">
                                                        <Check className="w-3 h-3 mt-0.5 shrink-0 text-[#CCFF00]" />
                                                        <span>{signal}</span>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                        {c.hook_options?.length > 1 && (
                                            <div className="mt-3 text-xs text-white/45">
                                                <span className="font-mono uppercase text-xs tracking-widest">Hook rewrite</span>
                                                <div className="text-white/70 mt-1">{c.hook_options[1]}</div>
                                            </div>
                                        )}

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
                            Find clips to rank moments with both editorial AI and local media signals.
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
                    className="absolute top-0.5 w-4 h-4 transition-[left] duration-150"
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
