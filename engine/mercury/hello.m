%---------------------------------------------------------------------------%
% W0-5 toolchain spike (#24): prove a det predicate can read JSON from stdin.
%
% Reads one JSON value from stdin and writes one JSON object to stdout:
%   {"name": "Ada"}  ->  {"name":"Ada","status":"accepted"}
%   anything else    ->  {"reason":"...","status":"rejected"}
% Input that is not valid JSON writes to stderr and exits 1.
%---------------------------------------------------------------------------%

:- module hello.
:- interface.

:- import_module io.

:- pred main(io::di, io::uo) is det.

%---------------------------------------------------------------------------%

:- implementation.

:- import_module json.
:- import_module list.
:- import_module map.
:- import_module pair.
:- import_module stream.
:- import_module string.

:- type verdict
    --->    accepted(string)
    ;       rejected(string).

main(!IO) :-
    io.stdin_stream(Stdin, !IO),
    json.init_reader(Stdin, Reader, !IO),
    json.read_value(Reader, Result, !IO),
    (
        Result = ok(Value),
        classify(Value, Verdict),
        write_verdict(Verdict, !IO)
    ;
        Result = eof,
        report_error("unexpected end-of-file", !IO)
    ;
        Result = error(Error),
        report_error(stream.error_message(Error), !IO)
    ).

    % The switch has no default arm, so adding a constructor to json.value
    % is a compile error here. That is the guarantee E7 wants from Mercury.
    %
:- pred classify(json.value::in, verdict::out) is det.

classify(Value, Verdict) :-
    (
        Value = object(Object),
        ( if
            map.search(Object, "name", NameValue),
            NameValue = string(Name)
        then
            Verdict = accepted(Name)
        else
            Verdict = rejected("object has no string name")
        )
    ;
        Value = null,
        Verdict = rejected("null")
    ;
        Value = bool(_),
        Verdict = rejected("bool")
    ;
        Value = string(_),
        Verdict = rejected("string")
    ;
        Value = number(_),
        Verdict = rejected("number")
    ;
        Value = array(_),
        Verdict = rejected("array")
    ).

:- pred write_verdict(verdict::in, io::di, io::uo) is det.

write_verdict(Verdict, !IO) :-
    (
        Verdict = accepted(Name),
        Members = ["status" - string("accepted"), "name" - string(Name)]
    ;
        Verdict = rejected(Reason),
        Members = ["status" - string("rejected"), "reason" - string(Reason)]
    ),
    io.stdout_stream(Stdout, !IO),
    json.init_writer(Stdout, Writer, !IO),
    json.put_value(Writer, json.det_make_object(Members), !IO),
    io.nl(Stdout, !IO).

:- pred report_error(string::in, io::di, io::uo) is det.

report_error(Message, !IO) :-
    io.stderr_stream(Stderr, !IO),
    io.format(Stderr, "error: %s\n", [s(Message)], !IO),
    io.set_exit_status(1, !IO).

%---------------------------------------------------------------------------%
:- end_module hello.
%---------------------------------------------------------------------------%
