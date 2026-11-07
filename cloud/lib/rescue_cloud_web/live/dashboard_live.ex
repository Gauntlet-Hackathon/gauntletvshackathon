defmodule RescueCloudWeb.DashboardLive do
  @moduledoc """
  Skeleton dashboard page at `/`. The fleet, records and evals panels replace this body
  in tickets C5, C6 and C11.
  """
  use RescueCloudWeb, :live_view

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket), do: Phoenix.PubSub.subscribe(RescueCloud.PubSub, "verifications")

    {:ok, socket |> assign(:page_title, "Fleet") |> assign(:replay, RescueCloud.Replay.counts())}
  end

  @impl true
  def handle_info(:verified, socket),
    do: {:noreply, assign(socket, :replay, RescueCloud.Replay.counts())}

  def handle_info(_, socket), do: {:noreply, socket}

  @impl true
  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash}>
      <h1 id="dashboard-title" class="text-2xl font-semibold">Rescue triage cloud</h1>
      <p id="dashboard-status">No devices have synced yet.</p>
      <p id="replay-mismatches" data-count={@replay.mismatch}>
        Replay mismatches: {@replay.mismatch}
      </p>
    </Layouts.app>
    """
  end
end
