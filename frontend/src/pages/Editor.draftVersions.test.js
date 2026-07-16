jest.mock("react-router-dom", () => ({ useParams: jest.fn(), useNavigate: jest.fn(), useLocation: jest.fn() }), { virtual: true });
jest.mock("@/components/LibraryPanel", () => () => null, { virtual: true });
jest.mock("@/components/CreatorProfilesPanel", () => () => null, { virtual: true });
jest.mock("@/components/EditChatPanel", () => () => null, { virtual: true });
jest.mock("@/components/EditorialTeamPanel", () => () => null, { virtual: true });
jest.mock("@/components/EditorTimeline", () => () => null, { virtual: true });
jest.mock("@/lib/klipApi", () => ({ API: "http://localhost/api" }), { virtual: true });

import { hydrateEditorOptions, serializeEditorDraft } from "./Editor";

describe("editor draft version state", () => {
    test("round-trips manual cuts, full B-roll selections, and editable controls", () => {
        const snapshot = serializeEditorDraft({
            style: "editorial",
            aspect: "9:16",
            renderOpts: {
                remove_fillers: true,
                remove_silences: true,
                silence_threshold: 1.2,
                captions: false,
                sfx: true,
                zoom_ins: false,
                broll: true,
                background_music: true,
                background_music_volume: 0.22,
            },
            excludedFillers: new Set([7, 2, 7]),
            addedFillers: new Set([11, 4]),
            brollSelected: {
                15: {
                    id: "library-clip",
                    name: "Product close-up",
                    local_path: "library/product.mp4",
                    video_url: "/api/assets/product.mp4",
                    is_custom: true,
                    generated: false,
                },
            },
            creatorProfileId: "profile-1",
        });

        const hydrated = hydrateEditorOptions(
            {
                style: "tiktok",
                aspect: "16:9",
                renderOpts: {
                    remove_fillers: false,
                    remove_silences: false,
                    silence_threshold: 0.8,
                    captions: true,
                    sfx: false,
                    zoom_ins: true,
                    broll: false,
                    background_music: false,
                    background_music_volume: 0.16,
                },
            },
            {
                edit_options: snapshot,
                background_music_path: "audio/licensed.mp3",
                background_music_name: "licensed.mp3",
            }
        );

        expect(serializeEditorDraft({
            ...hydrated,
            renderOpts: hydrated.renderOpts,
        })).toEqual(snapshot);
    });
});
