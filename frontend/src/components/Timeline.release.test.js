jest.mock("@/lib/klipApi", () => ({}), { virtual: true });

import { layoutTimelineItems } from "./EditorTimeline";
import { layoutTimelineCues } from "./EditorialTeamPanel";

describe("mobile timeline cue layout", () => {
    test("places nearby editorial cues on separate lanes", () => {
        const result = layoutTimelineCues([
            { time: 10, label: "First" },
            { time: 11, label: "Second" },
            { time: 30, label: "Third" },
        ], 60);

        expect(result.cues[0].lane).not.toBe(result.cues[1].lane);
        expect(result.laneCount).toBeGreaterThan(1);
    });

    test("gives short track cues a minimum target width without same-lane collisions", () => {
        const result = layoutTimelineItems([
            { start: 10, end: 10.1, label: "First" },
            { start: 10.2, end: 10.3, label: "Second" },
            { start: 40, end: 40.1, label: "Third" },
        ], 60);

        expect(result.items.every((item) => item.width >= 7)).toBe(true);
        for (const item of result.items) {
            const sameLaneFollowers = result.items.filter((candidate) => candidate.lane === item.lane && candidate.left > item.left);
            for (const follower of sameLaneFollowers) expect(follower.left).toBeGreaterThanOrEqual(item.left + item.width + 1);
        }
    });
});
