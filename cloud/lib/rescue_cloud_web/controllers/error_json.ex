defmodule RescueCloudWeb.ErrorJSON do
  @moduledoc """
  This module is invoked by your endpoint in case of errors on JSON requests.

  See config/config.exs.
  """

  # If you want to customize a particular status code,
  # you may add your own clauses, such as:
  #
  # def render("500.json", _assigns) do
  #   %{errors: %{detail: "Internal Server Error"}}
  # end

  # By default, Phoenix returns the status message from
  # the template name. For example, "404.json" becomes
  # "Not Found".
  # Framework-raised errors on the sync API (for example a body that is not
  # valid JSON) use the sync error shape from contracts/sync_api.md:
  # {"error": "<snake_case_code>"}.
  def render(template, %{conn: %Plug.Conn{path_info: ["sync" | _]}}) do
    %{error: sync_code(template)}
  end

  def render(template, _assigns) do
    %{errors: %{detail: Phoenix.Controller.status_message_from_template(template)}}
  end

  defp sync_code("401.json"), do: "unauthorized"
  defp sync_code("403.json"), do: "forbidden"
  defp sync_code("404.json"), do: "not_found"
  defp sync_code("5" <> _), do: "server_error"
  defp sync_code(_), do: "bad_request"
end
