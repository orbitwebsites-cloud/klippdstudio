# Klipped Editor Council

This is a product-quality benchmark, not a claim that these editors endorsed Klipped or that an AI can reproduce their private judgment. It distills recurring, public ideas from interviews, talks, and documented workflows into tests the product can actually pass.

## The 15 editorial lenses

| Editor | Lens Klipped must respect | Product implication |
| --- | --- | --- |
| Walter Murch | Emotion, story, rhythm, eye-trace, 2D plane, 3D space, continuity | Suggestions must explain the editorial reason for a cut; do not optimize cut count alone. |
| Thelma Schoonmaker | Performance over cosmetic continuity; invisible rhythm when appropriate | Preserve the best take and natural pauses; do not auto-cut every silence. |
| Eddie Hamilton | Selects, progressive refinement, relentless but invisible flow | Show ranked selects, keep rejected options recoverable, and make iteration fast. |
| Joe Walker | Music, silence, atmosphere, and an imagined audience | Evaluate pacing with sound on and off; protect intentional quiet. |
| Sally Menke | Collaboration, tone, structure, and the courage to reshape material | Let the editor disagree with the first AI pass and compare versions. |
| Michael Kahn | Emotional clarity, performance, and disciplined collaboration | A cut needs a human-readable rationale, not a black-box score. |
| Margaret Sixel | Fresh eyes and atypical choices can prevent genre sameness | Creator profiles should guide taste without forcing a template. |
| Paul Hirsch | Clear visual storytelling and economical construction | Prefer readable cause-and-effect over decorative transitions. |
| Hank Corwin | Emotional intensity and associative montage | B-roll recommendations should support meaning, not merely fill empty space. |
| Kirk Baxter | Rhythm, character, and precise tonal control | Give users timing control at word, beat, and range level. |
| Maryann Brandon | Audience testing and clarity without flattening the work | Include review checkpoints and compare alternate cuts before export. |
| Alan Edward Bell | Story geography, coverage, and continuity as tools | Preserve spatial context and make scene/shot relationships inspectable. |
| Tatiana S. Riegel | Instinct, tone, and structural experimentation | Support multiple drafts instead of overwriting the only edit state. |
| Vashi Nedomansky | Practical workflow, organization, and editor ergonomics | Fast bins, shortcuts, markers, proxies, and recoverable operations matter. |
| Casey Faris | Accessible teaching, repeatable workflows, and technical reliability | Every powerful operation needs a clear explanation and a safe undo path. |

## Council verdict on the current product

Klipped has a legitimate differentiated core: transcript-grounded rough cuts, filler review, creator-specific rules, B-roll provenance, edit-chat previews, and post-render QA. That is a strong AI-assisted rough-cut product.

It is not yet a complete non-linear editor. The council would reject any positioning that implies parity with Kdenlive, Shotcut, OpenShot, Pitivi, or LosslessCut. Those tools establish the baseline for real editing: multitrack timeline work, clip bins, markers, waveforms, trimming/splitting, ripple/roll edits, effects, keyframes, autosave, proxies, and project interchange.

## Required product changes

### P0: quality and trust

- Never treat a fixed cut-rate target as universal editorial truth. Gaming, documentary, tutorials, interviews, and dramatic work need different pacing profiles.
- Keep every AI suggestion reviewable: source timestamp, reason, confidence, and reversible operation.
- Preserve pauses, breaths, performance, and room tone unless the user explicitly chooses aggressive cleanup.
- Keep original media untouched and maintain versioned edit states.

### P1: editor fundamentals

- Add a visible review timeline with playhead, markers, cut ranges, transcript words, B-roll cues, and audio events.
- Add markers/notes and keyboard navigation around the playhead.
- Add undo/redo for every manual edit, not only edit-chat operations.
- Add autosave and named draft versions.
- Add a waveform/audio review lane and basic gain/ducking controls.

### P2: workflow depth

- Add bins/search/filtering for media and generated assets.
- Add proxy generation for large uploads.
- Add scene/shot selects with ratings and alternate takes.
- Add export presets plus a transparent render log and QA report.
- Add interchange exports where useful: EDL/CSV/OTIO before promising Premiere/Resolve project export.

## Release gate

An edit is not “good” merely because it renders. Before the UI calls it ready, it should answer:

1. What changed and why?
2. Can the user inspect the exact source range?
3. Can the user undo it or return to the prior draft?
4. Does the pacing fit the selected profile rather than a universal formula?
5. Are captions, audio, safe zones, and B-roll rights checked?
6. Does the export preserve a coherent story and the strongest performance?

## Public source set used for this pass

- Walter Murch: *In the Blink of an Eye* and public discussions of the Rule of Six.
- [Thelma Schoonmaker interview, Film Comment](https://www.filmcomment.com/interview-thelma-schoonmaker/)
- [Eddie Hamilton interview, Film Independent](https://www.filmindependent.org/blog/top-gun-maverick-editor-eddie-hamilton/)
- [Michael Kahn interview, Los Angeles Times](https://www.latimes.com/entertainment/movies/la-et-mn-michael-kahn-steven-spielberg-20180329-story.html)
- [Sally Menke profile, BFI Sight and Sound](https://www.bfi.org.uk/sight-and-sound/features/sally-menke-1953-2010)
- [Pitivi workflow tour](https://www.pitivi.org/tour/)
- [Kdenlive feature set](https://kdenlive.org/features/)
- [Shotcut feature set](https://www.shotcut.org/features/)
- [OpenShot feature set](https://www.openshot.org/features/)
- [LosslessCut project documentation](https://github.com/mifi/lossless-cut)
