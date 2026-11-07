defmodule RescueCloud.Projection do
  @moduledoc """
  The `requests` read model: current state per request, built only from the `events` table.

  Every touched request is re-folded from all of its stored events, so applying the same event
  twice (or replaying a batch) changes nothing, and `rebuild/0` from an empty table gives exactly
  the incrementally maintained rows.

  Fold rules (events in `(occurred_at, sequence)` order, deduplicated on `event_id`):
    * status only moves forward: received → extracted → decided → assigned → escalated →
      rescued / closed_duplicate (`contracts/logic.md`, "Request status order"). Events that
      are not statuses (`corrected`) never move it, and an event with a lower rank than the
      current status is ignored.
    * `extracted` is the latest extraction with every `corrected` event applied on top, in order
      (`field` is a dotted path into the extracted payload, e.g. `people_count.value`).
    * tier, rule_id and the rest of the engine output come from the latest `decided` event.
    * `rules_version` comes from the latest `decided` event, `model_version` from the latest
      event that carries one.
    * `duplicate_group` is recomputed across all requests with `RescueCloud.Normalize.duplicate_groups/2`
      (window: `config :rescue_cloud, :duplicate_window_hours`, default 12).
  """
  import Ecto.Query

  alias RescueCloud.Events.Event
  alias RescueCloud.Normalize
  alias RescueCloud.Projection.Request
  alias RescueCloud.Repo

  @status_rank %{
    "received" => 1,
    "extracted" => 2,
    "decided" => 3,
    "assigned" => 4,
    "escalated" => 5,
    "rescued" => 6,
    "closed_duplicate" => 6
  }

  @engine_fields ~w(outcome priority_tier provisional_tier rule_id abstain_reason
                     follow_up_question duplicate_of)

  @doc "Status rank, or nil for event types that are not statuses (`corrected`)."
  def status_rank(type), do: Map.get(@status_rank, type)

  @doc """
  Updates the read model for the requests touched by `events` (event structs or maps with a
  `request_id`), then recomputes duplicate groups. Returns `{:ok, request_ids}`.
  """
  @spec apply_events([%{request_id: String.t()}]) :: {:ok, [String.t()]}
  def apply_events([]), do: {:ok, []}

  def apply_events(events) when is_list(events) do
    request_ids = events |> Enum.map(& &1.request_id) |> Enum.uniq()

    {:ok, _} =
      Repo.transaction(fn ->
        Enum.each(request_ids, &project/1)
        regroup()
      end)

    {:ok, request_ids}
  end

  @doc "Drops the read model and rebuilds it from the events table."
  @spec rebuild() :: {:ok, non_neg_integer()}
  def rebuild do
    Repo.transaction(fn ->
      Repo.delete_all(Request)
      ids = Repo.all(from e in Event, distinct: true, select: e.request_id)
      Enum.each(ids, &project/1)
      regroup()
      length(ids)
    end)
  end

  @doc "All requests, ordered by request_id."
  def list_requests, do: Repo.all(from r in Request, order_by: r.request_id)

  @doc "One request by id, or nil."
  def get_request(request_id), do: Repo.get(Request, request_id)

  # --- fold ----------------------------------------------------------------------------------

  defp project(request_id) do
    events =
      Repo.all(
        from e in Event,
          where: e.request_id == ^request_id,
          order_by: [asc: e.occurred_at, asc: e.id]
      )
      |> Enum.uniq_by(& &1.event_id)

    case events do
      [] ->
        :ok

      [first | _] ->
        state = Enum.reduce(events, initial(first), &fold/2)

        row =
          state
          |> Map.delete(:corrections)
          |> Map.put(:extracted, corrected(state.extracted, state.corrections))
          |> derive_extracted_fields()
          |> Map.put(:event_count, length(events))
          |> Map.put(:last_event_seq, events |> Enum.map(& &1.id) |> Enum.max())

        Repo.insert_all(Request, [row],
          on_conflict: {:replace, Request.fields() -- [:request_id, :duplicate_group]},
          conflict_target: :request_id
        )
    end
  end

  defp initial(first) do
    %{
      request_id: first.request_id,
      status: first.type,
      received_at: first.occurred_at,
      device_id: first.device_id,
      channel: nil,
      raw_text: nil,
      contact: nil,
      extracted: nil,
      outcome: nil,
      priority_tier: nil,
      provisional_tier: nil,
      rule_id: nil,
      abstain_reason: nil,
      follow_up_question: nil,
      duplicate_of: nil,
      rules_version: nil,
      model_version: nil,
      correction_count: 0,
      corrections: [],
      last_event_at: first.occurred_at
    }
    |> Map.update!(:status, fn type -> if status_rank(type), do: type, else: "received" end)
  end

  defp fold(event, acc) do
    acc
    |> advance(event.type)
    |> Map.put(:last_event_at, event.occurred_at)
    |> then(fn acc ->
      if event.model_version, do: %{acc | model_version: event.model_version}, else: acc
    end)
    |> apply_payload(event)
  end

  defp advance(acc, type) do
    case status_rank(type) do
      nil -> acc
      rank -> if rank > status_rank(acc.status), do: %{acc | status: type}, else: acc
    end
  end

  defp apply_payload(acc, %{type: "received"} = e) do
    %{
      acc
      | received_at: e.occurred_at,
        device_id: e.device_id,
        channel: e.payload["channel"],
        raw_text: e.payload["raw_text"],
        contact: e.payload["contact"]
    }
  end

  defp apply_payload(acc, %{type: "extracted"} = e), do: %{acc | extracted: e.payload}

  defp apply_payload(acc, %{type: "decided"} = e) do
    out = e.payload["engine_output"] || %{}

    engine =
      Map.new(@engine_fields, fn f -> {String.to_existing_atom(f), out[f]} end)

    acc
    |> Map.merge(engine)
    |> Map.put(:rules_version, e.rules_version || out["rules_version"])
  end

  defp apply_payload(acc, %{type: "corrected"} = e) do
    %{
      acc
      | corrections: acc.corrections ++ [e.payload],
        correction_count: acc.correction_count + 1
    }
  end

  defp apply_payload(acc, _event), do: acc

  defp corrected(nil, _corrections), do: nil

  defp corrected(extracted, corrections) do
    Enum.reduce(corrections, extracted, fn c, acc ->
      put_path(acc, String.split(c["field"], "."), c["new_value"])
    end)
  end

  defp put_path(_container, [], value), do: value

  defp put_path(container, [key | rest], value) when is_map(container) do
    Map.put(container, key, put_path(Map.get(container, key), rest, value))
  end

  defp put_path(container, [key | rest] = path, value) when is_list(container) do
    case Integer.parse(key) do
      {i, ""} when i >= 0 and i < length(container) ->
        List.update_at(container, i, &put_path(&1, rest, value))

      _ ->
        put_path(%{}, path, value)
    end
  end

  defp put_path(_other, path, value), do: put_path(%{}, path, value)

  defp derive_extracted_fields(row) do
    ex = row.extracted || %{}
    location = ex["location"]
    people = ex["people_count"]

    Map.merge(row, %{
      location_text: if(is_map(location), do: stringish(location["text"])),
      people_count: if(is_map(people) and is_integer(people["value"]), do: people["value"])
    })
  end

  defp stringish(v) when is_binary(v), do: v
  defp stringish(_), do: nil

  # --- cross-site duplicate groups -------------------------------------------------------------

  defp regroup do
    rows =
      Repo.all(
        from r in Request,
          select: %{
            request_id: r.request_id,
            received_at: r.received_at,
            location_text: r.location_text,
            people_count: r.people_count,
            contact: r.contact,
            priority_tier: r.priority_tier,
            duplicate_group: r.duplicate_group
          }
      )

    inputs =
      Enum.map(rows, fn r ->
        %{
          "request_id" => r.request_id,
          "received_at" => DateTime.to_iso8601(r.received_at),
          "location_text" => r.location_text,
          "people_count" => r.people_count,
          "contact" => r.contact,
          "priority_tier" => r.priority_tier
        }
      end)

    group_of =
      for [oldest | _] = group <- Normalize.duplicate_groups(inputs, window_hours()),
          rid <- group,
          into: %{},
          do: {rid, oldest}

    for r <- rows, (new = group_of[r.request_id]) != r.duplicate_group do
      Repo.update_all(from(x in Request, where: x.request_id == ^r.request_id),
        set: [duplicate_group: new]
      )
    end

    :ok
  end

  defp window_hours, do: Application.get_env(:rescue_cloud, :duplicate_window_hours, 12)
end
