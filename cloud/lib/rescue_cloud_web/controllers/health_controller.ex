defmodule RescueCloudWeb.HealthController do
  @moduledoc """
  `GET /health`: unauthenticated liveness ping used by the field sync workers.
  """
  use RescueCloudWeb, :controller

  def show(conn, _params) do
    json(conn, %{status: "ok", time: DateTime.utc_now() |> DateTime.truncate(:second)})
  end

  @doc """
  `GET /healthz`: platform probe. 200 when the Repo answers `SELECT 1`, 503 otherwise.
  The query is overridable via `config :rescue_cloud, :health_db_check` (a 0-arity fun) so tests can simulate an outage.
  """
  def healthz(conn, _params) do
    if db_up?() do
      json(conn, %{status: "ok", db: "ok"})
    else
      conn |> put_status(503) |> json(%{status: "error", db: "down"})
    end
  end

  defp db_up? do
    check = Application.get_env(:rescue_cloud, :health_db_check, &default_check/0)
    match?({:ok, _}, safe(check))
  end

  defp safe(check) do
    {:ok, check.()}
  rescue
    _ -> :error
  catch
    _, _ -> :error
  end

  defp default_check, do: RescueCloud.Repo.query!("SELECT 1")
end
