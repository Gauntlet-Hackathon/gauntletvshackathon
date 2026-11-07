%---------------------------------------------------------------------------%
% E7 (#66): JSON <-> typed engine values for the Mercury engine.
%
% Accepts the same payloads as the Python fallback CLI (rescue_engine.fallback): one
% engine_input object, or a list of them. Fields the Python engine reads with a default
% (context, its members, hazards, needs, time_ref, ...) are optional here too, with the
% same defaults; partial thresholds are merged over the defaults. Enum fields must hold
% one of the contract's values: anything else is an input_error.
%
% Parsing throws input_error(Message) on bad input; mercury_engine.m catches it and
% turns it into the CLI error shape (exit 1, one stderr line, empty stdout).
%---------------------------------------------------------------------------%

:- module json_io.
:- interface.

:- import_module json.
:- import_module list.
:- import_module rules.

:- type input_error
    --->    input_error(string).

:- type payload(T)
    --->    single(T)
    ;       batch(list(T)).

    % Throws input_error/1 when Value is not an engine_input or a list of them.
    %
:- func parse_payload(json.value) = payload(engine_input).

:- func payload_to_json(payload(engine_output)) = json.value.

:- func output_to_json(engine_output) = json.value.

%---------------------------------------------------------------------------%

:- implementation.

:- import_module bool.
:- import_module exception.
:- import_module map.
:- import_module maybe.
:- import_module pair.
:- import_module string.

%---------------------------------------------------------------------------%

parse_payload(Value) = Payload :-
    ( if Value = object(Obj) then
        Payload = single(parse_input(Obj))
    else if
        Value = array(Items),
        list.map(get_object, Items, Objs)
    then
        Payload = batch(list.map(parse_input, Objs))
    else
        Payload = fail_input("input must be an engine_input object or a list of them")
    ).

:- func parse_input(json.object) = engine_input.

parse_input(Obj) = engine_input(parse_extracted(Ex), parse_context(Ctx)) :-
    Ex = req_object(Obj, "extracted"),
    Ctx = opt_object(Obj, "context").

:- func parse_extracted(json.object) = extracted.

parse_extracted(Ex) = Extracted :-
    Location = req_object(Ex, "location"),
    LocType = location_type_from(req_string(Location, "type")),
    LocConf = req_number(Location, "confidence"),
    PeopleCount = opt_number(opt_object(Ex, "people_count"), "value"),
    Vulnerable = list.map(conf_item(vulnerable_from), opt_objects(Ex, "vulnerable")),
    Hazards = list.map(conf_item(hazard_from), opt_objects(Ex, "hazards")),
    TimeRef = opt_object(Ex, "time_ref"),
    IsStale = opt_bool(TimeRef, "is_stale_post"),
    StaleConf = maybe_default(opt_number(TimeRef, "confidence"), 0.0),
    IsRequest = req_object(Ex, "is_request"),
    RequestValue = req_bool(IsRequest, "value"),
    RequestConf = req_number(IsRequest, "confidence"),
    Needs = list.map(parse_need, opt_objects(Ex, "needs")),
    ( if search_value(Ex, "request_kind", KindValue) then
        RequestKind = request_kind_from(as_string("request_kind", KindValue))
    else
        RequestKind = other
    ),
    Extracted = extracted(LocType, LocConf, PeopleCount, Vulnerable, Hazards,
        IsStale, StaleConf, RequestValue, RequestConf, Needs, RequestKind).

:- func conf_item(func(string) = T, json.object) = conf_item(T).

conf_item(KindFrom, Item) =
    conf_item(KindFrom(req_string(Item, "kind")), req_number(Item, "confidence")).

:- func parse_need(json.object) = need.

parse_need(Item) =
    need(need_kind_from(req_string(Item, "kind")), opt_number(Item, "hours_without")).

:- func parse_context(json.object) = rules.context.

parse_context(Ctx) = context(Candidates, HasStreetHits, HoaxHits, Thresholds) :-
    Candidates = list.map(parse_candidate, opt_objects(Ctx, "duplicate_candidates")),
    ( if search_value(Ctx, "street_list_hits", Hits) then
        ( if Hits = array(HitList) then
            HasStreetHits = ( if HitList = [] then no else yes )
        else
            HasStreetHits = fail_input("street_list_hits must be an array")
        )
    else
        HasStreetHits = no
    ),
    HoaxHits = maybe_default(opt_number(Ctx, "hoax_marker_hits"), 0.0),
    Thresholds = parse_thresholds(opt_object(Ctx, "thresholds")).

:- func parse_candidate(json.object) = candidate.

parse_candidate(C) = candidate(
        req_string(C, "request_id"),
        req_string(C, "received_at"),
        opt_number(C, "people_count"),
        opt_bool(C, "contact_match"),
        maybe.map_maybe(tier_from, opt_string(C, "priority_tier"))).

:- func parse_thresholds(json.object) = thresholds.

parse_thresholds(T) = thresholds(
        th(T, "request_confidence_min", D ^ request_confidence_min),
        th(T, "stale_confidence_min", D ^ stale_confidence_min),
        th(T, "hoax_marker_min", D ^ hoax_marker_min),
        th(T, "hoax_request_confidence_max", D ^ hoax_request_confidence_max),
        th(T, "location_confidence_min", D ^ location_confidence_min),
        th(T, "hazard_firm_min", D ^ hazard_firm_min),
        th(T, "hazard_uncertain_min", D ^ hazard_uncertain_min),
        th(T, "hazard_weak_min", D ^ hazard_weak_min),
        th(T, "need_hours_over", D ^ need_hours_over),
        th(T, "duplicate_window_hours", D ^ duplicate_window_hours)) :-
    D = default_thresholds.

:- func th(json.object, string, float) = float.

th(T, Name, Default) = maybe_default(opt_number(T, Name), Default).

%---------------------------------------------------------------------------%
% Enums. Each is an exhaustive mapping back in output_to_json; here an unknown
% string is an input error.

:- func location_type_from(string) = location_type.

location_type_from(S) = ( if location_type_s(T, S) then T else bad_enum("location.type", S) ).

:- pred location_type_s(location_type, string).
:- mode location_type_s(out, in) is semidet.

location_type_s(landmark, "landmark").
location_type_s(address, "address").
location_type_s(coords, "coords").
location_type_s(none, "none").

:- func hazard_from(string) = hazard.

hazard_from(S) = ( if hazard_s(H, S) then H else bad_enum("hazards.kind", S) ).

:- pred hazard_s(hazard, string).
:- mode hazard_s(out, in) is semidet.

hazard_s(rising_water, "rising_water").
hazard_s(on_roof, "on_roof").
hazard_s(injury, "injury").
hazard_s(medical_device_no_power, "medical_device_no_power").
hazard_s(fire, "fire").
hazard_s(trapped, "trapped").

:- func vulnerable_from(string) = vulnerable.

vulnerable_from(S) = ( if vulnerable_s(V, S) then V else bad_enum("vulnerable.kind", S) ).

:- pred vulnerable_s(vulnerable, string).
:- mode vulnerable_s(out, in) is semidet.

vulnerable_s(child, "child").
vulnerable_s(elderly, "elderly").
vulnerable_s(disabled, "disabled").
vulnerable_s(medical, "medical").
vulnerable_s(pregnant, "pregnant").

:- func need_kind_from(string) = need_kind.

need_kind_from(S) = ( if need_kind_s(N, S) then N else bad_enum("needs.kind", S) ).

:- pred need_kind_s(need_kind, string).
:- mode need_kind_s(out, in) is semidet.

need_kind_s(food, "food").
need_kind_s(water, "water").
need_kind_s(meds, "meds").

:- func request_kind_from(string) = request_kind.

request_kind_from(S) = ( if request_kind_s(K, S) then K else bad_enum("request_kind", S) ).

:- pred request_kind_s(request_kind, string).
:- mode request_kind_s(out, in) is semidet.

request_kind_s(rescue, "rescue").
request_kind_s(evacuation, "evacuation").
request_kind_s(welfare_check, "welfare_check").
request_kind_s(supply, "supply").
request_kind_s(other, "other").

:- func tier_from(string) = tier.

tier_from(S) = ( if tier_s(T, S) then T else bad_enum("priority_tier", S) ).

:- pred tier_s(tier, string).
:- mode tier_s(out, in) is semidet.

tier_s(p1, "P1").
tier_s(p2, "P2").
tier_s(p3, "P3").

:- func bad_enum(string, string) = T.
:- mode bad_enum(in, in) = out is erroneous.

bad_enum(Field, S) = fail_input(Field ++ ": unknown value " ++ quoted(S)).

%---------------------------------------------------------------------------%
% Field access. Missing and null are the same thing, as with Python's
% dict.get(...) or default.

:- pred search_value(json.object::in, string::in, json.value::out) is semidet.

search_value(Obj, Name, Value) :-
    map.search(Obj, Name, Value),
    Value \= null.

:- func req_value(json.object, string) = json.value.

req_value(Obj, Name) =
    ( if search_value(Obj, Name, V) then V else fail_input("missing field " ++ quoted(Name)) ).

:- func req_object(json.object, string) = json.object.

req_object(Obj, Name) = as_object(Name, req_value(Obj, Name)).

:- func req_string(json.object, string) = string.

req_string(Obj, Name) = as_string(Name, req_value(Obj, Name)).

:- func req_number(json.object, string) = float.

req_number(Obj, Name) = as_number(Name, req_value(Obj, Name)).

:- func req_bool(json.object, string) = bool.

req_bool(Obj, Name) = as_bool(Name, req_value(Obj, Name)).

:- func opt_object(json.object, string) = json.object.

opt_object(Obj, Name) =
    ( if search_value(Obj, Name, V) then as_object(Name, V) else map.init ).

:- func opt_number(json.object, string) = maybe(float).

opt_number(Obj, Name) =
    ( if search_value(Obj, Name, V) then yes(as_number(Name, V)) else no ).

:- func opt_string(json.object, string) = maybe(string).

opt_string(Obj, Name) =
    ( if search_value(Obj, Name, V) then yes(as_string(Name, V)) else no ).

:- func opt_bool(json.object, string) = bool.

opt_bool(Obj, Name) =
    ( if search_value(Obj, Name, V) then as_bool(Name, V) else no ).

:- func opt_objects(json.object, string) = list(json.object).

opt_objects(Obj, Name) = Objs :-
    ( if search_value(Obj, Name, V) then
        ( if V = array(Items), list.map(get_object, Items, Objs0) then
            Objs = Objs0
        else
            Objs = fail_input(quoted(Name) ++ " must be an array of objects")
        )
    else
        Objs = []
    ).

:- func as_object(string, json.value) = json.object.

as_object(Name, V) =
    ( if V = object(O) then O else fail_input(quoted(Name) ++ " must be an object") ).

:- func as_string(string, json.value) = string.

as_string(Name, V) =
    ( if V = string(S) then S else fail_input(quoted(Name) ++ " must be a string") ).

:- func as_number(string, json.value) = float.

as_number(Name, V) =
    ( if V = number(N) then N else fail_input(quoted(Name) ++ " must be a number") ).

:- func as_bool(string, json.value) = bool.

as_bool(Name, V) =
    ( if V = bool(B) then B else fail_input(quoted(Name) ++ " must be a boolean") ).

:- func maybe_default(maybe(T), T) = T.

maybe_default(yes(X), _) = X.
maybe_default(no, D) = D.

:- func quoted(string) = string.

quoted(S) = "'" ++ S ++ "'".

:- func fail_input(string) = T.
:- mode fail_input(in) = out is erroneous.

fail_input(Message) = _ :-
    throw(input_error(Message)).

%---------------------------------------------------------------------------%
% Output.

payload_to_json(single(Out)) = output_to_json(Out).
payload_to_json(batch(Outs)) = array(list.map(output_to_json, Outs)).

output_to_json(Out) = json.det_make_object([
        "outcome" - string(outcome_string(Out ^ outcome)),
        "priority_tier" - maybe_json(tier_string, Out ^ priority_tier),
        "rule_id" - string(rule_id_string(Out ^ rule_id)),
        "duplicate_of" - maybe_json(id, Out ^ duplicate_of),
        "abstain_reason" - maybe_json(abstain_reason_string, Out ^ abstain_reason),
        "follow_up_question" - maybe_json(question_string, Out ^ follow_up_question),
        "rules_version" - string(rules_version)
    ]).

:- func maybe_json(func(T) = string, maybe(T)) = json.value.

maybe_json(_, no) = null.
maybe_json(ToString, yes(X)) = string(ToString(X)).

:- func id(string) = string.

id(S) = S.

%---------------------------------------------------------------------------%
:- end_module json_io.
%---------------------------------------------------------------------------%
