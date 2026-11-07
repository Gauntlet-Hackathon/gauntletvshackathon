defmodule RescueCloud.Projection do
  @moduledoc """
  The `requests` read model, built only from the `events` table.

  Every row is a pure function of that request's events (folded in `(occurred_at, event_id)`
  order), so `apply_events/1` is idempotent (events are deduped by `event_id` in the log) and
  `rebuild/0` from empty yields exactly the incrementally maintained table.

  * `status` only moves forward (see `@status_rank`); a late or replayed event never rewinds it.
  * `priority_tier`, `rule_id`, `rules_version` and `outcome` come from the latest `decided` event.
  * `duplicate_group` is the oldest member's request_id for groups of 2+ requests, found with the
    shared duplicate rules (`contracts/logic.md`) across all sites; `nil` otherwise.
  """
  import Ecto.Query

  alias RescueCloud.Events.Event
  alias RescueCloud.Projection.Request
  alias RescueCloud.{Normalize, Repo}

  @window_hours 12
  @status_rank %{
    "received" => 1,
    "extracted" => 2,
    "decided" => 3,
    "escalated" => 4,
    "assigned" => 5,
    "rescued" => 6,
    "closed_duplicate" => 7
  }

  @doc "Re-derives the rows touched by `events` (anything with `request_id`) and regroups duplicates."
  @spec apply_events([map()]) :: :ok
  def apply_events([]), do: :ok

  def apply_events(events) do
    ids = events |> Enum.map(& &1.request_id) |> Enum.uniq()
    {:ok, _} = Repo.transaction(fn -> project(ids) end)
    :ok
  end

  @doc "Drops the table contents and rebuilds every row from the events table."
  @spec rebuild() :: :ok
  def rebuild do
    {:ok, _} =
      Repo.transaction(fn ->
        Repo.delete_all(Request)
        project(Repo.all(from e in Event, select: e.request_id, distinct: true))
      end)

    :ok
  end

  @doc "Current rows ordered by request_id."
  def list, do: Repo.all(from r in Request, order_by: r.request_id)
  def get(request_id), do: Repo.get(Request, request_id)

  defp project(ids) do
    Repo.all(from e in Event, where: e.request_id in ^ids)
    |> Enum.group_by(& &1.request_id)
    |> Enum.each(fn {id, evs} ->
      attrs = evs |> derive() |> Map.put(:request_id, id)

      Repo.insert!(struct(Request, attrs),
        on_conflict: {:replace, Request.fields() -- [:duplicate_group]},
        conflict_target: :request_id
      )
    end)

    regroup()
  end

  defp derive(events) do
    events = Enum.sort_by(events, &{DateTime.to_unix(&1.occurred_at), &1.event_id})

    base = %{
      status: "received",
      event_count: length(events),
      last_event_id: events |> Enum.map(& &1.id) |> Enum.max(),
      device_id: hd(events).device_id,
      received_at: hd(events).occurred_at
    }

    events |> Enum.reduce(base, &fold/2) |> put_derived()
  end

  defp fold(%{type: type} = e, acc) do
    acc = advance(acc, type)

    case type do
      "received" ->
        Map.merge(acc, %{
          received_at: e.occurred_at,
          device_id: e.device_id,
          contact: e.payload["contact"]
        })

      "extracted" ->
        Map.merge(acc, %{extracted: e.payload, model_version: e.model_version})

      "decided" ->
        out = e.payload["engine_output"] || %{}

        Map.merge(acc, %{
          outcome: out["outcome"],
          priority_tier: out["priority_tier"],
          rule_id: out["rule_id"],
          duplicate_of: out["duplicate_of"],
          rules_version: e.rules_version || out["rules_version"]
        })

      "corrected" ->
        correct(acc, e.payload)

      "closed_duplicate" ->
        Map.put(acc, :duplicate_of, e.payload["duplicate_of"])

      _ ->
        acc
    end
  end

  defp advance(acc, type) do
    if @status_rank[type] > @status_rank[acc.status], do: %{acc | status: type}, else: acc
  end

  # Applies a coordinator correction to the extracted payload (dotted path, e.g. people_count.value).
  defp correct(%{extracted: ex} = acc, %{"field" => field, "new_value" => v}) when is_map(ex) do
    path = field |> String.split(".") |> Enum.map(&Access.key(&1, %{}))

    try do
      %{acc | extracted: put_in(ex, path, v)}
    rescue
      _ -> acc
    end
  end

  defp correct(acc, _), do: acc

  defp put_derived(acc) do
    ex = Map.get(acc, :extracted) || %{}

    acc
    |> Map.put(:location_text, get_in(ex, ["location", "text"]))
    |> Map.put(:people_count, get_in(ex, ["people_count", "value"]))
  end

  # Duplicate groups depend on all requests, so recompute them over the whole table.
  defp regroup do
    rows = Repo.all(Request)

    groups =
      rows
      |> Enum.filter(& &1.received_at)
      |> Enum.map(fn r ->
        %{
          "request_id" => r.request_id,
          "received_at" => DateTime.to_iso8601(r.received_at),
          "location_text" => r.location_text,
          "people_count" => r.people_count,
          "contact" => r.contact,
          "priority_tier" => r.priority_tier
        }
      end)
      |> duplicate_groups(@window_hours)

    by_member = for [oldest | _] = g <- groups, id <- g, into: %{}, do: {id, oldest}

    for r <- rows, (want = by_member[r.request_id]) != r.duplicate_group do
      Repo.update_all(from(x in Request, where: x.request_id == ^r.request_id),
        set: [duplicate_group: want]
      )
    end

    :ok
  end

  @doc """
  Connected components (2+ members) of the duplicate-candidate relation in `contracts/logic.md`,
  as lists of request ids oldest first, groups ordered by their oldest member.
  Items are string-keyed maps like the ones in `contracts/vectors/duplicate_groups.json`.
  """
  @spec duplicate_groups([map()], number()) :: [[String.t()]]
  def duplicate_groups(items, window_hours) do
    keyed =
      Enum.map(items, fn i ->
        {i, Normalize.location(i["location_text"]), Normalize.contact(i["contact"]),
         ts(i["received_at"])}
      end)

    order = fn i -> {i["received_at"], i["request_id"]} end

    edges =
      for {a, a_loc, a_contact, a_ts} <- keyed,
          {b, b_loc, b_contact, b_ts} <- keyed,
          a_loc != "",
          a_loc == b_loc,
          order.(b) < order.(a),
          a_ts - b_ts <= window_hours * 3600,
          (a_contact != "" and a_contact == b_contact) or
            (a["people_count"] != nil and a["people_count"] == b["people_count"]),
          do: {a["request_id"], b["request_id"]}

    parent = Map.new(items, &{&1["request_id"], &1["request_id"]})
    parent = Enum.reduce(edges, parent, fn {x, y}, p -> union(p, x, y) end)
    by_id = Map.new(items, &{&1["request_id"], &1})

    parent
    |> Map.keys()
    |> Enum.group_by(&find(parent, &1))
    |> Map.values()
    |> Enum.filter(&(length(&1) > 1))
    |> Enum.map(fn g -> Enum.sort_by(g, &order.(by_id[&1])) end)
    |> Enum.sort_by(fn [first | _] -> order.(by_id[first]) end)
  end

  defp ts(s) do
    {:ok, dt, _} = DateTime.from_iso8601(s)
    DateTime.to_unix(dt)
  end

  defp find(parent, x) do
    case parent[x] do
      ^x -> x
      p -> find(parent, p)
    end
  end

  defp union(parent, x, y) do
    rx = find(parent, x)
    ry = find(parent, y)
    if rx == ry, do: parent, else: Map.put(parent, rx, ry)
  end
end
