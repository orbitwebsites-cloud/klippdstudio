import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, BookOpen, Gauge, Image, Loader2, RefreshCw, ShieldCheck, UserRound } from "lucide-react";
import { apiErrorMessage, getEditVersions, getEditorialTeamReview, restoreEditVersion, saveEditVersion } from "@/lib/klipApi";

const ICONS = { story: BookOpen, rhythm: Gauge, performance: UserRound, visual: Image, finishing: ShieldCheck };

export default function EditorialTeamPanel({ projectId, onUsePrompt }) {
    const [review, setReview] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [expanded, setExpanded] = useState(null);
    const [versions, setVersions] = useState([]);
    const [versionWorking, setVersionWorking] = useState(false);

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
            const result = await saveEditVersion(projectId, `Draft ${versions.length + 1}`);
            setVersions(result.versions || []);
        } catch (exception) { setError(apiErrorMessage(exception, "Draft could not be saved")); }
        finally { setVersionWorking(false); }
    };

    const restoreDraft = async (versionId) => {
        setVersionWorking(true);
        try { await restoreEditVersion(projectId, versionId); await load(); }
        catch (exception) { setError(apiErrorMessage(exception, "Draft could not be restored")); }
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
                <button type="button" className="btn-ghost !p-2" onClick={load} disabled={loading} aria-label="Refresh editorial review" data-testid="refresh-editorial-team">
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
                            <div className="relative mt-5 h-12 border-y border-white/10">
                                <div className="absolute inset-x-0 top-1/2 border-t border-white/10" />
                                {(review.timeline?.events || []).map((event, index) => {
                                    const duration = Number(review.timeline?.duration || 0);
                                    const left = duration > 0 ? Math.max(0, Math.min(100, (Number(event.time || 0) / duration) * 100)) : 0;
                                    const color = event.type === "filler" ? "#ff4f5e" : event.type === "broll" ? "#ff4f8b" : "#00d9ff";
                                    return <button type="button" key={`${event.type}-${event.time}-${index}`} title={`${event.label} at ${Number(event.time || 0).toFixed(2)}s`} aria-label={`${event.label} at ${Number(event.time || 0).toFixed(2)} seconds`} className="absolute top-1/2 -translate-y-1/2 w-2 h-7 -ml-1" style={{ left: `${left}%`, background: color }} onClick={() => onUsePrompt?.(`Review the ${event.type} cue at ${Number(event.time || 0).toFixed(2)} seconds: ${event.label}. Keep or remove it based on whether it improves the story.`)} data-testid={`timeline-cue-${index}`} />;
                                })}
                            </div>
                            <div className="mt-2 flex flex-wrap gap-3 font-mono text-[10px] text-white/40"><span><i className="inline-block w-2 h-2 bg-[#ff4f5e] mr-1" />FILLER</span><span><i className="inline-block w-2 h-2 bg-[#ff4f8b] mr-1" />B-ROLL</span><span><i className="inline-block w-2 h-2 bg-[#00d9ff] mr-1" />EDIT CUE</span></div>
                        </div>
                        <div className="border border-white/10 bg-black p-4" data-testid="edit-versions">
                            <div className="flex items-center justify-between gap-3"><div><div className="font-mono text-[10px] tracking-widest text-white/45">// DRAFT MEMORY</div><div className="mt-1 text-xs text-white/45">Save a checkpoint before trying a team recommendation.</div></div><button type="button" className="btn-ghost !px-3 !py-2 text-xs" onClick={saveDraft} disabled={versionWorking}>{versionWorking ? <Loader2 className="w-3 h-3 animate-spin" /> : "Save draft"}</button></div>
                            {versions.length > 0 && <div className="mt-3 grid gap-2">{versions.slice().reverse().slice(0, 4).map((version) => <div key={version.id} className="flex items-center justify-between gap-3 border-t border-white/10 pt-2"><span className="font-mono text-[10px] text-white/50">{version.name} · {new Date(version.created_at).toLocaleString()}</span><button type="button" className="text-[10px] font-mono uppercase text-[#ccff00]" onClick={() => restoreDraft(version.id)} disabled={versionWorking}>Restore</button></div>)}</div>}
                        </div>
                        {review.team.map((member) => {
                            const Icon = ICONS[member.id] || BookOpen;
                            const isOpen = expanded === member.id;
                            const note = member.notes?.[0];
                            return (
                                <div key={member.id} className="border border-white/10 bg-black" data-testid={`editorial-role-${member.id}`}>
                                    <button type="button" className="w-full text-left p-3 flex items-center gap-3" onClick={() => setExpanded(isOpen ? null : member.id)} aria-expanded={isOpen}>
                                        <Icon className="w-4 h-4 shrink-0" style={{ color: member.color }} />
                                        <span className="min-w-0 flex-1"><span className="block font-mono text-xs uppercase tracking-wider text-white/80">{member.name}</span><span className="block mt-1 text-xs text-white/40">{member.editorial_lens}</span></span>
                                        <span className="font-mono text-[10px] text-white/35">{member.notes?.length || 0} NOTE{member.notes?.length === 1 ? "" : "S"}</span>
                                    </button>
                                    {isOpen && note && (
                                        <div className="border-t border-white/10 p-4">
                                            <div className="flex items-start justify-between gap-3"><div><div className="font-heading text-xl tracking-wider">{note.title}</div><div className="mt-2 text-sm leading-6 text-white/65">{note.detail}</div></div><span className={`font-mono text-[10px] uppercase ${note.priority === "high" ? "text-[#ffb000]" : "text-white/35"}`}>{note.priority}</span></div>
                                            <div className="mt-3 flex flex-wrap gap-2">{note.evidence.map((item) => <span key={item} className="border border-white/10 px-2 py-1 font-mono text-[10px] text-white/45">{item}</span>)}</div>
                                            <div className="mt-4 flex items-center justify-between gap-3 border-t border-white/10 pt-3"><p className="text-xs text-white/45 flex-1">Suggested edit: {note.prompt}</p><button type="button" className="btn-brand !px-3 !py-2 text-xs shrink-0" onClick={() => onUsePrompt?.(note.prompt)} data-testid={`use-editorial-note-${member.id}`}><ArrowUpRight className="w-3 h-3" /> Send to Copilot</button></div>
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
