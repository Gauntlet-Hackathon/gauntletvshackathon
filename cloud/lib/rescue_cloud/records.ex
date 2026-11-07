defmodule RescueCloud.Records do
  @moduledoc """
  Read-only queries behind the `/records` and `/activity` pages.

  Records search the `requests` read model (`RescueCloud.Projection`); a record's history and
  device activity come straight from the `events` log.

  ## Field activity window ("since its last sync")

  Each stored event carries `synced_at`, the server time of the sync that delivered it. A
  device's *latest sync* is the run of its sync batches that ends with its newest batch, where
  consecutive batches are at most `sync_gap` seconds apart (default 300, the fleet stale
  threshold: a laptop silent for longer than that was disconnected, so the next batch starts a new
  sync). A large offline backlog spread over several POSTs therefore counts as one sync, and
  everything the laptop logged while offline shows up in that window.
  Configure with `config :rescue_cloud, RescueCloud.Records, sync_gap: seconds`.
  """
  import Ecto.Query

  alias RescueCloud.Events.Event
  alias RescueCloud.Projection.Request
  alias RescueCloud.Repo

  @default_sync_gap 300
  @default_limit 200

  @tiers ~w(P1 P2 P3)
  @statuses ~w(received extracted decided escalated assigned rescued closed_duplicate)

  def tiers, do: @tiers
  def statuses, do: @statuses

  @doc """
  Searches requests. Filters (string keys, blank values ignored):
    * `"q"`: case-insensitive substring of raw text, location, contact or request id
    * `"tier"`: `P1` | `P2` | `P3`
    * `"status"`: a request status
  Ordered by tier (P1 first, untiered last), then newest first. At most `limit` rows.
  """
  @spec search_requests(map(), keyword()) :: [%Request{}]
  def search_requests(filters \\ %{}, opts \\ []) do
    limit = Keyword.get(opts, :limit, @default_limit)

    from(r in Request,
      order_by: [
        asc_nulls_last: r.priority_tier,
        desc: r.received_at,
        asc: r.request_id
      ],
      limit: ^limit
    )
    |> filter_text(blank_to_nil(filters["q"]))
    |> filter_eq(:priority_tier, blank_to_nil(filters["tier"]))
    |> filter_eq(:status, blank_to_nil(filters["status"]))
    |> Repo.all()
  end

  @doc """
  Request totals from one grouped query (no row limit):
  `%{total, p1, p2, p3, follow_up}`. `follow_up` counts requests whose engine outcome is
  `abstain` (the engine asked a follow-up question instead of ranking).
  """
  @spec counts() :: %{
          total: non_neg_integer(),
          p1: non_neg_integer(),
          p2: non_neg_integer(),
          p3: non_neg_integer(),
          follow_up: non_neg_integer()
        }
  def counts do
    rows =
      Repo.all(
        from r in Request,
          group_by: [r.priority_tier, r.outcome],
          select: {r.priority_tier, r.outcome, count(r.request_id)}
      )

    Enum.reduce(rows, %{total: 0, p1: 0, p2: 0, p3: 0, follow_up: 0}, fn {tier, outcome, n},
                                                                         acc ->
      acc = %{acc | total: acc.total + n}

      cond do
        outcome == "abstain" -> %{acc | follow_up: acc.follow_up + n}
        tier == "P1" -> %{acc | p1: acc.p1 + n}
        tier == "P2" -> %{acc | p2: acc.p2 + n}
        tier == "P3" -> %{acc | p3: acc.p3 + n}
        true -> acc
      end
    end)
  end

  @doc "Server time the newest event was received (`synced_at`), or nil."
  @spec last_event_at() :: DateTime.t() | nil
  def last_event_at, do: Repo.one(from e in Event, select: max(e.synced_at))

  defp filter_text(query, nil), do: query

  defp filter_text(query, text) do
    pattern = "%" <> escape_like(text) <> "%"

    from r in query,
      where:
        ilike(r.raw_text, ^pattern) or ilike(r.location_text, ^pattern) or
          ilike(r.contact, ^pattern) or ilike(r.request_id, ^pattern)
  end

  defp filter_eq(query, _field, nil), do: query
  defp filter_eq(query, field, value), do: from(r in query, where: field(r, ^field) == ^value)

  defp escape_like(s), do: String.replace(s, ["\\", "%", "_"], &("\\" <> &1))

  defp blank_to_nil(v) when is_binary(v) do
    case String.trim(v) do
      "" -> nil
      t -> t
    end
  end

  defp blank_to_nil(_), do: nil

  @doc "A request with its full event history (laptop time order), or nil."
  @spec get_record(String.t()) :: %{request: %Request{}, events: [%Event{}]} | nil
  def get_record(request_id) do
    case Repo.get(Request, request_id) do
      nil ->
        nil

      request ->
        events =
          Repo.all(
            from e in Event,
              where: e.request_id == ^request_id,
              order_by: [asc: e.occurred_at, asc: e.id]
          )

        %{request: request, events: events}
    end
  end

  @doc """
  Per device, what its latest sync delivered (see the moduledoc for the window).

  Returns one map per device, sorted by device id:
  `%{device_id, sync_started_at, last_synced_at, requests, decisions, abstains, escalations, events}`
  where counts are of `received`, `decided`, `decided` with outcome `abstain`, and `escalated`
  events in the window, and `events` lists those events in laptop-time (`occurred_at`) order.
  Option `:sync_gap` overrides the configured gap.
  """
  @spec device_activity(keyword()) :: [map()]
  def device_activity(opts \\ []) do
    gap = Keyword.get_lazy(opts, :sync_gap, fn -> conf(:sync_gap, @default_sync_gap) end)

    sync_times =
      Repo.all(
        from e in Event,
          distinct: true,
          select: {e.device_id, e.synced_at},
          order_by: [asc: e.device_id, desc: e.synced_at]
      )
      |> Enum.group_by(&elem(&1, 0), &elem(&1, 1))

    sync_times
    |> Enum.sort_by(&elem(&1, 0))
    |> Enum.map(fn {device_id, [last | _] = times} ->
      start = sync_start(times, gap)

      events =
        Repo.all(
          from e in Event,
            where: e.device_id == ^device_id and e.synced_at >= ^start,
            order_by: [asc: e.occurred_at, asc: e.id]
        )

      %{
        device_id: device_id,
        sync_started_at: start,
        last_synced_at: last,
        requests: Enum.count(events, &(&1.type == "received")),
        decisions: Enum.count(events, &(&1.type == "decided")),
        abstains: Enum.count(events, &abstain?/1),
        escalations: Enum.count(events, &(&1.type == "escalated")),
        events: events
      }
    end)
  end

  @doc "True for a `decided` event whose engine outcome is `abstain`."
  def abstain?(%{type: "decided", payload: %{"engine_output" => %{"outcome" => "abstain"}}}),
    do: true

  def abstain?(_), do: false

  # `times` is newest first; walk back while consecutive batches are within `gap` seconds.
  defp sync_start([newest | older], gap) do
    Enum.reduce_while(older, newest, fn t, start ->
      if DateTime.diff(start, t, :second) <= gap, do: {:cont, t}, else: {:halt, start}
    end)
  end

  defp conf(key, default),
    do: :rescue_cloud |> Application.get_env(__MODULE__, []) |> Keyword.get(key, default)
end
