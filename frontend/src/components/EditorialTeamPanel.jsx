import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowUpRight, BookOpen, Gauge, Image, Loader2, RefreshCw, ShieldCheck, UserRound } from "lucide-react";
import { apiErrorMessage, getEditVersions, getEditorialTeamReview, restoreEditVersion, saveEditVersion } from "@/lib/klipApi";

const ICONS = { story: BookOpen, rhythm: Gauge, performance: UserRound, visual: Image, finishing: ShieldCheck };

export const layoutTimelineCues = (events = [], duration = 0, minimumGapPercent = 7) => {
    const total = Number(duration) || 0;
    const laneEnds = [];
    const cues = events
        .map((event, sourceIndex) => ({
            ...event,
            sourceIndex,
            left: total > 0 ? Math.max(0, Math.min(100, (Number(event.time || 0) / total) * 100)) : 0,
        }))
        .sort((a, b) => a.left - b.left || a.sourceIndex - b.sourceIndex)
        .map((cue) => {
            let lane = laneEnds.findIndex((end) => cue.left - end >= minimumGapPercent);
            if (lane < 0) lane = laneEnds.length;
            laneEnds[lane] = cue.left;
            return { ...cue, lane };
        });
    return { cues, laneCount: Math.max(1, laneEnds.length) };
};

export default function EditorialTeamPanel({ projectId, onUsePrompt, onBeforeSave, onBeforeRestore, onRestored, onRestoreFailed }) {
    const [review, setReview] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [expanded, setExpanded] = useState(null);
    const [versions, setVersions] = useState([]);
    const [versionWorking, setVersionWorking] = useState(false);
    const cueLayout = useMemo(
        () => layoutTimelineCues(review?.timeline?.events || [], review?.timeline?.duration),
        [review?.timeline?.events, review?.timeline?.duration]
    );

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const [team, saved] = await Promise.all([getEditorialTeamReview(projectId), getEditVersions(projectId)]);
            setReview(team);
            setVersions(saved?.versions || []);
        }
        catch (exception) { setError(apiErrorMessage(exception, "Editorial team could not review this project")); }
        finally { setLoading(false); }
    }, [projectId]);

    const saveDraft = async () => {
        setVersionWorking(true);
        try {
            const editorState = await onBeforeSave?.();
            const result = await saveEditVersion(projectId, `Draft ${versions.length + 1}`, editorState);
            setVersions(result.versions || []);
        } catch (exception) { setError(apiErrorMessage(exception, "Draft could not be saved")); }
        finally { setVersionWorking(false); }
    };

    const restoreDraft = async (versionId) => {
        setVersionWorking(true);
        try {
            await onBeforeRestore?.();
            const result = await restoreEditVersion(projectId, versionId);
            await onRestored?.(result?.project);
            await load();
        }
        catch (exception) {
            await onRestoreFailed?.();
            setError(apiErrorMessage(exception, "Draft could not be restored"));
        }
        finally { setVersionWorking(false); }
    };

    useEffect(() => { load(); }, [load]);

    return (
        <section className="panel" data-testid="editorial-team-panel">
            <div className="px-5 py-4 border-b border-white/10 flex items-start justify-between gap-3">
                <div>
                    <div className="font-mono text-[10px] text-[#ccff00] tracking-widest">// EDITORIAL ROOM</div>
                    <h2 className="font-display text-2xl tracking-wider mt-1">FIVE SETS OF EYES</h2>
                    <p className="text-xs text-white/45 mt-1 max-w-2xl">Evidence-backed notes from story, rhythm, performance, visual, and finishing roles. Nothing changes until you send a note to Edit Copilot and approve its preview.</p>
                </div>
                <button type="button" className="btn-ghost !p-0 w-11 h-11 justify-center" onClick={load} disabled={loading} aria-label="Refresh editorial review" data-testid="refresh-editorial-team">
                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                </button>
            </div>
            <div className="p-5">
                {loading && <div className="py-6 flex items-center justify-center gap-2 text-xs font-mono text-white/40"><Loader2 className="w-4 h-4 animate-spin" /> TEAM REVIEWING THE CUT</div>}
                {error && <div className="border border-[#ff3333]/40 bg-[#ff3333]/[0.05] p-3 text-xs text-[#ff9999]" role="alert">{error}<button type="button" className="underline ml-2" onClick={load}>Retry</button></div>}
                {review && (
                    <div className="space-y-4">
                        <div className="border border-[#ccff00]/30 bg-[#ccff00]/[0.04] p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3" data-testid="editorial-quality-score">
                            <div><div className="font-mono text-[10px] tracking-widest text-[#ccff00]">// EDITORIAL QUALITY GATE</div><div className="mt-1 text-sm text-white/65">{review.quality?.disclaimer}</div></div>
                            <div className="shrink-0 text-right"><div className="font-heading text-4xl text-[#ccff00]">{review.quality?.score ?? "—"}</div><div className="font-mono text-[10px] uppercase text-white/45">{String(review.quality?.verdict || "review").replaceAll("_", " ")}</div></div>
                        </div>
                        <div className="border border-white/10 bg-black p-4" data-testid="editorial-review-timeline">
                            <div className="flex items-center justify-between gap-3"><div className="font-mono text-[10px] tracking-widest text-white/45">// REVIEW TIMELINE</div><div className="font-mono text-[10px] text-white/35">{review.timeline?.events?.length || 0} CUES</div></div>
                            <div className="mt-4 overflow-x-auto" role="region" aria-label="Editorial review cues">
                                <div className="relative min-w-[640px] border-y border-white/10" style={{ height: `${cueLayout.laneCount * 48 + 8}px` }}>
                                    <div className="absolute inset-x-0 top-1/2 border-t border-white/10" />
                                    {cueLayout.cues.map((event) => {
                                    const color = event.type === "filler" ? "#ff4f5e" : event.type === "broll" ? "#ff4f8b" : "#00d9ff";
                                        return <button type="button" key={`${event.type}-${event.time}-${event.sourceIndex}`} title={`${event.label} at ${Number(event.time || 0).toFixed(2)}s`} aria-label={`${event.label} at ${Number(event.time || 0).toFixed(2)} seconds`} className="absolute flex h-11 w-11 -translate-x-1/2 items-center justify-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#ccff00]" style={{ left: `clamp(22px, ${event.left}%, calc(100% - 22px))`, top: `${event.lane * 48 + 4}px` }} onClick={() => onUsePrompt?.(`Review the ${event.type} cue at ${Number(event.time || 0).toFixed(2)} seconds: ${event.label}. Keep or remove it based on whether it improves the story.`)} data-testid={`timeline-cue-${event.sourceIndex}`}><span className="h-7 w-2" style={{ background: color }} aria-hidden="true" /></button>;
                                    })}
                                </div>
                            </div>
                            <div className="mt-2 flex flex-wrap gap-3 font-mono text-[10px] text-white/40"><span><i className="inline-block w-2 h-2 bg-[#ff4f5e] mr-1" />FILLER</span><span><i className="inline-block w-2 h-2 bg-[#ff4f8b] mr-1" />B-ROLL</span><span><i className="inline-block w-2 h-2 bg-[#00d9ff] mr-1" />EDIT CUE</span></div>
                        </div>
                        <div className="border border-white/10 bg-black p-4" data-testid="edit-versions">
                            <div className="flex items-center justify-between gap-3"><div><div className="font-mono text-[10px] tracking-widest text-white/45">// DRAFT MEMORY</div><div className="mt-1 text-xs text-white/45">Save a checkpoint before trying a team recommendation.</div></div><button type="button" className="btn-ghost min-h-11 !px-3 text-xs" onClick={saveDraft} disabled={versionWorking}>{versionWorking ? <Loader2 className="w-3 h-3 animate-spin" /> : "Save draft"}</button></div>
                            {versions.length > 0 && <div className="mt-3 grid gap-2">{versions.slice().reverse().slice(0, 4).map((version) => <div key={version.id} className="flex items-center justify-between gap-3 border-t border-white/10 pt-2"><span className="font-mono text-[10px] text-white/50">{version.name} · {new Date(version.created_at).toLocaleString()}</span><button type="button" className="min-h-11 px-2 text-[10px] font-mono uppercase text-[#ccff00]" onClick={() => restoreDraft(version.id)} disabled={versionWorking}>Restore</button></div>)}</div>}
                        </div>
                        {review.team.map((member) => {
                            const Icon = ICONS[member.id] || BookOpen;
                            const isOpen = expanded === member.id;
                            const notes = member.notes || [];
                            return (
                                <div key={member.id} className="border border-white/10 bg-black" data-testid={`editorial-role-${member.id}`}>
                                    <button type="button" className="w-full min-h-11 text-left p-3 flex items-center gap-3" onClick={() => setExpanded(isOpen ? null : member.id)} aria-expanded={isOpen}>
                                        <Icon className="w-4 h-4 shrink-0" style={{ color: member.color }} />
                                        <span className="min-w-0 flex-1"><span className="block font-mono text-xs uppercase tracking-wider text-white/80">{member.name}</span><span className="block mt-1 text-xs text-white/40">{member.editorial_lens}</span></span>
                                        <span className="font-mono text-[10px] text-white/35">{member.notes?.length || 0} NOTE{member.notes?.length === 1 ? "" : "S"}</span>
                                    </button>
                                    {isOpen && notes.length > 0 && (
                                        <div className="border-t border-white/10 divide-y divide-white/10">
                                            {notes.map((note, noteIndex) => (
                                                <article className="p-4" key={note.id || `${member.id}-${noteIndex}`} data-testid={`editorial-note-${member.id}-${noteIndex}`}>
                                                    <div className="flex items-start justify-between gap-3"><div><div className="font-heading text-xl tracking-wider">{note.title}</div><div className="mt-2 text-sm leading-6 text-white/65">{note.detail}</div></div><span className={`font-mono text-[10px] uppercase ${note.priority === "high" ? "text-[#ffb000]" : "text-white/35"}`}>{note.priority}</span></div>
                                                    <div className="mt-3 flex flex-wrap gap-2">{(note.evidence || []).map((item) => <span key={item} className="border border-white/10 px-2 py-1 font-mono text-[10px] text-white/45">{item}</span>)}</div>
                                                    <div className="mt-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-t border-white/10 pt-3"><p className="text-xs text-white/45 flex-1">Suggested edit: {note.prompt}</p><button type="button" className="btn-brand min-h-11 !px-3 text-xs shrink-0 justify-center" onClick={() => onUsePrompt?.(note.prompt)} data-testid={`use-editorial-note-${member.id}-${noteIndex}`}><ArrowUpRight className="w-3 h-3" /> Send to Copilot</button></div>
                                                </article>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </section>
    );
}
