# Product

## Register

product

## Platform

web

## Users

Platform, cloud, and FinOps engineers who **self-host** CloudWarden and live inside it. They onboard Azure, AWS, and GCP accounts, author and bind governance-as-code policies, run the collection/analysis pipeline, read cost and compliance posture, and approve or execute remediations. Their context is operational: they arrive with a task — investigate a spend spike, chase a compliance violation, sign off on a right-sizing action — and want the tool to get out of the way. They are fluent in cloud consoles and terminals; density and precision are features, not friction. Leadership may occasionally read a dashboard, but the UI is built for the operator, not the viewer.

## Product Purpose

CloudWarden is a single self-hostable control plane for multi-cloud governance and FinOps. It turns raw cloud inventory, cost, and telemetry into clear spend visualizations, explainable right-sizing and shutdown recommendations, and a guarded path to act on them — alongside governance-as-code policy authoring, binding, execution, and compliance posture across Azure, AWS, and GCP. Success is an operator who can do all three core jobs with confidence: safely act on remediations where real money and live resources are at stake, trust the cost and savings numbers enough to report on them, and author and manage governance that provably does what it says. No single job dominates; it is the full control plane.

## Positioning

Governance-as-code **and** FinOps in one self-hostable stack across Azure, AWS, and GCP — a control plane you own and can inspect, not a SaaS you rent.

## Brand Personality

Precise, trustworthy, dense. This is a serious control plane, and the interface should feel like one: information-rich, exact, quiet. The best compliment is that the tool disappears into the operator's task. Voice is direct and technical — it names things by their real names, states figures with their caveats, and never dresses up a number it can't stand behind.

## Anti-references

Not generic AI/SaaS dashboard slop — no gradient hero-metric templates, no endless identical icon-card grids, no decorative charts that carry no information. Not a heavy enterprise-legacy console in the mold of the Azure Portal — no nested chrome, blade-upon-blade navigation, or sluggish clutter. Not playful or consumer-styled — no mascots, bright illustration, or casual copy; this is an operations tool. And not a sparse, marketing-flavored dashboard that spends the viewport on whitespace and a few vanity numbers while hiding the data operators actually came for.

## Design Principles

The tool disappears into the task. Density serves the operator; there is no chrome for its own sake, no orchestrated load sequence, no ceremony between the user and the data.

Trust is earned by transparency. Every number, recommendation, and action shows its basis — source, rationale, confidence, caveats, and an audit trail. Estimates are labeled as estimates. Nothing is a black box.

Guard the dangerous path. Anything that spends money or deletes resources is dry-run-first, guardrailed, and reversible-by-review, and the interface makes the safe path the obvious one.

One vocabulary across clouds. Azure, AWS, and GCP read as a single system; the provider is a dimension you filter by, not three different products bolted together.

Familiarity over invention. Standard affordances — tables, tabs, navigation, badges, forms — done exactly right. In an operations tool, surprise is a bug.

## Accessibility & Inclusion

Target WCAG 2.2 AA. Body text holds ≥4.5:1 contrast against its surface (a real constraint on the dark theme's muted grays); every interactive element is fully keyboard-operable with a visible focus state; and all motion has a `prefers-reduced-motion` alternative. Status is a recurring signal here (compliant/violation, risk and confidence levels, run success/failure) — pairing color with an icon or label wherever state is conveyed is encouraged so it survives for colorblind operators, even though a formal colorblind-safe audit is not a release gate.
