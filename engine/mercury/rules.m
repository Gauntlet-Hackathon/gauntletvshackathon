%---------------------------------------------------------------------------%
% E7 (#66): the decision rules (contracts/engine_rules.md, docs/plan/04-decision-rubric.md)
% ported from the Python fallback (engine/rescue_engine/fallback/) to Mercury.
%
% Every input is fully typed before it gets here (json_io.m does the parsing), so every
% enum in the contract is a Mercury type and every switch on it is exhaustive: adding a
% hazard, tier, rule or abstain reason without handling it everywhere is a compile error.
% decide/1 is a det function: exactly one engine_output per engine_input.
%---------------------------------------------------------------------------%

:- module rules.
:- interface.

:- import_module bool.
:- import_module list.
:- import_module maybe.

%---------------------------------------------------------------------------%
% Contract enums.

:- type location_type
    --->    landmark
    ;       address
    ;       coords
    ;       none.

:- type hazard
    --->    rising_water
    ;       on_roof
    ;       injury
    ;       medical_device_no_power
    ;       fire
    ;       trapped.

:- type vulnerable
    --->    child
    ;       elderly
    ;       disabled
    ;       medical
    ;       pregnant.

:- type need_kind
    --->    food
    ;       water
    ;       meds.

:- type request_kind
    --->    rescue
    ;       evacuation
    ;       welfare_check
    ;       supply
    ;       other.

:- type tier
    --->    p1
    ;       p2
    ;       p3.

:- type outcome
    --->    prioritized
    ;       abstain
    ;       not_a_request
    ;       duplicate.

:- type abstain_reason
    --->    low_confidence_request
    ;       no_usable_location
    ;       low_confidence_hazard
    ;       conflicting_counts
    ;       possible_hoax.

    % Step 3 tier rules, in the order they are tried (see tier_rules/0).
:- type tier_rule
    --->    r_p1_01
    ;       r_p1_02
    ;       r_p1_03
    ;       r_p1_04
    ;       r_p1_05
    ;       r_p2_01
    ;       r_p2_02
    ;       r_p2_03
    ;       r_p2_04
    ;       r_p3_01
    ;       r_p3_02
    ;       r_p3_03
    ;       r_p3_04.

:- type rule_id
    --->    r_nar_01
    ;       r_nar_02
    ;       r_abs_req
    ;       r_abs_loc
    ;       r_abs_hoax
    ;       r_abs_cnt
    ;       r_abs_haz
    ;       r_dup_01
    ;       tier_rule(tier_rule).

:- type question
    --->    q_location
    ;       q_request
    ;       q_hazard
    ;       q_confirm
    ;       q_hoax
    ;       q_counts.

%---------------------------------------------------------------------------%
% engine_input, typed. Raw text never reaches the engine; text fields of the
% extracted payload are dropped by json_io.m because no rule reads them.

:- type conf_item(T)
    --->    conf_item(item_kind :: T, item_conf :: float).

:- type need
    --->    need(need_kind :: need_kind, hours_without :: maybe(float)).

:- type extracted
    --->    extracted(
                location_type       :: location_type,
                location_conf       :: float,
                people_count        :: maybe(float),
                vulnerable          :: list(conf_item(vulnerable)),
                hazards             :: list(conf_item(hazard)),
                is_stale_post       :: bool,
                stale_conf          :: float,
                is_request          :: bool,
                request_conf        :: float,
                needs               :: list(need),
                request_kind        :: request_kind
            ).

:- type candidate
    --->    candidate(
                cand_request_id     :: string,
                cand_received_at    :: string,
                cand_people_count   :: maybe(float),
                cand_contact_match  :: bool,
                cand_tier           :: maybe(tier)
            ).

:- type thresholds
    --->    thresholds(
                request_confidence_min      :: float,
                stale_confidence_min        :: float,
                hoax_marker_min             :: float,
                hoax_request_confidence_max :: float,
                location_confidence_min     :: float,
                hazard_firm_min             :: float,
                hazard_uncertain_min        :: float,
                hazard_weak_min             :: float,
                need_hours_over             :: float,
                duplicate_window_hours      :: float
            ).

:- type context
    --->    context(
                duplicate_candidates    :: list(candidate),
                has_street_list_hits    :: bool,
                hoax_marker_hits        :: float,
                thresholds              :: thresholds
            ).

:- type engine_input
    --->    engine_input(
                extracted   :: extracted,
                context     :: context
            ).

:- type engine_output
    --->    engine_output(
                outcome             :: outcome,
                priority_tier       :: maybe(tier),
                rule_id             :: rule_id,
                duplicate_of        :: maybe(string),
                abstain_reason      :: maybe(abstain_reason),
                follow_up_question  :: maybe(question)
            ).

%---------------------------------------------------------------------------%

:- func default_thresholds = thresholds.

    % Same placeholder the Python fallback emits (rules.RULES_VERSION).
:- func rules_version = string.

:- func decide(engine_input) = engine_output.

:- func outcome_string(outcome) = string.
:- func tier_string(tier) = string.
:- func rule_id_string(rule_id) = string.
:- func abstain_reason_string(abstain_reason) = string.
:- func question_string(question) = string.

%---------------------------------------------------------------------------%

:- implementation.

:- import_module float.
:- import_module int.
:- import_module set.
:- import_module string.

default_thresholds = thresholds(0.6, 0.6, 1.0, 0.85, 0.6, 0.75, 0.5, 0.25, 24.0, 12.0).

rules_version = "rules@0000000+bin:00000000".

%---------------------------------------------------------------------------%

decide(engine_input(Ex, Ctx)) = Out :-
    Th = Ctx ^ thresholds,
    ( if check_request(Ex, Th, RequestOut) then
        Out = RequestOut
    else if check_hoax(Ex, Ctx, Th) then
        Out = abstain_output(r_abs_hoax, possible_hoax, q_hoax)
    else if not usable_location(Ex, Ctx, Th) then
        Out = abstain_output(r_abs_loc, no_usable_location, q_location)
    else
        TierOut = check_tier(Ex, Th),
        Candidates = Ctx ^ duplicate_candidates,
        ( if
            TierOut ^ outcome = prioritized,
            TierOut ^ priority_tier = yes(OwnTier),
            Candidates = [First | Rest]
        then
            Out = check_duplicates(Ex, OwnTier, First, Rest)
        else
            Out = TierOut
        )
    ).

:- func abstain_output(rule_id, abstain_reason, question) = engine_output.

abstain_output(Rule, Reason, Question) =
    engine_output(abstain, no, Rule, no, yes(Reason), yes(Question)).

%---------------------------------------------------------------------------%
% Step 1: request check.

:- pred check_request(extracted::in, thresholds::in, engine_output::out) is semidet.

check_request(Ex, Th, Out) :-
    ( if Ex ^ request_conf < Th ^ request_confidence_min then
        Out = abstain_output(r_abs_req, low_confidence_request, q_request)
    else if Ex ^ is_request = no then
        Out = engine_output(not_a_request, no, r_nar_01, no, no, no)
    else
        Ex ^ is_stale_post = yes,
        Ex ^ stale_conf >= Th ^ stale_confidence_min,
        Out = engine_output(not_a_request, no, r_nar_02, no, no, no)
    ).

%---------------------------------------------------------------------------%
% Hoax: markers plus a shaky is_request route to a human, never discard.

:- pred check_hoax(extracted::in, context::in, thresholds::in) is semidet.

check_hoax(Ex, Ctx, Th) :-
    Ctx ^ hoax_marker_hits >= Th ^ hoax_marker_min,
    Ex ^ request_conf < Th ^ hoax_request_confidence_max.

%---------------------------------------------------------------------------%
% Step 2: location check.

:- pred usable_location(extracted::in, context::in, thresholds::in) is semidet.

usable_location(Ex, Ctx, Th) :-
    Type = Ex ^ location_type,
    Ex ^ location_conf >= Th ^ location_confidence_min,
    (
        Type = landmark,
        Ctx ^ has_street_list_hits = yes
    ;
        Type = address
    ;
        Type = coords
    ).
    % Type = none is never usable: the switch above has no arm for it.

%---------------------------------------------------------------------------%
% Step 3: tier, with uncertainty escalation. A signal is never dropped to
% lower a tier: a weak signal that would raise it means abstain.

:- type signals
    --->    signals(
                sig_hazards         :: set(hazard),
                sig_vulnerable      :: set(vulnerable),
                sig_needs           :: list(need),
                sig_request_kind    :: request_kind,
                sig_need_hours_over :: float
            ).

:- func check_tier(extracted, thresholds) = engine_output.

check_tier(Ex, Th) = Out :-
    match_tier(signals_at(Ex, Th ^ hazard_firm_min, Th), Firm, _),
    match_tier(signals_at(Ex, Th ^ hazard_uncertain_min, Th), Uncertain, Rule),
    match_tier(signals_at(Ex, Th ^ hazard_weak_min, Th), Weak, _),
    ( if higher(Weak, Uncertain) then
        Out = abstain_output(r_abs_haz, low_confidence_hazard, q_hazard)
    else
        ( if higher(Uncertain, Firm) then
            Question = yes(q_confirm)
        else
            Question = no
        ),
        Out = engine_output(prioritized, yes(Uncertain), tier_rule(Rule), no, no, Question)
    ).

:- func signals_at(extracted, float, thresholds) = signals.

signals_at(Ex, MinConf, Th) =
    signals(kinds_at(Ex ^ hazards, MinConf), kinds_at(Ex ^ vulnerable, MinConf),
        Ex ^ needs, Ex ^ request_kind, Th ^ need_hours_over).

:- func kinds_at(list(conf_item(T)), float) = set(T).

kinds_at(Items, MinConf) = set.list_to_set(Kinds) :-
    list.filter_map(
        ( pred(conf_item(K, C)::in, K::out) is semidet :- C >= MinConf ),
        Items, Kinds).

:- func tier_rules = list(tier_rule).

tier_rules = [r_p1_01, r_p1_02, r_p1_03, r_p1_04, r_p1_05,
    r_p2_01, r_p2_02, r_p2_03, r_p2_04,
    r_p3_01, r_p3_02, r_p3_03, r_p3_04].

    % The first tier rule that matches. R-P3-04 always matches, so the
    % fallback arm below is only there to keep the predicate det.
    %
:- pred match_tier(signals::in, tier::out, tier_rule::out) is det.

match_tier(S, Tier, Rule) :-
    ( if list.find_first_match(rule_matches(S), tier_rules, Found) then
        Rule = Found
    else
        Rule = r_p3_04
    ),
    Tier = rule_tier(Rule).

:- pred rule_matches(signals::in, tier_rule::in) is semidet.

rule_matches(S, Rule) :-
    H = S ^ sig_hazards,
    V = S ^ sig_vulnerable,
    K = S ^ sig_request_kind,
    (
        Rule = r_p1_01,
        set.member(rising_water, H),
        ( set.member(on_roof, H) ; set.member(trapped, H) )
    ;
        Rule = r_p1_02,
        set.member(injury, H)
    ;
        Rule = r_p1_03,
        set.member(medical_device_no_power, H)
    ;
        Rule = r_p1_04,
        set.member(rising_water, H),
        set.member(Vuln, V),
        p1_vulnerable(Vuln) = yes
    ;
        Rule = r_p1_05,
        set.member(fire, H)
    ;
        Rule = r_p2_01,
        ( set.member(trapped, H) ; set.member(on_roof, H) )
    ;
        Rule = r_p2_02,
        not set.is_empty(V)
    ;
        Rule = r_p2_03,
        list.member(Need, S ^ sig_needs),
        Need = need(_, yes(Hours)),
        Hours > S ^ sig_need_hours_over
    ;
        Rule = r_p2_04,
        ( not set.is_empty(H) ; K = rescue )
    ;
        Rule = r_p3_01,
        K = evacuation
    ;
        Rule = r_p3_02,
        K = welfare_check
    ;
        Rule = r_p3_03,
        K = supply
    ;
        Rule = r_p3_04
    ).

:- func p1_vulnerable(vulnerable) = bool.

p1_vulnerable(child) = yes.
p1_vulnerable(elderly) = yes.
p1_vulnerable(disabled) = yes.
p1_vulnerable(medical) = no.
p1_vulnerable(pregnant) = no.

:- func rule_tier(tier_rule) = tier.

rule_tier(r_p1_01) = p1.
rule_tier(r_p1_02) = p1.
rule_tier(r_p1_03) = p1.
rule_tier(r_p1_04) = p1.
rule_tier(r_p1_05) = p1.
rule_tier(r_p2_01) = p2.
rule_tier(r_p2_02) = p2.
rule_tier(r_p2_03) = p2.
rule_tier(r_p2_04) = p2.
rule_tier(r_p3_01) = p3.
rule_tier(r_p3_02) = p3.
rule_tier(r_p3_03) = p3.
rule_tier(r_p3_04) = p3.

:- func tier_rank(tier) = int.

tier_rank(p1) = 1.
tier_rank(p2) = 2.
tier_rank(p3) = 3.

    % higher(A, B): tier A is more urgent than tier B.
    %
:- pred higher(tier::in, tier::in) is semidet.

higher(A, B) :-
    tier_rank(A) < tier_rank(B).

%---------------------------------------------------------------------------%
% Steps 4-5: duplicates and contradictions (candidates arrive oldest first).

:- func check_duplicates(extracted, tier, candidate, list(candidate)) = engine_output.

check_duplicates(Ex, OwnTier, First, Rest) = Out :-
    Candidates = [First | Rest],
    ( if conflicting_counts(Ex, Candidates) then
        Out = abstain_output(r_abs_cnt, conflicting_counts, q_counts)
    else
        GroupTier = list.foldl(group_tier, Candidates, OwnTier),
        Earliest = list.foldl(earlier, Rest, First),
        Out = engine_output(duplicate, yes(GroupTier), r_dup_01,
            yes(Earliest ^ cand_request_id), no, no)
    ).

:- pred conflicting_counts(extracted::in, list(candidate)::in) is semidet.

conflicting_counts(Ex, Candidates) :-
    Ex ^ people_count = yes(Mine),
    list.member(C, Candidates),
    C ^ cand_contact_match = yes,
    C ^ cand_people_count = yes(Theirs),
    Theirs \= Mine.

:- func group_tier(candidate, tier) = tier.

group_tier(C, Best) =
    ( if C ^ cand_tier = yes(T), higher(T, Best) then T else Best ).

    % Strictly earlier only, so input order breaks ties (like Python's min).
    %
:- func earlier(candidate, candidate) = candidate.

earlier(C, Best) =
    ( if compare((<), C ^ cand_received_at, Best ^ cand_received_at) then C else Best ).

%---------------------------------------------------------------------------%
% Strings on the wire.

outcome_string(prioritized) = "prioritized".
outcome_string(abstain) = "abstain".
outcome_string(not_a_request) = "not_a_request".
outcome_string(duplicate) = "duplicate".

tier_string(p1) = "P1".
tier_string(p2) = "P2".
tier_string(p3) = "P3".

rule_id_string(r_nar_01) = "R-NAR-01".
rule_id_string(r_nar_02) = "R-NAR-02".
rule_id_string(r_abs_req) = "R-ABS-REQ".
rule_id_string(r_abs_loc) = "R-ABS-LOC".
rule_id_string(r_abs_hoax) = "R-ABS-HOAX".
rule_id_string(r_abs_cnt) = "R-ABS-CNT".
rule_id_string(r_abs_haz) = "R-ABS-HAZ".
rule_id_string(r_dup_01) = "R-DUP-01".
rule_id_string(tier_rule(R)) = tier_rule_string(R).

:- func tier_rule_string(tier_rule) = string.

tier_rule_string(r_p1_01) = "R-P1-01".
tier_rule_string(r_p1_02) = "R-P1-02".
tier_rule_string(r_p1_03) = "R-P1-03".
tier_rule_string(r_p1_04) = "R-P1-04".
tier_rule_string(r_p1_05) = "R-P1-05".
tier_rule_string(r_p2_01) = "R-P2-01".
tier_rule_string(r_p2_02) = "R-P2-02".
tier_rule_string(r_p2_03) = "R-P2-03".
tier_rule_string(r_p2_04) = "R-P2-04".
tier_rule_string(r_p3_01) = "R-P3-01".
tier_rule_string(r_p3_02) = "R-P3-02".
tier_rule_string(r_p3_03) = "R-P3-03".
tier_rule_string(r_p3_04) = "R-P3-04".

abstain_reason_string(low_confidence_request) = "low_confidence_request".
abstain_reason_string(no_usable_location) = "no_usable_location".
abstain_reason_string(low_confidence_hazard) = "low_confidence_hazard".
abstain_reason_string(conflicting_counts) = "conflicting_counts".
abstain_reason_string(possible_hoax) = "possible_hoax".

question_string(q_location) =
    "What's the street address, or the nearest cross streets or landmark?".
question_string(q_request) =
    "Is this a request for help? If so, what do you need and where are you?".
question_string(q_hazard) =
    "Can you confirm what is happening right now: is water rising, " ++
    "is anyone injured, trapped or in danger?".
question_string(q_confirm) =
    "Please confirm: is anyone in immediate danger (rising water, injury, " ++
    "fire, trapped), and is a child, elderly or disabled person with you?".
question_string(q_hoax) =
    "Can you confirm this request is real and tell us exactly what help you need?".
question_string(q_counts) =
    "We have different counts for this location. How many people need help right now?".

%---------------------------------------------------------------------------%
:- end_module rules.
%---------------------------------------------------------------------------%
