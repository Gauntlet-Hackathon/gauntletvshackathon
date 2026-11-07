defmodule RescueCloud.Corrections do
  @moduledoc """
  Coordinator corrections: create, review, export.

  A correction is a `corrected` event appended via `RescueCloud.Events.append_batch/2` (device
  `cloud`, no rules/model version) and applied to the requests read model. A reviewer then
  approves or rejects it in the review queue (`correction_reviews`; no row = pending).
  `export/1` writes approved corrections as JSONL for the next training run.

  Corrections never feed evals: the export directory is `config :rescue_cloud, :exports_dir`
  (default `exports`) and `export/1` refuses any path that has an `evals` segment.
  """
  import Ecto.Query

  alias RescueCloud.Corrections.Review
  alias RescueCloud.Events.Event
  alias RescueCloud.{Events, Projection, Repo}

  @device_id "cloud"
  @crockford ~c"0123456789ABCDEFGHJKMNPQRSTVWXYZ"

  @doc """
  Appends a `corrected` event for `request_id`. `attrs` (string keys): `"field"` (dotted path),
  `"new_value"`, `"corrected_by"`, optional `"note"`. `old_value` is read from the request's
  current extracted payload. Returns `{:ok, event}` or `{:error, reason}`.
  """
  @spec create(String.t(), map()) :: {:ok, struct()} | {:error, atom()}
  def create(request_id, attrs) do
    field = attrs["field"] |> to_string() |> String.trim()
    by = attrs["corrected_by"] |> to_string() |> String.trim()

    request = Repo.get(Projection.Request, request_id)

    cond do
      field == "" or by == "" ->
        {:error, :invalid}

      is_nil(request) ->
        {:error, :not_found}

      true ->
        event = %{
          "event_id" => ulid(),
          "request_id" => request_id,
          "type" => "corrected",
          "device_id" => @device_id,
          "occurred_at" => DateTime.utc_now() |> DateTime.truncate(:second) |> to_wire(),
          "rules_version" => nil,
          "model_version" => nil,
          "payload" => %{
            "field" => field,
            "old_value" => get_path(request.extracted, String.split(field, ".")),
            "new_value" => attrs["new_value"],
            "corrected_by" => by,
            "note" => blank_to_nil(attrs["note"])
          }
        }

        case Events.append_batch([event]) do
          {:ok, %{inserted: [stored]}} ->
            {:ok, _} = Projection.apply_events([stored])
            Phoenix.PubSub.broadcast(RescueCloud.PubSub, "events", {:event, stored})
            {:ok, stored}

          _ ->
            {:error, :invalid}
        end
    end
  end

  @doc "Corrections with their review status, newest first. `status`: nil (all), \"pending\", \"approved\", \"rejected\"."
  @spec list(String.t() | nil) :: [%{event: struct(), status: String.t(), review: struct() | nil}]
  def list(status \\ nil) do
    from(e in Event,
      left_join: r in Review,
      on: r.event_id == e.event_id,
      where: e.type == "corrected",
      order_by: [desc: e.id],
      select: {e, r}
    )
    |> Repo.all()
    |> Enum.map(fn {e, r} -> %{event: e, review: r, status: (r && r.status) || "pending"} end)
    |> Enum.filter(&(status in [nil, ""] or &1.status == status))
  end

  @doc "Approves or rejects a correction (`decision`: :approve | :reject). A decision can be changed."
  @spec review(String.t(), :approve | :reject, String.t()) ::
          {:ok, %Review{}} | {:error, :not_found | :invalid}
  def review(event_id, decision, reviewer) when decision in [:approve, :reject] do
    reviewer = reviewer |> to_string() |> String.trim()

    cond do
      reviewer == "" ->
        {:error, :invalid}

      not Repo.exists?(from e in Event, where: e.event_id == ^event_id and e.type == "corrected") ->
        {:error, :not_found}

      true ->
        status = if decision == :approve, do: "approved", else: "rejected"
        now = DateTime.utc_now() |> DateTime.truncate(:second)

        Repo.insert(
          %Review{event_id: event_id, status: status, reviewed_by: reviewer, reviewed_at: now},
          on_conflict: [set: [status: status, reviewed_by: reviewer, reviewed_at: now]],
          conflict_target: :event_id,
          returning: true
        )
    end
  end

  @doc """
  Writes approved corrections to `<dir>/corrections-<date>.jsonl` (one JSON object per line,
  oldest first). Options: `:dir` (default configured exports dir), `:date` (default today, UTC).
  Returns `{:ok, %{path: path, count: n}}`, or `{:error, :forbidden_path}` for a path under `evals`.
  """
  @spec export(keyword()) ::
          {:ok, %{path: String.t(), count: non_neg_integer()}} | {:error, atom()}
  def export(opts \\ []) do
    dir = Keyword.get_lazy(opts, :dir, &exports_dir/0)
    date = Keyword.get_lazy(opts, :date, fn -> Date.utc_today() end)
    path = Path.expand(Path.join(dir, "corrections-#{Date.to_iso8601(date)}.jsonl"))

    if "evals" in Path.split(path) do
      {:error, :forbidden_path}
    else
      rows =
        Repo.all(
          from e in Event,
            join: r in Review,
            on: r.event_id == e.event_id,
            where: e.type == "corrected" and r.status == "approved",
            order_by: [asc: e.id],
            select: {e, r}
        )

      lines =
        Enum.map(rows, fn {e, r} ->
          Jason.encode!(%{
            event_id: e.event_id,
            request_id: e.request_id,
            field: e.payload["field"],
            old_value: e.payload["old_value"],
            new_value: e.payload["new_value"],
            corrected_by: e.payload["corrected_by"],
            note: e.payload["note"],
            corrected_at: to_wire(e.occurred_at),
            approved_by: r.reviewed_by,
            approved_at: to_wire(r.reviewed_at)
          }) <> "\n"
        end)

      File.mkdir_p!(dir)
      File.write!(path, lines)
      {:ok, %{path: path, count: length(lines)}}
    end
  end

  def exports_dir, do: Application.get_env(:rescue_cloud, :exports_dir, "exports")

  defp to_wire(%DateTime{} = dt), do: dt |> DateTime.truncate(:second) |> DateTime.to_iso8601()

  defp blank_to_nil(nil), do: nil

  defp blank_to_nil(s) do
    case String.trim(to_string(s)) do
      "" -> nil
      t -> t
    end
  end

  defp get_path(value, []), do: value

  defp get_path(map, [k | rest]) when is_map(map), do: get_path(Map.get(map, k), rest)

  defp get_path(list, [k | rest]) when is_list(list) do
    case Integer.parse(k) do
      {i, ""} -> get_path(Enum.at(list, i), rest)
      _ -> nil
    end
  end

  defp get_path(_, _), do: nil

  # 26-char Crockford ULID: 48-bit ms timestamp + 80 random bits.
  defp ulid do
    bits = <<0::2, System.os_time(:millisecond)::48, :crypto.strong_rand_bytes(10)::binary>>
    for <<c::5 <- bits>>, into: "", do: <<Enum.at(@crockford, c)>>
  end
end
