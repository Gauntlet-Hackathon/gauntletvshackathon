defmodule RescueCloudWeb.HealthController do
  @moduledoc """
  `GET /health`: unauthenticated liveness ping used by the field sync workers.
  """
  use RescueCloudWeb, :controller

  def show(conn, _params) do
    json(conn, %{status: "ok", time: DateTime.utc_now() |> DateTime.truncate(:second)})
  end
end
