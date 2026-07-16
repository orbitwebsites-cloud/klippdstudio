jest.mock("@/lib/klipApi", () => ({}), { virtual: true });

import { PLANS, WORKFLOW_CAPABILITIES } from "./Pricing";

describe("Pricing release copy", () => {
    test("assigns viral clip extraction to Pro at the configured plan prices", () => {
        const starter = PLANS.find((plan) => plan.id === "basic");
        const pro = PLANS.find((plan) => plan.id === "pro");

        expect(starter.price).toBe("$19");
        expect(starter.clipExtraction).toContain("Upgrade to Pro");
        expect(starter.features.join(" ")).not.toMatch(/viral clip extraction/i);
        expect(pro.price).toBe("$29");
        expect(pro.clipExtraction).toMatch(/included/i);
        expect(pro.features.join(" ")).toMatch(/viral clip extraction/i);
    });

    test("does not publish unsupported Opus comparison claims", () => {
        expect(JSON.stringify({ plans: PLANS, capabilities: WORKFLOW_CAPABILITIES })).not.toMatch(/opus/i);
        expect(WORKFLOW_CAPABILITIES.every((item) => item.title && item.detail)).toBe(true);
    });
});
