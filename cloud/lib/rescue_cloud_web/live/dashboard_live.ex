defmodule RescueCloudWeb.DashboardLive do
  @moduledoc """
  Dashboard page at `/`. Shows the replay verifier (C9) summary: how many decided events
  matched, mismatched (bug or tampering) or had no engine build, with a button to verify
  pending decisions now. Updates on the PubSub `"replay"` topic.
  """
  use RescueCloudWeb, :live_view

  alias RescueCloud.Replay

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket), do: Phoenix.PubSub.subscribe(RescueCloud.PubSub, Replay.topic())

    {:ok, socket |> assign(:page_title, "Fleet") |> assign(:replay, Replay.counts())}
  end

  @impl true
  def handle_event("verify", _params, socket) do
    {:ok, _run} = Replay.verify()
    {:noreply, assign(socket, :replay, Replay.counts())}
  end

  @impl true
  def handle_info({:replay, _run}, socket),
    do: {:noreply, assign(socket, :replay, Replay.counts())}

  @impl true
  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash}>
      <h1 id="dashboard-title" class="text-2xl font-semibold">Rescue triage cloud</h1>
      <p id="dashboard-status">No devices have synced yet.</p>

      <section id="replay-summary" class="mt-6 space-y-2">
        <h2 class="text-xl font-semibold">Replay verifier</h2>
        <p>
          <span
            id="replay-mismatches"
            class={["font-semibold", @replay["mismatch"] > 0 && "text-error"]}
          >
            {@replay["mismatch"]} mismatches
          </span>
          · <span id="replay-matches">{@replay["match"]} match</span>
          · <span id="replay-missing">{@replay["missing_version"]} missing version</span>
          · <span id="replay-errors">{@replay["error"]} engine errors</span>
        </p>
        <.button id="replay-verify" phx-click="verify">Verify decisions</.button>
      </section>
    </Layouts.app>
    """
  end
end
