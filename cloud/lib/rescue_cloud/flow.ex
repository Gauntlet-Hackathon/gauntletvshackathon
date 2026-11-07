defmodule RescueCloud.Flow do
  @moduledoc """
  Read model behind the `/flow` page: where each request is in the call flow, from intake to
  the volunteer crew, built straight from the `events` log.

  Stages, in order:

    * `:received`  the field app took the message in (a `received` event)
    * `:extracted` the tagger model read it (an `extracted` event: the model's answer)
    * `:decided`   the decision engine ranked it (a `decided` event)
    * `:synced`    the cloud has it (the `synced_at` of its first event here)
    * `:assigned`  a crew was sent (an `assigned` event)
    * `:closed`    `rescued` or `closed_duplicate`

  The field pipeline writes `received`, `extracted` and `decided` in one transaction with one
  laptop timestamp, so those three share a time; the model and engine latencies are not recorded.
  A request the engine ruled `not_a_request` skips `:assigned` and `:closed`.
  """
  import Ecto.Query

  alias RescueCloud.Events.Event
  alias RescueCloud.Repo

  @stages [:received, :extracted, :decided, :synced, :assigned, :closed]
  @default_limit 25

  @participants [
    intake: "Intake (ECC simulator)",
    field_app: "Field app",
    model: "Tagger model",
    engine: "Decision engine",
    field_db: "Field DB (SQLite)",
    cloud: "Cloud (Postgres)",
    crew: "Volunteer crew"
  ]

  def stages, do: @stages

  @doc "Sequence-diagram participants, in column order: `[{key, label}]`."
  def participants, do: @participants

  @doc "The requests with the newest activity (latest event stored), newest first."
  @spec recent(keyword()) :: [map()]
  def recent(opts \\ []) do
    limit = Keyword.get(opts, :limit, @default_limit)

    ids =
      Repo.all(
        from e in Event,
          group_by: e.request_id,
          order_by: [desc: max(e.id)],
          limit: ^limit,
          select: e.request_id
      )

    events =
      Repo.all(
        from e in Event, where: e.request_id in ^ids, order_by: [asc: e.occurred_at, asc: e.id]
      )
      |> Enum.group_by(& &1.request_id)

    Enum.map(ids, &build(&1, Map.fetch!(events, &1)))
  end

  @doc "One request's flow, or nil when the cloud has no events for it."
  @spec get(String.t()) :: map() | nil
  def get(request_id) do
    Repo.all(
      from e in Event,
        where: e.request_id == ^request_id,
        order_by: [asc: e.occurred_at, asc: e.id]
    )
    |> case do
      [] -> nil
      events -> build(request_id, events)
    end
  end

  @doc "Builds a flow from a request's events (laptop-time order)."
  @spec build(String.t(), [%Event{}]) :: map()
  def build(request_id, events) do
    last = fn type -> events |> Enum.filter(&(&1.type == type)) |> List.last() end
    received = last.("received")
    extracted = last.("extracted")
    decided = last.("decided")
    assigned = last.("assigned")
    closed = last.("rescued") || last.("closed_duplicate")
    decision = decided && decided.payload["engine_output"]
    synced_at = events |> Enum.map(& &1.synced_at) |> Enum.min(DateTime, fn -> nil end)

    flow = %{
      request_id: request_id,
      device_id: hd(events).device_id,
      received: received,
      extracted: extracted,
      decided: decided,
      assigned: assigned,
      escalated: last.("escalated"),
      closed: closed,
      corrections: Enum.filter(events, &(&1.type == "corrected")),
      raw_text: received && received.payload["raw_text"],
      channel: received && received.payload["channel"],
      extraction: extracted && extracted.payload,
      decision: decision,
      engine_input: decided && decided.payload["engine_input"],
      model_version: (extracted || decided || %{model_version: nil}).model_version,
      rules_version: decision && decision["rules_version"],
      crew: assigned && assigned.payload["crew"],
      synced_at: synced_at,
      cloud_lag_s: received && synced_at && DateTime.diff(synced_at, received.occurred_at),
      assign_after_s:
        received && assigned && DateTime.diff(assigned.occurred_at, received.occurred_at),
      last_event_id: events |> Enum.map(& &1.id) |> Enum.max()
    }

    flow
    |> Map.put(:stage, @stages |> Enum.filter(&(state(flow, &1) == :done)) |> List.last())
    |> Map.put(:waiting_for, waiting_for(flow))
  end

  @doc "`:done`, `:waiting`, or `:skipped` (not a request: no crew)."
  @spec state(map(), atom()) :: :done | :waiting | :skipped
  def state(flow, :synced), do: if(flow.synced_at, do: :done, else: :waiting)

  def state(flow, stage) when stage in [:assigned, :closed] do
    cond do
      Map.fetch!(flow, stage) -> :done
      not_a_request?(flow) -> :skipped
      true -> :waiting
    end
  end

  def state(flow, stage), do: if(Map.fetch!(flow, stage), do: :done, else: :waiting)

  defp not_a_request?(%{decision: %{"outcome" => "not_a_request"}}), do: true
  defp not_a_request?(_), do: false

  defp waiting_for(flow) do
    cond do
      flow.closed || not_a_request?(flow) ->
        nil

      flow.assigned ->
        "the crew to report back"

      flow.decision == nil ->
        "the engine's decision"

      flow.decision["outcome"] == "abstain" ->
        "an answer to: #{flow.decision["follow_up_question"]}"

      true ->
        "a crew"
    end
  end

  @doc """
  The request's call flow as sequence-diagram messages, in order:
  `[%{from, to, label, detail, at, state}]` with `state` `:done | :pending | :skipped`.
  Model and engine hops carry the laptop time of the transaction that stored their result.
  """
  @spec sequence(map()) :: [map()]
  def sequence(flow) do
    t = flow.received && flow.received.occurred_at
    d = flow.decision
    crew_state = fn stage -> sequence_state(state(flow, stage)) end

    [
      msg(
        :intake,
        :field_app,
        "POST /requests (#{flow.channel || "?"})",
        flow.raw_text,
        t,
        flow.received
      ),
      msg(:field_app, :model, "raw text to read", nil, t, flow.extracted),
      msg(
        :model,
        :field_app,
        "extracted fields",
        model_summary(flow.extraction),
        t,
        flow.extracted
      ),
      msg(
        :field_app,
        :engine,
        "fields + context (no raw text)",
        context_summary(flow),
        t,
        flow.decided
      ),
      msg(:engine, :field_app, decision_label(d), d && d["rule_id"], t, flow.decided),
      msg(
        :field_app,
        :field_db,
        "store 3 events + outbox",
        "received, extracted, decided",
        t,
        flow.decided
      ),
      msg(:field_db, :cloud, "sync", lag_label(flow.cloud_lag_s), flow.synced_at, flow.synced_at),
      %{
        msg(
          :field_app,
          :crew,
          "assigned #{flow.crew}",
          flow.assigned && flow.assigned.payload["note"],
          flow.assigned && flow.assigned.occurred_at,
          flow.assigned
        )
        | state: crew_state.(:assigned)
      },
      %{
        msg(
          :crew,
          :field_app,
          closed_label(flow),
          escalated_label(flow),
          flow.closed && flow.closed.occurred_at,
          flow.closed
        )
        | state: crew_state.(:closed)
      }
    ]
  end

  defp msg(from, to, label, detail, at, done) do
    %{
      from: from,
      to: to,
      label: label,
      detail: detail,
      at: done && at,
      state: if(done, do: :done, else: :pending)
    }
  end

  defp sequence_state(:waiting), do: :pending
  defp sequence_state(s), do: s

  defp decision_label(nil), do: "decision"

  defp decision_label(d) do
    [d["outcome"], d["priority_tier"] || (d["provisional_tier"] && "#{d["provisional_tier"]}?")]
    |> Enum.reject(&is_nil/1)
    |> Enum.join(" ")
  end

  defp closed_label(%{closed: %{type: "rescued", payload: p}}),
    do: "rescued #{p["rescued_count"] || ""}" |> String.trim()

  defp closed_label(%{closed: %{type: "closed_duplicate"}}), do: "closed as duplicate"
  defp closed_label(_), do: "rescued / closed"

  defp escalated_label(%{escalated: %{payload: p}}), do: "escalated: #{p["reason"]}"
  defp escalated_label(_), do: nil

  defp lag_label(nil), do: nil
  defp lag_label(s), do: "+#{s}s after intake"

  defp model_summary(nil), do: nil

  defp model_summary(x) do
    kinds = fn key -> x |> Map.get(key, []) |> Enum.map_join(", ", & &1["kind"]) end

    [
      "location: #{get_in(x, ["location", "text"])}",
      "people: #{get_in(x, ["people_count", "value"])}",
      "vulnerable: #{kinds.("vulnerable")}",
      "hazards: #{kinds.("hazards")}"
    ]
    |> Enum.join(" · ")
  end

  defp context_summary(%{engine_input: %{"context" => c}}) do
    "street hits #{length(c["street_list_hits"] || [])} · " <>
      "duplicate candidates #{length(c["duplicate_candidates"] || [])} · " <>
      "hoax markers #{c["hoax_marker_hits"]}"
  end

  defp context_summary(_), do: nil
end
