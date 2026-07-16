# Competitor benchmark: plan editing

Research date: 2026-07-16

## What the market teaches us

| Product | Strongest pattern | Gap we can exploit |
| --- | --- | --- |
| Descript / Underlord | Conversational co-editing that remembers project context and can execute multi-step workflows | The user still needs to trust a broad agent action; the review surface is the opportunity |
| CapCut | Transcript-based editing, keyword search, filler-word removal, and synchronized timeline changes | Powerful, but the workflow is feature-rich rather than goal-led |
| Riverside | A clear path from rough cut to polished audio, captions, chapters, and social clips | Optimized for recording/podcast workflows; less opinionated for creator style and B-roll decisions |

Community signal from public creator discussions: transcript editing is repeatedly described as the feature that changes the workflow; filler-word and silence removal are the highest-satisfaction automation; reusable caption presets and fast clip exports matter more than a giant effects catalog. The recurring complaint is that AI clip selection can be hit-or-miss, so every automated decision needs a visible review step.

## Klip Studio direction

Make the edit plan the product's center of gravity:

1. Start from a creator goal, not a blank command box.
2. Translate the request into a compact, readable plan before anything changes.
3. Show scope, operation count, timing, and assumptions so the user can judge the edit.
4. Keep the transcript, timeline, and chat as synchronized ways to inspect the same plan.
5. Make apply reversible, with undo/redo immediately available.

## Implemented in this pass

- Goal-led plan starters: polish, viral, authentic, and best-clip workflows.
- Review state renamed from “preview” to “edit plan” to set the right expectation.
- Plan summary metrics for number of changes, scope, and expected render effort.
- Operation cards with category labels and reviewability affordance.
- Warnings/assumptions surfaced before apply when returned by the planner.
- Existing apply, cancel, undo, and redo behavior preserved.
- Dead-air trimming with a user-controlled pause threshold, implemented from word timestamps.
- Transcript search with bulk “cut matches” for precise text-first editing.

Sources:

- Descript Underlord: https://help.descript.com/hc/en-us/articles/36803785502221-Underlord-beta-Your-AI-co-editor-in-Descript
- CapCut transcript-based editing: https://www.capcut.com/resource/edit-video-with-text
- Riverside AI video editor: https://riverside.fm/tools/ai-video-editor
