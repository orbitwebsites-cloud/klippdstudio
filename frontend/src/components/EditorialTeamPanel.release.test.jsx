import { act } from "react";
import { createRoot } from "react-dom/client";
import EditorialTeamPanel from "./EditorialTeamPanel";
import {
    getEditVersions,
    getEditorialTeamReview,
    restoreEditVersion,
    saveEditVersion,
} from "@/lib/klipApi";

jest.mock("@/lib/klipApi", () => ({
    apiErrorMessage: (_error, fallback) => fallback,
    getEditVersions: jest.fn(),
    getEditorialTeamReview: jest.fn(),
    restoreEditVersion: jest.fn(),
    saveEditVersion: jest.fn(),
}), { virtual: true });

const review = {
    quality: { score: 80, verdict: "review", disclaimer: "Review before delivery" },
    timeline: { duration: 60, events: [] },
    team: [{
        id: "story",
        name: "Story Editor",
        editorial_lens: "Structure and payoff",
        color: "#ccff00",
        notes: [
            { id: "one", title: "First recommendation", detail: "First detail", evidence: ["A"], prompt: "First prompt", priority: "high" },
            { id: "two", title: "Second recommendation", detail: "Second detail", evidence: ["B"], prompt: "Second prompt", priority: "review" },
        ],
    }],
};

describe("EditorialTeamPanel release behavior", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        getEditorialTeamReview.mockResolvedValue(review);
        getEditVersions.mockResolvedValue({ versions: [{ id: "v1", name: "Draft 1", created_at: "2026-01-01T00:00:00Z" }] });
        saveEditVersion.mockResolvedValue({ versions: [] });
        restoreEditVersion.mockResolvedValue({ project: { id: "project-1", edit_options: { captions: false } } });
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
        jest.clearAllMocks();
    });

    const renderPanel = async (props = {}) => {
        await act(async () => {
            root.render(<EditorialTeamPanel projectId="project-1" {...props} />);
        });
    };

    test("shows every recommendation for an expanded editorial role", async () => {
        await renderPanel();
        const roleButton = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("Story Editor"));
        await act(async () => roleButton.click());

        expect(container.textContent).toContain("First recommendation");
        expect(container.textContent).toContain("Second recommendation");
        expect(container.querySelectorAll('[data-testid^="editorial-note-story-"]')).toHaveLength(2);
    });

    test("flushes current editor options before saving a checkpoint", async () => {
        let releaseFlush;
        const onBeforeSave = jest.fn(() => new Promise((resolve) => { releaseFlush = resolve; }));
        const editorState = { excluded_filler_indices: [2], selected_broll: [{ word_index: 8, id: "clip-1" }] };
        await renderPanel({ onBeforeSave });
        const saveButton = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("Save draft"));

        await act(async () => saveButton.click());
        expect(onBeforeSave).toHaveBeenCalledTimes(1);
        expect(saveEditVersion).not.toHaveBeenCalled();

        await act(async () => releaseFlush(editorState));
        expect(saveEditVersion).toHaveBeenCalledWith("project-1", "Draft 2", editorState);
    });

    test("hands the restored project to the editor before review reload", async () => {
        const onBeforeRestore = jest.fn();
        let finishHydration;
        const onRestored = jest.fn(() => new Promise((resolve) => { finishHydration = resolve; }));
        await renderPanel({ onBeforeRestore, onRestored });
        const restoreButton = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("Restore"));

        await act(async () => restoreButton.click());
        expect(onBeforeRestore).toHaveBeenCalledTimes(1);
        expect(onBeforeRestore.mock.invocationCallOrder[0]).toBeLessThan(restoreEditVersion.mock.invocationCallOrder[0]);
        expect(onRestored).toHaveBeenCalledWith({ id: "project-1", edit_options: { captions: false } });
        expect(getEditorialTeamReview).toHaveBeenCalledTimes(1);

        await act(async () => finishHydration());
        expect(getEditorialTeamReview).toHaveBeenCalledTimes(2);
    });

    test("resumes editor autosave when restore fails", async () => {
        const onRestoreFailed = jest.fn();
        restoreEditVersion.mockRejectedValueOnce(new Error("restore failed"));
        await renderPanel({ onRestoreFailed });
        const restoreButton = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("Restore"));

        await act(async () => restoreButton.click());
        expect(onRestoreFailed).toHaveBeenCalledTimes(1);
        expect(container.textContent).toContain("Draft could not be restored");
    });
});
