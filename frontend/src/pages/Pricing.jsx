import { useEffect, useState } from "react";
import { Check, Crown, Loader2, Sparkles, Zap } from "lucide-react";
import { toast } from "sonner";
import { apiErrorMessage, createBillingPortal, createCheckout, getSubscription } from "@/lib/klipApi";

export const PLANS = [
    {
        id: "basic",
        name: "Starter",
        price: "$19",
        suffix: "USD/mo",
        kicker: "Core editor tools",
        retention: "7-day project retention",
        summary: "Core editing and export tools for solo creators. Automated viral clip extraction is available on Pro.",
        clipExtraction: "Upgrade to Pro for viral clip extraction ($29 USD/month)",
        features: [
            "Manual editor workflow and range exports",
            "Gaming-aware edit planning",
            "Animated captions for short-form formats",
            "Full editor included, not a locked basic cutter",
            "Multi-aspect exports: 9:16, 1:1, and 16:9",
            "Filler-word removal",
            "Watermark-free exports",
        ],
    },
    {
        id: "pro",
        name: "Pro",
        price: "$29",
        suffix: "USD/mo",
        billing: "$29 billed monthly",
        kicker: "Premium editor tools",
        retention: "30-day project retention",
        featured: true,
        summary: "Viral clip extraction plus Creator DNA, edit chat, B-roll, and richer project controls.",
        clipExtraction: "Viral clip extraction included",
        stats: ["Creator DNA", "Edit chat"],
        features: [
            "Everything in Starter plan",
            "Ranked viral clip extraction and short-form render workflow",
            "Creator DNA from owned Klipped projects",
            "Edit Copilot previews for pacing, captions, cuts, B-roll, and emphasis",
            "Approved library and generated B-roll support",
            "Multiple aspect ratios: 9:16, 1:1, 16:9",
            "Edit Copilot preview, apply, undo, and redo",
        ],
    },
    {
        id: "elite",
        name: "Business",
        price: "Custom",
        suffix: "pricing",
        kicker: "Custom beta support",
        retention: "No automatic deletion",
        summary: "For organizations that need tailored processing volume, retention, support, and deployment planning.",
        contact: true,
        features: [
            "Everything in the Pro plan",
            "Customized retention planning",
            "Dedicated onboarding and workflow review",
            "Deployment and persistence planning",
            "Beta support during rollout",
        ],
    },
];

const VALUE_STACK = [
    { metric: "Beta", label: "single workspace mode", detail: "Built for a controlled private workspace while account isolation is still being hardened." },
    { metric: "Pro", label: "premium editor routes", detail: "Creator DNA and Edit Copilot stay visible, with clear upgrade states when the plan is not eligible." },
    { metric: "QA", label: "render review data", detail: "The backend records post-render QA so the editor can expose publishability checks." },
    { metric: "Included", label: "B-roll, captions, filler cuts, exports", detail: "The core editing workflow is bundled into the product surface instead of hidden behind extra screens." },
];

export const WORKFLOW_CAPABILITIES = [
    {
        title: "Editing profiles",
        detail: "Gaming and general editing profiles keep clip planning grounded in the workflows currently available in Klipped.",
    },
    {
        title: "Creator DNA",
        detail: "Creator DNA uses owned Klipped projects to inform pacing, captions, cuts, and visual rhythm.",
    },
    {
        title: "Visible quality review",
        detail: "Post-render QA and visible edit controls keep quality checks inside the editor workflow.",
    },
    {
        title: "Reviewable edit chat",
        detail: "Edit-chat previews turn a rough plan into a reviewable edit before render.",
    },
    {
        title: "Auditable B-roll",
        detail: "Explicit B-roll targets and approved library assets make insert choices visible before rendering.",
    },
    {
        title: "Connected workflow",
        detail: "Editing controls, review, rendering, and delivery stay together in one project workspace.",
    },
];

const ROADMAP_FEATURES = [
    "Auto post to YouTube Shorts, TikTok, IG Reels, or download",
    "1 social account connection",
    "Team workspace with 2 seats included",
    "Bulk clipping for long podcasts, webinars, streams, and courses",
    "Input from 10+ sources",
    "Export to Adobe Premiere Pro and DaVinci Resolve",
    "Social media scheduler and auto-posting",
    "Intercom chat support",
    "Custom fonts and saved brand vocabulary",
    "API access for creator workflows",
    "Video dubbing",
    "Customized seats and social account connections",
    "Dedicated storage",
    "API and custom integrations",
    "Master Service Agreement (MSA)",
    "Priority support with a dedicated Slack channel",
    "Enterprise-level security",
];

export default function Pricing() {
    const [subscription, setSubscription] = useState(null);
    const [subscriptionLoading, setSubscriptionLoading] = useState(true);
    const [subscriptionError, setSubscriptionError] = useState("");
    const [loadingPlan, setLoadingPlan] = useState("");

    const refresh = () => {
        setSubscriptionLoading(true);
        setSubscriptionError("");
        return getSubscription()
            .then((data) => {
                setSubscription(data);
                return data;
            })
            .catch((error) => {
                setSubscriptionError(apiErrorMessage(error, "Could not load billing status"));
                setSubscription(null);
                return null;
            })
            .finally(() => setSubscriptionLoading(false));
    };

    useEffect(() => {
        refresh();
        const result = new URLSearchParams(window.location.search).get("checkout");
        if (result === "success") toast.success("Payment received. Your plan will update once Stripe confirms it.");
        if (result === "cancelled") toast.message("Checkout cancelled.");
    }, []);

    const startCheckout = async (plan) => {
        setLoadingPlan(plan);
        try {
            const { url } = await createCheckout(plan);
            window.location.assign(url);
        } catch (error) {
            toast.error(apiErrorMessage(error, "Could not start checkout"));
            setLoadingPlan("");
        }
    };

    const manageBilling = async () => {
        setLoadingPlan("portal");
        try {
            const { url } = await createBillingPortal();
            window.location.assign(url);
        } catch (error) {
            toast.error(apiErrorMessage(error, "Could not open billing management"));
            setLoadingPlan("");
        }
    };

    const contactSales = () => {
        window.location.assign("mailto:sales@klippdstudio.com?subject=Klipped%20Studio%20Business%20plan");
    };

    const activePlan = subscription?.plan || "basic";
    const billingKnownDisabled = subscription && !subscription.billing_enabled;
    return (
        <main className="min-h-[calc(100vh-72px)] px-6 md:px-16 py-14 md:py-20">
            <div className="max-w-6xl mx-auto">
                <div className="font-mono text-xs tracking-widest text-[#ccff00] mb-5">KLIPPED STUDIO PLANS</div>
                <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-5 mb-12">
                    <h1 className="font-heading text-5xl md:text-7xl leading-[0.9] tracking-wider">EDIT WITH CONTROL.<br />SHIP FASTER.</h1>
                    <div className="font-mono text-xs text-white/50 max-w-sm leading-6">
                        Clear plan gates, visible editor tools, and fewer upgrade traps. Your active plan is <span className="text-white">{subscriptionLoading ? "LOADING" : activePlan.toUpperCase()}</span>.
                    </div>
                </div>
                {subscriptionError && (
                    <div className="mb-5 border border-[#ff5a5a]/30 bg-[#ff5a5a]/10 px-4 py-3 text-sm text-[#ffb3b3] flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3" role="alert">
                        <span>{subscriptionError}. Checkout is locked until billing status loads so existing subscriptions cannot be duplicated.</span>
                        <button type="button" className="btn-ghost justify-center" onClick={refresh}>Retry billing status</button>
                    </div>
                )}

                <section className="mb-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    {VALUE_STACK.map((item) => (
                        <div key={item.label} className="border border-white/10 bg-white/[0.03] px-4 py-4 min-h-[148px]">
                            <div className="font-heading text-4xl text-[#ccff00] tracking-wider">{item.metric}</div>
                            <div className="mt-2 font-mono text-xs uppercase text-white/80 leading-5">{item.label}</div>
                            <p className="mt-3 text-sm text-white/50 leading-5">{item.detail}</p>
                        </div>
                    ))}
                </section>

                <section className="mb-4 border border-white/10 bg-black">
                    <div className="grid grid-cols-1 lg:grid-cols-[0.9fr_1.4fr]">
                        <div className="p-6 sm:p-8 border-b lg:border-b-0 lg:border-r border-white/10">
                            <div className="font-mono text-xs tracking-widest text-[#ccff00]">// EDITOR WORKFLOW</div>
                            <h2 className="mt-4 font-heading text-4xl md:text-5xl leading-none tracking-wider">
                                BUILT AROUND YOUR EDITING RULES.
                                <br />
                                REVIEWABLE BY DESIGN.
                            </h2>
                            <p className="mt-5 text-sm text-white/60 leading-6">
                        Klipped is built for creators who want their own references, pacing rules, and edit decisions visible before anything gets rendered.
                            </p>
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2">
                            {WORKFLOW_CAPABILITIES.map((item, index) => {
                                const borderClass = [
                                    index < WORKFLOW_CAPABILITIES.length - 1 ? "border-b" : "",
                                    index % 2 === 0 ? "sm:border-r" : "",
                                    index >= WORKFLOW_CAPABILITIES.length - 2 ? "sm:border-b-0" : "",
                                ].filter(Boolean).join(" ");
                                return (
                                    <article key={item.title} className={`p-5 border-white/10 ${borderClass}`}>
                                        <div className="font-mono text-[11px] uppercase text-[#ccff00] leading-5">{item.title}</div>
                                        <p className="mt-2 text-sm text-white/78 leading-6">{item.detail}</p>
                                    </article>
                                );
                            })}
                        </div>
                    </div>
                </section>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    {PLANS.map((plan) => {
                        const billingReady = Boolean(subscription) && !subscriptionLoading && !subscriptionError;
                        const current = activePlan === plan.id && subscription?.has_billing_subscription;
                        const checkoutLocked = !plan.contact && (!billingReady || Boolean(loadingPlan) || billingKnownDisabled);
                        return (
                            <section key={plan.id} className={`panel p-6 sm:p-7 flex flex-col min-h-[620px] ${plan.featured ? "border-[#ccff00]/70 shadow-[0_0_0_1px_rgba(204,255,0,0.25)]" : ""}`}>
                                <div className="flex items-center justify-between gap-4">
                                    <h2 className="font-heading text-3xl tracking-wider">{plan.name.toUpperCase()}</h2>
                                    {plan.featured && <Sparkles className="w-5 h-5 text-[#ccff00]" />}
                                    {plan.contact && <Crown className="w-5 h-5 text-[#ccff00]" />}
                                </div>
                                <p className="mt-4 text-sm text-white/60 leading-6 min-h-[4.5rem]">{plan.summary}</p>
                                <div className="mt-6 flex items-end gap-2">
                                    <span className="font-heading text-5xl text-[#ccff00]">{plan.price}</span>
                                    <span className="font-mono text-xs text-white/50 mb-2 uppercase">{plan.suffix}</span>
                                </div>
                                {plan.billing && <div className="mt-2 font-mono text-xs text-white/50">{plan.billing.toUpperCase()}</div>}
                                <div className="mt-5 grid gap-2">
                                    <div className="border border-[#ccff00]/30 bg-[#ccff00]/10 px-3 py-3 font-mono text-xs text-[#ccff00] uppercase flex items-center gap-2">
                                        <Zap className="w-4 h-4 shrink-0" /> {plan.kicker}
                                    </div>
                                    <div className="border border-white/10 px-3 py-3 font-mono text-xs text-white/55 uppercase">{plan.retention}</div>
                                </div>
                                {plan.stats && (
                                    <div className="mt-4 grid grid-cols-2 gap-2">
                                        {plan.stats.map((stat) => (
                                            <div key={stat} className="border border-white/10 px-3 py-2 font-mono text-[11px] text-white/60 uppercase">{stat}</div>
                                        ))}
                                    </div>
                                )}
                                {plan.clipExtraction && <div className="mt-4 border border-white/10 px-3 py-3 font-mono text-[11px] text-white/65 uppercase" data-testid={`clip-extraction-${plan.id}`}>{plan.clipExtraction}</div>}
                                <ul className="mt-6 space-y-3 flex-1 text-sm text-white/75">
                                    {plan.features.map((feature) => (
                                        <li key={feature} className="flex gap-3 leading-5">
                                            <Check className="w-4 h-4 shrink-0 mt-0.5 text-[#ccff00]" />
                                            <span>{feature}</span>
                                        </li>
                                    ))}
                                </ul>
                                <button
                                    className={current ? "btn-ghost w-full justify-center mt-8" : "btn-brand w-full justify-center mt-8"}
                                    disabled={current || checkoutLocked}
                                    onClick={() => plan.contact ? contactSales() : subscription?.has_billing_subscription ? manageBilling() : startCheckout(plan.id)}
                                >
                                    {current ? "CURRENT PLAN" : plan.contact ? "CONTACT US" : loadingPlan === plan.id || loadingPlan === "portal" ? <Loader2 className="w-4 h-4 animate-spin" /> : !billingReady ? "LOAD BILLING FIRST" : subscription?.has_billing_subscription ? "MANAGE IN STRIPE" : "START CHECKOUT"}
                                </button>
                                {!plan.contact && <div className="mt-3 text-center font-mono text-[11px] text-white/40">SECURE STRIPE CHECKOUT</div>}
                            </section>
                        );
                    })}
                </div>

                {subscription?.billing_enabled && subscription?.has_billing_subscription && (
                    <button className="btn-ghost mt-8" disabled={loadingPlan === "portal"} onClick={manageBilling}>
                        {loadingPlan === "portal" ? <Loader2 className="w-4 h-4 animate-spin" /> : "MANAGE BILLING"}
                    </button>
                )}
                {billingKnownDisabled && (
                    <div className="mt-8 font-mono text-xs text-[#ff5a5a]">BILLING IS NOT CONFIGURED YET.</div>
                )}

                <section className="mt-10 border border-white/10 bg-white/[0.025] p-5 sm:p-6">
                    <div className="font-mono text-xs tracking-widest text-white/40">// ROADMAP, NOT IN CURRENT CHECKOUT</div>
                    <p className="mt-3 text-sm text-white/55 leading-6">
                        These capabilities are product directions, not promises included in today&apos;s plans.
                    </p>
                    <div className="mt-4 grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
                        {ROADMAP_FEATURES.map((feature) => (
                            <div key={feature} className="border border-white/10 px-3 py-2 text-xs text-white/45 font-mono uppercase">
                                {feature}
                            </div>
                        ))}
                    </div>
                </section>
            </div>
        </main>
    );
}
