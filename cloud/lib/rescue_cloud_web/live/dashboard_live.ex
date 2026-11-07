defmodule RescueCloudWeb.DashboardLive do
  @moduledoc """
  Skeleton dashboard page at `/`. The fleet, records and evals panels replace this body
  in tickets C5, C6 and C11.
  """
  use RescueCloudWeb, :live_view

  @impl true
  def mount(_params, _session, socket) do
    {:ok, assign(socket, :page_title, "Fleet")}
  end

  @impl true
  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash}>
      <h1 id="dashboard-title" class="text-2xl font-semibold">Rescue triage cloud</h1>
      <p id="dashboard-status">No devices have synced yet.</p>
    </Layouts.app>
    """
  end
end
