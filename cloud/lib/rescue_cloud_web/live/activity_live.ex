defmodule RescueCloudWeb.ActivityLive do
  @moduledoc """
  `/activity`: what each laptop did, from the synced event log: requests logged, decisions,
  abstains and escalations, with laptop (`occurred_at`) timestamps. Refreshes on PubSub `"events"`.
  """
  use RescueCloudWeb, :live_view

  alias RescueCloud.Records

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket), do: Phoenix.PubSub.subscribe(RescueCloud.PubSub, "events")
    {:ok, socket |> assign(:page_title, "Field activity") |> load()}
  end

  @impl true
  def handle_info({:event, _}, socket), do: {:noreply, load(socket)}
  def handle_info(_, socket), do: {:noreply, socket}

  defp load(socket), do: assign(socket, :devices, Records.device_activity())

  @impl true
  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash}>
      <h1 id="activity-title" class="text-2xl font-semibold">Field activity</h1>
      <p :if={@devices == []} id="activity-empty">No device activity yet.</p>
      <section :for={d <- @devices} id={"activity-#{d.device_id}"} class="my-4">
        <h2 class="text-xl font-semibold">{d.device_id}</h2>
        <p>
          <span data-role="requests">{d.requests}</span>
          requests, <span data-role="decisions">{d.decisions}</span>
          decisions, <span data-role="abstains">{d.abstains}</span>
          abstains, <span data-role="escalations">{d.escalations}</span>
          escalations
        </p>
        <ul>
          <li :for={e <- d.recent} id={"act-#{e.event_id}"}>
            <span data-role="at">{Calendar.strftime(e.occurred_at, "%Y-%m-%d %H:%M:%S UTC")}</span>
            <span data-role="type">{label(e)}</span>
            <.link navigate={~p"/records/#{e.request_id}"}>{e.request_id}</.link>
          </li>
        </ul>
      </section>
    </Layouts.app>
    """
  end

  defp label(%{type: "decided"} = e) do
    if Records.outcome(e) == "abstain", do: "abstain", else: "decided"
  end

  defp label(e), do: e.type
end
