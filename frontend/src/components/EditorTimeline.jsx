import { useCallback, useEffect, useMemo, useState } from "react";
import { BookmarkPlus, Loader2, Trash2 } from "lucide-react";
import { apiErrorMessage, createProjectMarker, deleteProjectMarker, getProjectMarkers } from "@/lib/klipApi";

const formatTime = (value) => {
    const seconds = Math.max(0, Number(value) || 0);
    return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
};

export const layoutTimelineItems = (items = [], duration = 1, minimumWidthPercent = 7) => {
    const total = Math.max(1, Number(duration) || 1);
    const laneEnds = [];
    const laidOut = items
        .map((item, sourceIndex) => {
            const start = Math.max(0, Math.min(total, Number(item.start) || 0));
            const end = Math.max(start, Math.min(total, Number(item.end) || start));
            const width = Math.min(100, Math.max(minimumWidthPercent, ((end - start) / total) * 100));
            const left = Math.max(0, Math.min(100 - width, (start / total) * 100));
            return { ...item, sourceIndex, start, end, left, width };
        })
        .sort((a, b) => a.left - b.left || a.sourceIndex - b.sourceIndex)
        .map((item) => {
            let lane = laneEnds.findIndex((laneEnd) => item.left >= laneEnd + 1);
            if (lane < 0) lane = laneEnds.length;
            laneEnds[lane] = item.left + item.width;
            return { ...item, lane };
        });
    return { items: laidOut, laneCount: Math.max(1, laneEnds.length) };
};

export default function EditorTimeline({ project, currentTime, onSeek }) {
    const [markers, setMarkers] = useState([]);
    const [label, setLabel] = useState("");
    const [loading, setLoading] = useState(true);
    const [working, setWorking] = useState(false);
    const [error, setError] = useState("");
    const duration = Math.max(1, Number(project?.duration) || 1);
    const words = project?.transcript?.words || [];
    const analysis = project?.analysis || {};
    const fillers = useMemo(() => new Set(analysis.filler_indices || []), [analysis.filler_indices]);
    const broll = analysis.broll_moments || [];
    const audioCues = analysis.audio_cues || [];
    const tracks = [
        { name: "VIDEO 01", tone: "bg-[#ccff00]/40", items: [{ start: 0, end: duration, label: project.name || "Source video" }] },
        { name: "SPEECH", tone: "bg-[#00d9ff]/30", items: words.length ? [{ start: words[0].start || 0, end: words[words.length - 1].end || duration, label: `${words.length} words` }] : [] },
        { name: "B-ROLL", tone: "bg-[#ff4f8b]/45", items: broll.map((item) => ({ start: words[item.word_index]?.start || 0, end: (words[item.word_index]?.end || 0) + 1.5, label: item.query || "B-roll" })) },
        { name: "AUDIO CUES", tone: "bg-[#ffb000]/50", items: audioCues.map((item) => ({ start: words[item.word_index]?.start || 0, end: (words[item.word_index]?.start || 0) + 0.4, label: item.type || "cue" })) },
    ].map((track) => ({ ...track, layout: layoutTimelineItems(track.items, duration) }));
    const markerLayout = layoutTimelineItems(markers.map((marker) => ({ ...marker, start: marker.time, end: marker.time })), duration);

    const loadMarkers = useCallback(async () => {
        try { setMarkers((await getProjectMarkers(project.id)).markers || []); }
        catch (exception) { setError(apiErrorMessage(exception, "Markers could not be loaded")); }
        finally { setLoading(false); }
    }, [project.id]);

    useEffect(() => { loadMarkers(); }, [loadMarkers]);

    const position = (time) => `${Math.max(0, Math.min(100, (Number(time || 0) / duration) * 100))}%`;
    const seekFromEvent = (event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        onSeek?.(Math.max(0, Math.min(duration, ((event.clientX - bounds.left) / bounds.width) * duration)));
    };

    const addMarker = async (event) => {
        event.preventDefault();
        const clean = label.trim();
        if (!clean) return;
        setWorking(true);
        setError("");
        try {
            const result = await createProjectMarker(project.id, { time: Number(currentTime.toFixed(2)), label: clean, kind: "note" });
            setMarkers(result.markers || []);
            setLabel("");
        } catch (exception) { setError(apiErrorMessage(exception, "Marker could not be saved")); }
        finally { setWorking(false); }
    };

    const removeMarker = async (markerId) => {
        setWorking(true);
        try { setMarkers((await deleteProjectMarker(project.id, markerId)).markers || []); }
        catch (exception) { setError(apiErrorMessage(exception, "Marker could not be deleted")); }
        finally { setWorking(false); }
    };

    return (
        <section className="panel" data-testid="editor-timeline">
            <div className="px-5 py-4 border-b border-white/10 flex items-start justify-between gap-4">
                <div><div className="font-mono text-[10px] tracking-widest text-[#ccff00]">// EDIT TIMELINE</div><h2 className="font-display text-2xl tracking-wider mt-1">CUT REVIEW</h2><p className="text-xs text-white/45 mt-1">Source, speech, B-roll, audio cues, and notes stay aligned to the playhead.</p></div>
                <div className="font-mono text-xs text-white/60">{formatTime(currentTime)} / {formatTime(duration)}</div>
            </div>
            <div className="p-5">
                {error && <div className="mb-3 text-xs text-[#ff9b9b]" role="alert">{error}</div>}
                <div className="overflow-x-auto">
                    <div className="min-w-[720px]">
                        <div
                            className="relative ml-24 h-11 border-b border-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#ccff00]"
                            onClick={seekFromEvent}
                            onKeyDown={(event) => {
                                const value = Number(currentTime) || 0;
                                const step = event.shiftKey ? 1 : 0.1;
                                let next = value;
                                if (event.key === "ArrowLeft" || event.key === "ArrowDown") next = value - step;
                                else if (event.key === "ArrowRight" || event.key === "ArrowUp") next = value + step;
                                else if (event.key === "Home") next = 0;
                                else if (event.key === "End") next = duration;
                                else return;
                                event.preventDefault();
                                onSeek?.(Math.max(0, Math.min(duration, next)));
                            }}
                            role="slider"
                            aria-label="Timeline position"
                            aria-valuemin="0"
                            aria-valuemax={duration}
                            aria-valuenow={currentTime}
                            tabIndex={0}
                        >
                            {[0, 25, 50, 75, 100].map((tick) => <span key={tick} className="absolute bottom-2 font-mono text-[9px] text-white/35" style={{ left: `${tick}%`, transform: "translateX(-50%)" }}>{formatTime(duration * tick / 100)}</span>)}
                        </div>
                        {tracks.map((track) => {
                            const height = track.layout.laneCount * 48;
                            return <div key={track.name} className="grid grid-cols-[6rem_1fr] border-b border-white/10"><div className="flex items-start pt-4 font-mono text-[10px] tracking-wider text-white/45">{track.name}</div><div className="relative" style={{ height: `${height}px` }} onClick={seekFromEvent}><div className="absolute inset-y-0 z-20 w-px bg-[#ccff00] pointer-events-none" style={{ left: position(currentTime) }} />{track.layout.items.map((item) => <button type="button" key={`${track.name}-${item.sourceIndex}`} className={`absolute z-10 h-11 min-w-11 ${track.tone} border border-white/10 px-2 text-left overflow-hidden text-[10px] text-white/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#ccff00]`} style={{ left: `${item.left}%`, top: `${item.lane * 48 + 2}px`, width: `${item.width}%` }} onClick={(event) => { event.stopPropagation(); onSeek?.(item.start); }} title={`${item.label} · ${formatTime(item.start)}`} aria-label={`${item.label} at ${formatTime(item.start)}`}>{item.label}</button>)}</div></div>;
                        })}
                        <div className="grid grid-cols-[6rem_1fr] border-b border-white/10"><div className="flex items-start pt-4 font-mono text-[10px] tracking-wider text-white/45">NOTES</div><div className="relative" style={{ height: `${markerLayout.laneCount * 48}px` }} onClick={seekFromEvent}><div className="absolute inset-y-0 z-20 w-px bg-[#ccff00] pointer-events-none" style={{ left: position(currentTime) }} />{markerLayout.items.map((marker) => <button type="button" key={marker.id} className="absolute z-30 flex h-11 w-11 items-center justify-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#ccff00]" style={{ left: `${marker.left}%`, top: `${marker.lane * 48 + 2}px` }} title={`${marker.label} · ${formatTime(marker.time)}`} onClick={(event) => { event.stopPropagation(); onSeek?.(marker.time); }} aria-label={`Jump to marker ${marker.label}`}><span className="h-3 w-3 rotate-45 bg-[#ccff00]" aria-hidden="true" /></button>)}</div></div>
                    </div>
                </div>
                <div className="mt-4 flex flex-col sm:flex-row gap-2 sm:items-end">
                    <form onSubmit={addMarker} className="flex gap-2 flex-1"><label className="flex-1 font-mono text-[10px] uppercase text-white/45">Add note at {formatTime(currentTime)}<input value={label} onChange={(event) => setLabel(event.target.value)} className="mt-1 min-h-11 w-full bg-black border border-white/15 px-3 py-2 text-sm text-white outline-none focus:border-[#ccff00]" placeholder="e.g. strongest reaction" data-testid="marker-label" /></label><button type="submit" className="btn-brand min-h-11 !px-3 text-xs" disabled={!label.trim() || working} data-testid="add-marker">{working ? <Loader2 className="w-3 h-3 animate-spin" /> : <BookmarkPlus className="w-3 h-3" />} Add marker</button></form>
                    {markers.length > 0 && <div className="flex flex-wrap gap-2">{markers.slice(-4).map((marker) => <button type="button" key={marker.id} className="btn-ghost min-h-11 !px-3 text-[10px]" onClick={() => removeMarker(marker.id)} title={`Delete ${marker.label}`}><Trash2 className="w-3 h-3" />{marker.label}</button>)}</div>}
                </div>
                {loading && <div className="mt-2 text-[10px] font-mono text-white/30">Loading markers…</div>}
            </div>
        </section>
    );
}
