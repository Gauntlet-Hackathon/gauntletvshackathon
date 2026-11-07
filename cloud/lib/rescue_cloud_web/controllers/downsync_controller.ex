defmodule RescueCloudWeb.DownsyncController do
  @moduledoc """
  `GET /sync/down?since=<n>`: other devices' `assigned`/`rescued` events plus the approved
  rules/model versions (see `contracts/sync_api.md`).

  Approved versions come from `config :rescue_cloud, :approved_versions` (a map with optional
  `:rules_version` / `:model_version`) as a fallback; an approved model in the registry (`RescueCloud.Models`) wins for `model_version`.
  """
  use RescueCloudWeb, :controller

  alias RescueCloud.Events

  def show(conn, params) do
    case parse_since(params["since"]) do
      {:ok, since} ->
        {events, next_since} = Events.read_down(conn.assigns.device_id, since)
        approved = Application.get_env(:rescue_cloud, :approved_versions, %{})

        json(conn, %{
          next_since: next_since,
          events: Enum.map(events, &envelope/1),
          approved: %{
            rules_version: approved[:rules_version],
            model_version: RescueCloud.Models.approved_model_version() || approved[:model_version]
          }
        })

      :error ->
        conn |> put_status(400) |> json(%{error: "bad_request", detail: "invalid since"})
    end
  end

  defp parse_since(nil), do: {:ok, 0}

  defp parse_since(v) when is_binary(v) do
    case Integer.parse(v) do
      {n, ""} when n >= 0 -> {:ok, n}
      _ -> :error
    end
  end

  defp parse_since(_), do: :error

  defp envelope(e) do
    %{
      event_id: e.event_id,
      request_id: e.request_id,
      type: e.type,
      device_id: e.device_id,
      occurred_at: e.occurred_at |> DateTime.truncate(:second) |> DateTime.to_iso8601(),
      rules_version: e.rules_version,
      model_version: e.model_version,
      payload: e.payload
    }
  end
end
