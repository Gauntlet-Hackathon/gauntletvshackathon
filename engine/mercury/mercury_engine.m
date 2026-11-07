%---------------------------------------------------------------------------%
% E7 (#66): Mercury decision engine CLI, the same JSON interface as
% `python -m rescue_engine.fallback`:
%   engine_input JSON (object or list of objects) on stdin
%   -> engine_output JSON (object or list) on stdout, one line, exit 0.
% Error shape: exit 1, empty stdout, one `rescue_engine.mercury: <message>`
% line on stderr.
%---------------------------------------------------------------------------%

:- module mercury_engine.
:- interface.

:- import_module io.

:- pred main(io::di, io::uo) is cc_multi.

%---------------------------------------------------------------------------%

:- implementation.

:- import_module exception.
:- import_module json.
:- import_module json_io.
:- import_module list.
:- import_module rules.
:- import_module stream.
:- import_module string.
:- import_module univ.

main(!IO) :-
    io.stdin_stream(Stdin, !IO),
    json.init_reader(Stdin, Reader, !IO),
    json.read_value(Reader, ReadResult, !IO),
    (
        ReadResult = ok(Value),
        try(decide_payload(Value), Result),
        (
            Result = succeeded(Json),
            io.stdout_stream(Stdout, !IO),
            json.write_compact(Stdout, Json, !IO),
            io.nl(Stdout, !IO)
        ;
            Result = exception(Univ),
            ( if univ_to_type(Univ, input_error(Message)) then
                report_error(Message, !IO)
            else
                report_error("unexpected error: " ++ string(univ_value(Univ)), !IO)
            )
        )
    ;
        ReadResult = eof,
        report_error("JSONDecodeError: empty input", !IO)
    ;
        ReadResult = error(Error),
        report_error("JSONDecodeError: " ++ stream.error_message(Error), !IO)
    ).

:- pred decide_payload(json.value::in, json.value::out) is det.

decide_payload(Value, Json) :-
    Payload = parse_payload(Value),
    (
        Payload = single(Input),
        Outputs = single(decide(Input))
    ;
        Payload = batch(Inputs),
        Outputs = batch(list.map(decide, Inputs))
    ),
    Json = payload_to_json(Outputs).

:- pred report_error(string::in, io::di, io::uo) is det.

report_error(Message, !IO) :-
    OneLine = string.join_list(" ", string.words(Message)),
    io.stderr_stream(Stderr, !IO),
    io.format(Stderr, "rescue_engine.mercury: %s\n", [s(OneLine)], !IO),
    io.set_exit_status(1, !IO).

%---------------------------------------------------------------------------%
:- end_module mercury_engine.
%---------------------------------------------------------------------------%
