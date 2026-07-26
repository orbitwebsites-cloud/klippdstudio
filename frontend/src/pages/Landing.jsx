import { useEffect, useState, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
    Upload,
    Play,
    Trash2,
    Clock,
    Zap,
    Scissors,
    Wand2,
    Film,
    Sparkles,
    Volume2,
    WifiOff,
    RotateCcw,
} from "lucide-react";
import {
    listProjects,
    uploadVideo,
    deleteProject,
    analyzeProject,
    apiErrorMessage,
} from "@/lib/klipApi";
import { useSignedIn } from "@/hooks/useSignedIn";

const FEATURES = [
    { icon: Scissors, title: "Kill filler words", copy: "Um. Uh. Stutters. All gone." },
    { icon: Wand2, title: "Animated captions", copy: "Word-by-word, TikTok or YouTube styled." },
    { icon: Film, title: "Auto B-roll", copy: "AI picks moments, finds stock clips or uses your own library." },
    { icon: Zap, title: "Zoom + SFX", copy: "Impact zooms on hooks, whoosh on cuts." },
];

const STATUS_LABELS = {
    uploaded: "UPLOADED",
    queued: "QUEUED",
    probing: "READING",
    extracting_audio: "EXTRACTING AUDIO",
    transcribing: "TRANSCRIBING",
    analyzing: "ANALYZING",
    ready: "READY",
    queued_render: "QUEUED",
    rendering: "RENDERING",
    done: "DONE",
    error: "ERROR",
};

export default function Landing({ backendOnline }) {
    const navigate = useNavigate();
    const [projects, setProjects] = useState([]);
    const [uploading, setUploading] = useState(false);
    const [uploadProgress, setUploadProgress] = useState(0);
    const [dragOver, setDragOver] = useState(false);
    const [uploadName, setUploadName] = useState("");
    const [projectsError, setProjectsError] = useState(false);
    const inputRef = useRef();

    const { signedIn } = useSignedIn();

    const refresh = useCallback(() => {
        if (!signedIn) return;
        listProjects()
            .then((items) => {
                setProjects(items);
                setProjectsError(false);
            })
            .catch(() => setProjectsError(true));
    }, [signedIn]);

    useEffect(() => {
        if (!signedIn) {
            setProjects([]);
            return undefined;
        }
        refresh();
        // Poll to update processing statuses
        const t = setInterval(refresh, 4000);
        return () => clearInterval(t);
    }, [refresh, signedIn]);

    const handleFile = async (file) => {
        if (!file) return;
        if (!signedIn) {
            toast.error("Please sign in to upload and edit videos.");
            return;
        }
        if (backendOnline === false) {
            toast.error("The Klipped Studio server is offline. Try again once it is connected.");
            return;
        }
        const validExt = /\.(mp4|mov|mkv|webm|m4v|avi|mpeg|mpg|qt)$/i;
        const isVideo = (file.type && file.type.startsWith("video/")) || validExt.test(file.name);
        if (!isVideo) {
            toast.error(`Not a supported video: ${file.name}${file.type ? ` (${file.type})` : ""}`);
            return;
        }
        setUploading(true);
        setUploadName(file.name);
        setUploadProgress(0);
        try {
            const proj = await uploadVideo(file, setUploadProgress);
            toast.success("Uploaded — kicking off analysis");
            // Auto-trigger analyze
            await analyzeProject(proj.id).catch((e) => {
                toast.warning(`Uploaded, but analysis has not started: ${apiErrorMessage(e, "try again in the project")}`);
            });
            refresh();
            try { window.sessionStorage.setItem(`klippd_settings_cue_${proj.id}`, "1"); }
            catch { /* The editor still works when browser storage is blocked. */ }
            navigate(`/project/${proj.id}`, { state: { newUpload: true } });
        } catch (e) {
            console.error("upload error", e);
            toast.error(apiErrorMessage(e, "Upload failed"));
        } finally {
            setUploading(false);
            setUploadProgress(0);
            setUploadName("");
        }
    };

    const onDrop = (e) => {
        e.preventDefault();
        setDragOver(false);
        const f = e.dataTransfer.files?.[0];
        handleFile(f);
    };

    const handleDelete = async (id, name) => {
        if (!window.confirm(`Delete "${name}"? This can't be undone.`)) return;
        try {
            await deleteProject(id);
            toast.success("Deleted");
            refresh();
        } catch (e) {
            toast.error("Delete failed");
        }
    };

    return (
        <div className="min-h-[calc(100vh-72px)]" data-testid="landing-page">
            {/* Marquee band */}
            <div className="overflow-hidden border-b border-white/10 py-2">
                <div className="marquee">
                    <div className="marquee-track">
                        FREE·EDITS·NO·CAP · KILL·THE·UMS · CAPTIONS·POP · BROLL·AUTO ·
                        FREE·EDITS·NO·CAP · KILL·THE·UMS · CAPTIONS·POP · BROLL·AUTO ·
                    </div>
                </div>
            </div>

            {/* Hero */}
            <section className="px-6 md:px-16 pt-16 pb-24 relative">
                <div className="max-w-6xl">
                    <div className="font-mono text-xs text-[#ccff00] tracking-widest mb-6 flex items-center gap-2">
                        <span className="w-2 h-2 bg-[#ccff00] pulse-brand" />
                        AI-GUIDED EDITING · CAPTIONS · B-ROLL · SMART CUTS
                    </div>
                    <h1 className="font-heading text-6xl md:text-[8rem] leading-[0.9] tracking-tight">
                        DROP<br />THE VID.<br />
                        <span className="text-[#ccff00]">GET</span> A CINEMA<span className="text-[#ff0050]">.</span>
                    </h1>
                    <p className="mt-8 text-lg md:text-xl text-white/70 max-w-2xl font-light">
                        Upload your raw take. AI kills every <span className="text-[#ff3333] line-through">um and uh</span>,
                        drops animated captions, auto-fetches B-roll, and adds zooms & SFX
                        that hit — like a $3K editor did it. But you did it in a click.
                    </p>

                    {/* Upload zone */}
                    <div
                        onDragOver={(e) => {
                            e.preventDefault();
                            setDragOver(true);
                        }}
                        onDragLeave={() => setDragOver(false)}
                        onDrop={onDrop}
                        onClick={() => !uploading && inputRef.current?.click()}
                        onKeyDown={(e) => {
                            if (!uploading && (e.key === "Enter" || e.key === " ")) inputRef.current?.click();
                        }}
                        role="button"
                        tabIndex={0}
                        className={`mt-12 border-2 ${
                            dragOver ? "border-[#ccff00] bg-[#ccff00]/5" : "border-dashed border-white/25"
                        } p-12 md:p-16 cursor-pointer transition-colors ${
                            uploading ? "pointer-events-none" : ""
                        }`}
                        data-testid="upload-dropzone"
                    >
                        <input
                            ref={inputRef}
                            type="file"
                            accept="video/*"
                            className="hidden"
                            onChange={(e) => handleFile(e.target.files?.[0])}
                            data-testid="upload-file-input"
                        />
                        {uploading ? (
                            <div className="text-center">
                                <div className="font-heading text-4xl md:text-6xl text-[#ccff00] mb-4">
                                    UPLOADING {uploadProgress}%
                                </div>
                                <div className="h-1 bg-white/10 max-w-md mx-auto">
                                    <div
                                        className="h-1 bg-[#ccff00] transition-all"
                                        style={{ width: `${uploadProgress}%` }}
                                    />
                                </div>
                                <div className="text-white/50 text-xs font-mono mt-4 tracking-widest">
                                    {uploadName} · SAFE TO RETRY IF YOUR CONNECTION DROPS
                                </div>
                            </div>
                        ) : (
                            <div className="text-center">
                                <Upload className="w-14 h-14 mx-auto text-[#ccff00] mb-4" />
                                <div className="font-heading text-4xl md:text-5xl tracking-wider">
                                    DROP A VIDEO
                                </div>
                                <div className="text-white/50 mt-3 text-sm">
                                    or click — MP4, MOV, WEBM. Long-form or short-form. Up to 30min.
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            </section>

            {/* Features bento */}
            <section className="px-6 md:px-16 pb-16 border-t border-white/10 pt-16">
                <div className="font-mono text-xs text-white/40 tracking-widest mb-6">
                    // WHAT IT DOES
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                    {FEATURES.map((f, i) => {
                        const Icon = f.icon;
                        return (
                            <div
                                key={i}
                                className="panel p-8 hover:border-[#ccff00]/40 transition-colors"
                                data-testid={`feature-${i}`}
                            >
                                <Icon className="w-8 h-8 text-[#ccff00] mb-6" />
                                <div className="font-heading text-2xl tracking-wider mb-2">
                                    {f.title}
                                </div>
                                <div className="text-white/60 text-sm">{f.copy}</div>
                            </div>
                        );
                    })}
                </div>
            </section>

            {/* Projects list */}
            <section className="px-6 md:px-16 pb-24 pt-8">
                <div className="flex items-baseline justify-between mb-6">
                    <div>
                        <div className="font-mono text-xs text-white/40 tracking-widest mb-2">
                            // YOUR PROJECTS
                        </div>
                        <h2 className="font-heading text-4xl tracking-wider">
                            THE VAULT
                        </h2>
                    </div>
                    <div className="text-white/50 text-sm font-mono">
                        {projects.length} CLIP{projects.length !== 1 ? "S" : ""}
                    </div>
                </div>
                {projects.length === 0 ? (
                    projectsError ? (
                    <div className="panel p-12 text-center text-white/50" role="alert">
                        <WifiOff className="w-10 h-10 mx-auto mb-3 text-[#ff5a5a]" />
                        <div className="font-heading text-2xl tracking-wider text-white/80">COULDN'T LOAD PROJECTS</div>
                        <button className="btn-ghost mt-4" onClick={refresh}><RotateCcw className="w-4 h-4" /> Try again</button>
                    </div>
                    ) : (
                    <div className="panel p-16 text-center text-white/40" data-testid="empty-projects">
                        <Film className="w-12 h-12 mx-auto mb-3 opacity-40" />
                        <div className="font-heading text-2xl tracking-wider text-white/60">
                            NOTHING HERE YET
                        </div>
                        <div className="text-sm mt-2">Drop your first clip above.</div>
                    </div>
                    )
                ) : (
                    <div
                        className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"
                        data-testid="projects-grid"
                    >
                        {projects.map((p) => (
                            <ProjectCard
                                key={p.id}
                                project={p}
                                onOpen={() => navigate(`/project/${p.id}`)}
                                onDelete={() => handleDelete(p.id, p.name)}
                            />
                        ))}
                    </div>
                )}
            </section>
        </div>
    );
}

function ProjectCard({ project, onOpen, onDelete }) {
    const status = project.status || "queued";
    const isProcessing = ["queued", "uploaded", "probing", "extracting_audio", "transcribing", "analyzing", "queued_render", "rendering"].includes(status);
    const label = STATUS_LABELS[status] || status.toUpperCase();
    const statusColor =
        status === "done" || status === "ready"
            ? "text-[#ccff00]"
            : status === "error"
            ? "text-[#ff3333]"
            : "text-[#00ffff]";
    return (
        <div
            className={`panel group relative overflow-hidden ${
                isProcessing ? "trace-border" : ""
            }`}
            data-testid={`project-card-${project.id}`}
        >
            <div
                className="aspect-video bg-black relative cursor-pointer flex items-center justify-center"
                onClick={onOpen}
            >
                <div className="text-white/20">
                    <Film className="w-16 h-16" strokeWidth={1} />
                </div>
                <div className="absolute top-3 left-3 flex items-center gap-2 font-mono text-[10px] tracking-widest">
                    <span className={`px-2 py-1 bg-black/70 ${statusColor}`}>
                        {isProcessing && (
                            <span className="inline-block w-1.5 h-1.5 bg-current rounded-full mr-1.5 pulse-brand" />
                        )}
                        {label}
                    </span>
                    {isProcessing && project.progress > 0 && (
                        <span className="px-2 py-1 bg-black/70 text-white/80">
                            {project.progress}%
                        </span>
                    )}
                </div>
                <button
                    onClick={onOpen}
                    className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black/40"
                >
                    <Play className="w-12 h-12 text-[#ccff00]" strokeWidth={2} />
                </button>
            </div>
            <div className="p-4 flex items-start justify-between">
                <div className="flex-1 min-w-0">
                    <div
                        className="font-heading text-lg tracking-wider truncate cursor-pointer hover:text-[#ccff00]"
                        onClick={onOpen}
                    >
                        {project.name}
                    </div>
                    <div className="text-white/40 text-xs font-mono mt-1 flex items-center gap-2">
                        <Clock className="w-3 h-3" />
                        {project.duration ? `${Math.round(project.duration)}s` : "—"}
                    </div>
                </div>
                <button
                    onClick={onDelete}
                    className="text-white/30 hover:text-[#ff3333] transition"
                    data-testid={`delete-project-${project.id}`}
                >
                    <Trash2 className="w-4 h-4" />
                </button>
            </div>
        </div>
    );
}
