defmodule RescueCloud.Records do
  @moduledoc """
  Read-side queries for the records and field-activity pages: request search, per-request event
  history and per-device activity. Reads the `requests` read model and the `events` log only.
  """
  import Ecto.Query

  alias RescueCloud.Events.Event
  alias RescueCloud.Projection.Request
  alias RescueCloud.Repo

  @doc """
  Searches requests. Filters (all optional, blank values ignored): `"q"` (case-insensitive text in
  raw text, location, contact or request id), `"tier"` (exact `priority_tier`), `"status"`.
  Newest first, at most `:limit` (default 100).
  """
  def search_requests(filters \\ %{}, opts \\ []) do
    q = blank_to_nil(filters["q"])
    tier = blank_to_nil(filters["tier"])
    status = blank_to_nil(filters["status"])

    query = from r in Request, order_by: [desc: r.received_at, desc: r.request_id]
    query = if tier, do: where(query, [r], r.priority_tier == ^tier), else: query
    query = if status, do: where(query, [r], r.status == ^status), else: query

    query =
      if q do
        pattern = "%" <> escape_like(q) <> "%"

        where(
          query,
          [r],
          ilike(r.raw_text, ^pattern) or ilike(r.location_text, ^pattern) or
            ilike(r.contact, ^pattern) or ilike(r.request_id, ^pattern)
        )
      else
        query
      end

    Repo.all(limit(query, ^Keyword.get(opts, :limit, 100)))
  end

  @doc "Distinct tiers and statuses present, for the filter dropdowns."
  def filter_options do
    %{
      tiers:
        Repo.all(
          from r in Request,
            where: not is_nil(r.priority_tier),
            distinct: true,
            select: r.priority_tier,
            order_by: r.priority_tier
        ),
      statuses: Repo.all(from r in Request, distinct: true, select: r.status, order_by: r.status)
    }
  end

  def get_request(request_id), do: Repo.get(Request, request_id)

  @doc "All events of a request in log order."
  def request_history(request_id),
    do: Repo.all(from e in Event, where: e.request_id == ^request_id, order_by: e.id)

  @doc """
  Per-device activity: for every device that has logged events (or sent a heartbeat), counts of
  requests logged, decisions, abstains and escalations, plus its most recent events (laptop
  `occurred_at`, newest first). `:since` (a `DateTime`, default none) limits to events the laptop
  logged at or after that time; `:limit` caps recent events per device (default 20).
  """
  def device_activity(opts \\ []) do
    since = opts[:since]
    limit = Keyword.get(opts, :limit, 20)

    events =
      Event
      |> then(fn q -> if since, do: where(q, [e], e.occurred_at >= ^since), else: q end)
      |> order_by([e], desc: e.occurred_at, desc: e.id)
      |> Repo.all()
      |> Enum.group_by(& &1.device_id)

    heartbeats = Map.new(RescueCloud.Fleet.list_heartbeats(), &{&1.device_id, &1})

    (Map.keys(events) ++ Map.keys(heartbeats))
    |> Enum.uniq()
    |> Enum.sort()
    |> Enum.map(fn device ->
      evs = Map.get(events, device, [])

      %{
        device_id: device,
        last_seen: heartbeats[device] && heartbeats[device].last_seen,
        requests: count(evs, &(&1.type == "received")),
        decisions: count(evs, &(&1.type == "decided")),
        abstains: count(evs, &(&1.type == "decided" and outcome(&1) == "abstain")),
        escalations: count(evs, &(&1.type == "escalated")),
        recent: Enum.take(evs, limit)
      }
    end)
  end

  @doc "Engine outcome carried by a `decided` event, or nil."
  def outcome(%{payload: %{"engine_output" => %{"outcome" => o}}}), do: o
  def outcome(_), do: nil

  defp count(list, fun), do: Enum.count(list, fun)

  defp blank_to_nil(v) when is_binary(v),
    do: if(String.trim(v) == "", do: nil, else: String.trim(v))

  defp blank_to_nil(_), do: nil

  defp escape_like(s), do: String.replace(s, ["\\", "%", "_"], &("\\" <> &1))
end
