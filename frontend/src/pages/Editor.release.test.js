import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("react-router-dom", () => ({ useParams: jest.fn(), useNavigate: jest.fn(), useLocation: jest.fn() }), { virtual: true });
jest.mock("@/components/LibraryPanel", () => () => null, { virtual: true });
jest.mock("@/components/CreatorProfilesPanel", () => () => null, { virtual: true });
jest.mock("@/components/EditChatPanel", () => () => null, { virtual: true });
jest.mock("@/components/EditorialTeamPanel", () => () => null, { virtual: true });
jest.mock("@/components/EditorTimeline", () => () => null, { virtual: true });
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn(), info: jest.fn() } }), { virtual: true });
jest.mock("@/lib/klipApi", () => ({
    API: "http://localhost/api",
    analyzeProject: jest.fn(),
    apiErrorMessage: (_error, fallback) => fallback,
    brollSearch: jest.fn(),
    downloadUrl: (id, label) => label ? `/api/projects/${id}/download?clip=${encodeURIComponent(label)}` : `/api/projects/${id}/download`,
    extractViralClips: jest.fn(),
    featureAccessState: jest.fn(),
    getProject: jest.fn(),
    mediaClip: (id, label) => `/api/media/clip/${id}/${encodeURIComponent(label)}`,
    mediaOriginal: (id) => `/api/media/original/${id}`,
    mediaOutput: (id) => `/api/media/output/${id}`,
    removeMusic: jest.fn(),
    renderProject: jest.fn(),
    saveEditOptions: jest.fn().mockResolvedValue({ ok: true }),
    uploadCustomBroll: jest.fn(),
    uploadMusic: jest.fn(),
}), { virtual: true });

import { useLocation, useNavigate, useParams } from "react-router-dom";
import { getProject, removeMusic, renderProject, saveEditOptions } from "@/lib/klipApi";
import Editor, { getFocusedOutput, hydrateEditorOptions, isInvalidRenderRange } from "./Editor";

const baseProject = {
    id: "project-1",
    name: "Release demo",
    status: "ready",
    status_message: "Ready",
    progress: 100,
    duration: 60,
    width: 1920,
    height: 1080,
    transcript: { words: [] },
    analysis: { filler_indices: [], emphasis_indices: [], broll_moments: [] },
    edit_options: {},
};

describe("Editor release safeguards", () => {
    let container;
    let root;
    let currentProject;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        currentProject = { ...baseProject };
        useParams.mockReturnValue({ id: "project-1" });
        useNavigate.mockReturnValue(jest.fn());
        useLocation.mockReturnValue({ state: null });
        getProject.mockImplementation(() => Promise.resolve(currentProject));
        removeMusic.mockResolvedValue({ ok: true, background_music: false });
        renderProject.mockResolvedValue({ ok: true, status: "queued_render" });
        saveEditOptions.mockResolvedValue({ ok: true });
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
        jest.clearAllMocks();
    });

    const renderEditor = async () => {
        await act(async () => {
            root.render(<Editor />);
            await Promise.resolve();
        });
    };

    const changeInput = async (testId, value) => {
        const input = container.querySelector(`[data-testid="${testId}"]`);
        const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
        await act(async () => {
            setValue.call(input, value);
            input.dispatchEvent(new Event("input", { bubbles: true }));
        });
    };

    test("hydrates the attached music name, enabled state, and saved volume", () => {
        const hydrated = hydrateEditorOptions(
            {
                style: "tiktok",
                aspect: "16:9",
                renderOpts: {
                    background_music: false,
                    background_music_volume: 0.16,
                    captions: true,
                },
            },
            {
                background_music_name: "licensed-bed.mp3",
                edit_options: {
                    style: "editorial",
                    aspect: "9:16",
                    background_music: true,
                    background_music_volume: 0.27,
                    captions: false,
                },
            }
        );

        expect(hydrated).toMatchObject({
            style: "editorial",
            aspect: "9:16",
            musicName: "licensed-bed.mp3",
            renderOpts: {
                background_music: true,
                background_music_volume: 0.27,
                captions: false,
            },
        });
    });

    test.each([
        ["", "", 60, false],
        ["5", "", 60, true],
        ["5", "4", 60, true],
        ["-1", "4", 60, true],
        ["5", "61", 60, true],
        ["5", "12", 60, false],
    ])("validates nonempty render range %p-%p", (start, end, duration, expected) => {
        expect(isInvalidRenderRange(start, end, duration)).toBe(expected);
    });

    test("does not hydrate stale enabled music without an attachment", () => {
        const hydrated = hydrateEditorOptions(
            { style: "tiktok", aspect: "16:9", renderOpts: { background_music: true } },
            { edit_options: { background_music: true } }
        );
        expect(hydrated.renderOpts.background_music).toBe(false);
        expect(hydrated.musicName).toBe("");
    });

    test("range render completion surfaces its preview and download", async () => {
        renderProject.mockImplementation(async (_id, options) => {
            currentProject = {
                ...currentProject,
                status: "done",
                render_options: options,
                last_clip_label: options.clip_label,
                focused_render: {
                    label: options.clip_label,
                    start: options.clip_start,
                    end: options.clip_end,
                },
                viral_renders: { [options.clip_label]: "C:/data/output/range.mp4" },
            };
            return { ok: true, status: "queued_render" };
        });
        await renderEditor();
        await changeInput("range-start", "5");
        await changeInput("range-end", "12");

        await act(async () => {
            container.querySelector('[data-testid="render-btn"]').click();
            await Promise.resolve();
        });

        expect(renderProject).toHaveBeenCalledWith("project-1", expect.objectContaining({
            clip_start: 5,
            clip_end: 12,
            clip_label: "range_5_12s",
        }));
        expect(container.querySelector('[data-testid="focused-output"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="focused-download-btn"]').getAttribute("href"))
            .toBe("/api/projects/project-1/download?clip=range_5_12s");
        expect(container.querySelector('[data-testid="video-player"]').getAttribute("src"))
            .toBe("/api/media/clip/project-1/range_5_12s");
        expect(getFocusedOutput(currentProject)).toMatchObject({ label: "range_5_12s", start: 5, end: 12 });
    });

    test("reload hydrates uploaded music and detach waits for backend cleanup", async () => {
        currentProject = {
            ...currentProject,
            background_music_path: "C:/data/music/project-1_track.mp3",
            background_music_name: "licensed-bed.mp3",
            edit_options: { background_music: true, background_music_volume: 0.22 },
        };
        await renderEditor();

        expect(container.querySelector('[data-testid="background-music-name"]').textContent)
            .toBe("licensed-bed.mp3");
        expect(container.querySelector('[data-testid="background-music-state"]').textContent)
            .toBe("ENABLED");

        await act(async () => {
            container.querySelector('[data-testid="remove-background-music"]').click();
            await Promise.resolve();
        });

        expect(removeMusic).toHaveBeenCalledWith("project-1");
        expect(container.querySelector('[data-testid="background-music-name"]').textContent)
            .toBe("Attach a licensed music bed");
        expect(container.querySelector('[data-testid="remove-background-music"]')).toBeNull();
    });
});
