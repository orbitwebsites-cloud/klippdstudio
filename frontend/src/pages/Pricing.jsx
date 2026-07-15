import { useEffect, useState } from "react";
import { Check, Crown, Loader2, ShieldCheck, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { apiErrorMessage, createBillingPortal, createCheckout, getSubscription } from "@/lib/klipApi";

const PLANS = [
    {
        id: "basic",
        name: "Basic",
        price: "$19",
        retention: "7-day project retention",
        features: ["AI transcript and filler removal", "Captions, B-roll, zooms, and SFX", "Watermark-free MP4 exports"],
    },
    {
        id: "pro",
        name: "Pro",
        price: "$49",
        retention: "30-day project retention",
        featured: true,
        features: ["Everything in Basic", "Creator DNA and edit-chat", "Viral clip generation"],
    },
    {
        id: "elite",
        name: "Elite",
        price: "$149",
        retention: "No automatic deletion",
        features: ["Everything in Pro", "Persistent project archive", "Premium workflow features as they launch"],
    },
];

export default function Pricing() {
    const [subscription, setSubscription] = useState(null);
    const [loadingPlan, setLoadingPlan] = useState("");

    const refresh = () => getSubscription().then(setSubscription).catch(() => {});

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

    const activePlan = subscription?.plan || "basic";
    return (
        <main className="min-h-[calc(100vh-72px)] px-6 md:px-16 py-14 md:py-20">
            <div className="max-w-6xl mx-auto">
                <div className="font-mono text-xs tracking-widest text-[#ccff00] mb-5">KLIPPED STUDIO PLANS</div>
                <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-5 mb-12">
                    <h1 className="font-heading text-5xl md:text-7xl leading-[0.9] tracking-wider">MAKE MORE.<br />KEEP WHAT MATTERS.</h1>
                    <div className="font-mono text-xs text-white/50 max-w-sm leading-6">
                        Your active workspace plan is <span className="text-white">{activePlan.toUpperCase()}</span>.
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    {PLANS.map((plan) => {
                        const current = activePlan === plan.id && subscription?.has_billing_subscription;
                        return (
                            <section key={plan.id} className={`panel p-7 flex flex-col min-h-[390px] ${plan.featured ? "border-[#ccff00]/70" : ""}`}>
                                <div className="flex items-center justify-between gap-4">
                                    <h2 className="font-heading text-3xl tracking-wider">{plan.name.toUpperCase()}</h2>
                                    {plan.featured && <Sparkles className="w-5 h-5 text-[#ccff00]" />}
                                </div>
                                <div className="mt-8 flex items-end gap-2">
                                    <span className="font-heading text-5xl text-[#ccff00]">{plan.price}</span>
                                    <span className="font-mono text-xs text-white/50 mb-2">/ MONTH</span>
                                </div>
                                <div className="mt-4 font-mono text-xs text-white/60">{plan.retention.toUpperCase()}</div>
                                <ul className="mt-8 space-y-4 flex-1 text-sm text-white/75">
                                    {plan.features.map((feature) => (
                                        <li key={feature} className="flex gap-3"><Check className="w-4 h-4 text-[#ccff00] shrink-0" />{feature}</li>
                                    ))}
                                </ul>
                                <button
                                    className={current ? "btn-ghost w-full justify-center mt-8" : "btn-brand w-full justify-center mt-8"}
                                    disabled={current || Boolean(loadingPlan) || !subscription?.billing_enabled}
                                    onClick={() => subscription?.has_billing_subscription ? manageBilling() : startCheckout(plan.id)}
                                >
                                    {loadingPlan === plan.id || loadingPlan === "portal" ? <Loader2 className="w-4 h-4 animate-spin" /> : current ? "CURRENT PLAN" : subscription?.has_billing_subscription ? "MANAGE IN STRIPE" : "CHOOSE PLAN"}
                                </button>
                            </section>
                        );
                    })}
                </div>

                <section className="mt-4 border border-white/10 px-7 py-8 flex flex-col md:flex-row md:items-center md:justify-between gap-6">
                    <div>
                        <div className="flex items-center gap-2 font-heading text-2xl tracking-wider"><Crown className="w-5 h-5 text-[#ccff00]" /> ENTERPRISE</div>
                        <p className="mt-2 text-sm text-white/60 max-w-2xl">Starts at $120 per seat monthly. Qualified organizations receive a custom agreement and tailored rollout.</p>
                    </div>
                    <div className="font-mono text-xs text-white/50 flex items-center gap-2"><ShieldCheck className="w-4 h-4" /> QUALIFICATION REQUIRED</div>
                </section>

                {subscription?.billing_enabled && subscription?.has_billing_subscription && (
                    <button className="btn-ghost mt-8" disabled={loadingPlan === "portal"} onClick={manageBilling}>
                        {loadingPlan === "portal" ? <Loader2 className="w-4 h-4 animate-spin" /> : "MANAGE BILLING"}
                    </button>
                )}
                {!subscription?.billing_enabled && subscription && (
                    <div className="mt-8 font-mono text-xs text-[#ff5a5a]">BILLING IS NOT CONFIGURED YET.</div>
                )}
            </div>
        </main>
    );
}
