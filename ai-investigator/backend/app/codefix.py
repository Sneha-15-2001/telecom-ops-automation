"""Permanent code-fix recommender (Phase 17).

Maps the diagnosed defect family to a concrete change proposal: service, area
(class/method), problem, before/after logic sketch, tests, risks, deploy notes.
Proposal only — a human merges, reviews and deploys. Also embeds under
investigator 'fix_hint' families so rule mode and LLM mode agree on direction.
"""

DISCLAIMER = ("PROPOSAL ONLY — human review, tests and deployment required. "
              "The investigator cannot commit or deploy code.")


def _has(journey, *events):
    evs = [j["event"] for j in journey]
    return any(e in evs for e in events)


def propose(inv: dict, incident_text: str = "") -> dict:
    suspect = (inv.get("suspect") or {}).get("service") or ""
    signal = (((inv.get("suspect") or {}).get("signal") or "") + " " + incident_text).upper()
    journey = inv.get("journey", [])
    db = inv.get("db_evidence") or {}
    # widen the match surface: journey events + every last_error in evidence
    blob_parts = [signal]
    blob_parts += [str(j.get("event", "")) for j in journey]
    for rows in db.values():
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, dict):
                    blob_parts.append(str(r.get("last_error") or ""))
                    blob_parts.append(str(r.get("comment") or ""))
    blob = " ".join(blob_parts).upper()
    fixes = []

    def add(title, service, area, problem, before, after, tests, risks, deploy):
        fixes.append({"title": title, "service": service, "area": area,
                      "problem": problem, "before": before, "after": after,
                      "tests": tests, "risks": risks, "deploy_notes": deploy})

    # 1. Expiry path rolls back its own fix (INC-10110 family).
    if "inventory-service" in suspect and ("EXPIRED" in signal or _has(journey, "RESOURCE_RESERVED")):
        add("Commit expiry compensation in its own unit",
            "inventory-service", "ReservationService.requireActive / confirm",
            "requireActive() marks EXPIRED then throws; the throw rolls back the marking AND any "
            "resource release, so the leak is permanent and every retry fails identically.",
            "requireActive(): set EXPIRED → throw → (rollback erases everything)",
            "Split into two transactions: 1) mark EXPIRED + commit; 2) in a REQUIRES_NEW unit, "
            "free the resource (→ AVAILABLE, clear orderId) + history event. Or a sweeper job over "
            "past-TTL ACTIVE holds doing the same.",
            ["reserve ttl=1 → wait → confirm returns 400 AND resource is AVAILABLE",
             "retry-after-expiry is idempotent", "sweeper dry-run counts exactly the leaked set"],
            "Releasing stock other flows may have re-reserved — guard with status check + row version.",
            "Backward compatible; deploy off-peak; watch RESERVED-without-ACTIVE-hold metric to zero.")

    # 2. No idempotency on notify (INC-10105 family).
    if "notification-service" in suspect or "DUPLICATE" in signal:
        add("Idempotency key on (order, event)",
            "notification-service", "NotificationService.notifyEvent",
            "Retried notify steps insert duplicate rows; both get sent.",
            "notifyEvent(): always INSERT a new notification",
            "UNIQUE(order_id, template_code, dedupe_window) or idempotency-key header table: "
            "second call with same key returns the first row instead of inserting.",
            ["double-notify returns one row", "different events still insert twice", "key TTL expiry re-allows"],
            "Backfill dedupe for historical duplicates before adding the constraint.",
            "Add nullable column first, backfill, then enforce UNIQUE in a follow-up release.")

    # 3. N+1 on order listing (INC-10108 family).
    if ("order-service" in suspect and "N+1" in signal) or "N+1" in signal:
        add("Kill the N+1 on order items",
            "order-service", "OrderRepository + OrderMapper.toResponse",
            "Page mapping calls findByOrderIdOrderByIdAsc per order: 1+N queries, linear slowdown.",
            "page.map(o -> toResponse(o, items.findByOrderId(o)))  // N queries",
            "@EntityGraph(attributePaths='items') on the page query, or one "
            "findByOrderIdIn(ids) + in-memory grouping; @BatchSize as belt-and-braces.",
            ["page of 15 fires ≤2 queries", "timeline path covered too", "no over-fetching on huge pages"],
            "Entity graphs change fetch shapes — re-run the full order suite; watch DB CPU.",
            "Safe rolling deploy; measure p95 of GET /api/orders before/after.")

    # 4. Skipped validation step (INC-10101 family): process gap, harden with a guard.
    if "order-service" in suspect and "WITHOUT PAYMENT_VALIDATED" in signal:
        add("Make skipped validation impossible",
            "order-service", "OrderService.submit/fulfill + a scheduled reconciler",
            "Nothing forces payment/validate to run after payment/record — a missed manual step "
            "freezes money and order silently.",
            "record → (hope validate runs) → fulfill allowed from PAYMENT_PENDING only",
            "Guard: fulfill/retry paths re-check latest payment state; plus a 5-minute sweeper that "
            "validates stale PENDING payments and alerts on repeated skips.",
            ["record-without-validate then fulfill → auto-validates or clean FAILED",
             "sweeper dry-run lists exactly the stuck set"],
            "Auto-validation moves money-affecting state — gate behind a feature flag + audit event.",
            "Flag off by default; enable per region; alert on sweeper actions.")

    # 5. Silent promo removal (INC-10109 family).
    if "order-service" in suspect and "PROMOTION" in signal:
        add("Name the promo removal + offer re-apply",
            "order-service", "OrderService.modify",
            "modify() clears promoCode/discount silently; support cannot explain the new payable.",
            "modify(): promo=null, discount=0  // no trace",
            "Write an explicit PROMOTION_REMOVED history event with old code+amount, and return a "
            "warning payload so the UI prompts one-click re-apply (recompute against new items).",
            ["modify keeps an audit entry", "re-apply recomputes (not copies) the discount"],
            "History-only change; safe. UI prompt is a separate frontend ticket.",
            "No migration; deploy anytime.")

    # 6. Suspended-customer orders (INC-10106 family): process, not code — eligibility gate.
    if "CUSTOMER" in signal or "SUSPENDED" in str(inv.get("entities", {}).get("status_words", [])):
        add("Eligibility pre-check in the sales flow",
            "order-service + customer-service", "Order creation (UX + API)",
            "Sales can open orders for ineligible customers; failure surfaces only at validation, "
            "after promises were made.",
            "create → validate → FAILED (late, embarrassing)",
            "Call customer validate/eligibility BEFORE create (UI disables submit + shows reason); "
            "keep server-side validation as the backstop.",
            ["suspended customer blocked at form with reason", "backstop still fails safely"],
            "UX change needs sales sign-off on wording.",
            "Frontend + copy first; no backend migration.")

    # 7. Provisioning opened without identifiers while stock sits reserved (INC-10112 family).
    if "MISSING_RESOURCE" in blob or "MISSING_MSISDN" in blob or "MISSING_IDENTIFIER" in blob:
        add("Require + auto-attach identifiers on provisioning requests",
            "order-service + provisioning-service", "OrderService.fulfill + ProvisioningService.create",
            "A provisioning request can exist with no msisdn/resourceNumber even when a reservation "
            "for the same order holds exactly what's missing — manual screens don't force the link.",
            "create(provisioning) accepts null identifiers; activate fails late with MISSING_*",
            "fulfill() already auto-attaches; extend the guard to ALL creates: reject BROADBAND/FIBER "
            "without resourceNumber and MOBILE/ESIM/ROAMING without msisdn at create time (400 with "
            "the missing field named), and surface the order's ACTIVE reservations as pick-list candidates.",
            ["broadband create without resource → instant 400 naming the field",
             "fulfill still auto-attaches and succeeds", "manual UI offers the held resources"],
            "Stricter create may block legacy callers that fill identifiers later — add a DRAFT status if needed.",
            "Backward compatible if DRAFT allowed; otherwise a minor breaking change behind a flag.")

    if not fixes:
        fixes.append({
            "title": "No code pattern matched — capture first",
            "service": suspect or "unknown", "area": "n/a",
            "problem": "Evidence too thin to name a defect family with confidence.",
            "before": "n/a", "after": "Add focused logging/IDs around the suspect step, re-run, re-investigate.",
            "tests": ["reproduction script asserting the failure"], "risks": "None — observation only.",
            "deploy_notes": "None."})

    return {"disclaimer": DISCLAIMER, "fixes": fixes}
