defmodule RescueCloudWeb.SyncController do
  @moduledoc "Device sync API (behind `RescueCloudWeb.Plugs.DeviceAuth`)."
  use RescueCloudWeb, :controller

  alias RescueCloud.Fleet

  def heartbeat(conn, params) do
    device_id = conn.assigns.device_id

    case params do
      %{"device_id" => other} when other != device_id ->
        conn |> put_status(403) |> json(%{error: "device_mismatch"})

      _ ->
        case Fleet.record_heartbeat(device_id, params) do
          {:ok, _hb} ->
            json(conn, %{ok: true, server_time: DateTime.utc_now() |> DateTime.truncate(:second)})

          {:error, changeset} ->
            conn
            |> put_status(400)
            |> json(%{error: "bad_request", detail: inspect(changeset.errors)})
        end
    end
  end
end
