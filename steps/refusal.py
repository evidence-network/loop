"""Which refusals of a subject an advertised policy answers are a breach, which cannot be judged and which
blame the request. Shared by step 07 (its asks) and step 12 (its probe), so both read one reason table."""

# Why a node refuses, against what its advertised policy answers (Mesh-LLM/capsules-plugin#112, #114): only
# these two reasons withhold an answer the policy promises; under evidence-interop/0 policy_declined and
# not_authorized also cover limits no policy promises past (a full stranger pool, an oversized answer, a full
# replay cache), so a refusal under them cannot be judged; under /1 they are policy (below).
# Anything else, a reason the spec does not name included, is a breach (vectors/aac-evidence-request in
# capsules-plugin). no_such_subject and derivation_unsupported withhold; the reasons below blame the request.
WITHHOLDING_REASONS = ("no_such_subject", "derivation_unsupported")
REQUEST_REASONS = ("coverage_unsatisfiable", "deadline_unmet", "request_malformed")
# retention_expired is right only once the node's retention commitment for the subject has lapsed; inside it the
# refusal breaches the promise (ER §9, ERV A9). No node publishes a retention commitment the step can read, so
# such a refusal cannot be judged and is never counted as answered (evidence-network/loop#60).
RETENTION_REASONS = ("retention_expired",)
RETENTION_ISSUE = "evidence-network/loop#60"
AMBIGUOUS_REASONS = ("policy_declined", "not_authorized")
REFUSAL_ISSUE = "Mesh-LLM/capsules-plugin#114"
# Interop profile evidence-interop/1 (AAC spec/interop-profile-v1.md, Evidence Request -02): a node declines for a
# limit of its own with capacity_exceeded and refuses a requester authentication that fails with
# request_unauthenticated, so under /1 policy_declined and not_authorized are policy only and a refusal under them of
# a subject the policy answers is a breach. A refusal carries no profile: it is read by its request's (IP v1 §6.3).
PROFILE_V0, PROFILE_V1 = "evidence-interop/0", "evidence-interop/1"
LIMIT_REASONS = ("capacity_exceeded",)
V1_REQUEST_REASONS = ("request_unauthenticated",)


def request_profile(request):
    """The identifier a sealed request asks for: its `profile`, or evidence-interop/0 when it names none (IP v1
    §3.3 step 2)."""
    return (request.get("profile") if isinstance(request, dict) else None) or PROFILE_V0


def refusal_verdict(reason, profile=PROFILE_V0):
    """"fail" if a refusal for `reason` of a subject the policy answers breaches the policy (a withholding reason, or
    one the spec does not name), "not_checked" if the reason is one an honest node also sends, "ok" if it blames
    the request. `profile` is the identifier the request asked for (request_profile; every caller holds the
    request in the proof). Under evidence-interop/1 policy_declined and not_authorized are policy, so a breach,
    capacity_exceeded (a limit of the node's own) is not checked and request_unauthenticated blames the request.
    evidence-interop/0 registers neither -02 token, so either answering a /0 request is an unregistered reason, a
    breach, and policy_declined and not_authorized cannot be judged (IP v1 §6.3, §9.1). retention_expired is not
    checked under either profile: it is right only once a retention commitment lapses, and none is readable
    (loop#60)."""
    if reason in REQUEST_REASONS:
        return "ok"
    if reason in RETENTION_REASONS:
        return "not_checked"
    if reason in LIMIT_REASONS or reason in V1_REQUEST_REASONS:
        if profile != PROFILE_V1:
            return "fail"
        return "not_checked" if reason in LIMIT_REASONS else "ok"
    if reason in AMBIGUOUS_REASONS:
        return "fail" if profile == PROFILE_V1 else "not_checked"
    return "fail"


def refusal_not_checked(what, reason):
    if reason in RETENTION_REASONS:
        return {"what": what, "issue": RETENTION_ISSUE,
                "why": f"the refusal's reason {reason} is right only after the node's retention commitment for the "
                       "subject lapses, and the node publishes none the step can read, so it cannot be judged"}
    if reason in LIMIT_REASONS:
        why = f"the refusal's reason {reason} is a limit of the node's own, which no policy promises past"
    else:
        # The text the sealed findings hold (step 08 reproduces them byte for byte).
        why = (f"the refusal's reason {reason} is also sent for limits no policy promises past, "
               "so it cannot be judged against the policy")
    return {"what": what, "why": why, "issue": REFUSAL_ISSUE}
