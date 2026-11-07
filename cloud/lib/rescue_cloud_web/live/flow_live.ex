defmodule RescueCloudWeb.FlowLive do
  @moduledoc """
  Call-flow page at `/flow`: each request's journey from intake to the volunteer crew, live.

  The table shows the requests with the newest activity, one row each, with a cell per stage
  of `RescueCloud.Flow` (received, model, engine, cloud, crew, closed). Below it, a sequence
  diagram of one request: every hop (intake, field app, tagger model, decision engine, field DB,
  cloud, crew) with what was sent, plus the model's answer and the engine's decision as JSON.

  `/flow` follows the request that changed last; `/flow/:request_id` pins one. Updates arrive on
  the PubSub `"events"` topic, one `{:event, event}` per stored event; only that request is
  reloaded, and its row moves to the top and is marked `data-fresh`.
  """
  use RescueCloudWeb, :live_view

  alias RescueCloud.Flow

  @limit 25
  @stage_labels [
    received: "Received",
    extracted: "Model read it",
    decided: "Engine decided",
    synced: "In the cloud",
    assigned: "Crew sent",
    closed: "Closed"
  ]

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket), do: Phoenix.PubSub.subscribe(RescueCloud.PubSub, "events")

    {:ok,
     socket
     |> assign(:page_title, "Call flow")
     |> assign(:fresh, nil)
     |> assign(:flows, Flow.recent(limit: @limit))}
  end

  @impl true
  def handle_params(%{"request_id" => rid}, _uri, socket) do
    {:noreply, assign(socket, pinned: rid, selected: Flow.get(rid))}
  end

  def handle_params(_params, _uri, socket) do
    {:noreply, assign(socket, pinned: nil, selected: List.first(socket.assigns.flows))}
  end

  @impl true
  def handle_info({:event, %{request_id: rid}}, socket) do
    case Flow.get(rid) do
      nil ->
        {:noreply, socket}

      flow ->
        flows =
          [flow | Enum.reject(socket.assigns.flows, &(&1.request_id == rid))]
          |> Enum.take(@limit)

        selected =
          case socket.assigns.pinned do
            nil -> flow
            ^rid -> flow
            _ -> socket.assigns.selected
          end

        {:noreply, assign(socket, flows: flows, selected: selected, fresh: rid)}
    end
  end

  def handle_info(_, socket), do: {:noreply, socket}

  @impl true
  def render(assigns) do
    assigns =
      assigns
      |> assign(:stage_labels, @stage_labels)
      |> assign(:counts, Enum.frequencies_by(assigns.flows, & &1.stage))

    ~H"""
    <Layouts.app flash={@flash} current="/flow">
      <h1 id="flow-title" class="text-3xl font-semibold">Call flow</h1>
      <p class="text-base max-w-3xl">
        Every message from intake to the volunteer crew: the field app stores it, the tagger model
        reads it, the decision engine ranks it, the cloud receives it, and a crew is sent.
        Updates live as events reach the cloud.
      </p>

      <div id="flow-live" aria-live="polite" class="space-y-6">
        <p :if={@flows == []} id="flow-empty">
          No requests yet. Send one from the intake simulator to a field laptop that syncs here.
        </p>

        <section :if={@flows != []} aria-labelledby="flow-counts-title">
          <h2 id="flow-counts-title" class="text-lg font-semibold">
            Where the last {length(@flows)} requests are now
          </h2>
          <dl class="grid grid-cols-3 sm:grid-cols-6 gap-2 mt-2">
            <div
              :for={{stage, label} <- @stage_labels}
              class="rounded-box border border-base-300 p-3"
            >
              <dt class="text-sm">{label}</dt>
              <dd id={"flow-count-#{stage}"} class="text-2xl font-semibold">
                {Map.get(@counts, stage, 0)}
              </dd>
            </div>
          </dl>
        </section>

        <div :if={@flows != []} class="overflow-x-auto">
          <table id="flow-requests" class="table">
            <caption class="sr-only">Recent requests and the stage each one has reached</caption>
            <thead>
              <tr>
                <th scope="col">Request</th>
                <th :for={{_stage, label} <- @stage_labels} scope="col">{label}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                :for={f <- @flows}
                id={"flow-row-#{f.request_id}"}
                data-fresh={@fresh == f.request_id}
                class={[
                  "hover:bg-base-200",
                  @fresh == f.request_id && "bg-info/10",
                  @selected && @selected.request_id == f.request_id &&
                    "outline outline-2 outline-primary"
                ]}
              >
                <td class="max-w-xs">
                  <.link patch={~p"/flow/#{f.request_id}"} class="link font-mono text-sm">
                    {short(f.request_id)}
                  </.link>
                  <span class="text-xs block opacity-70">{f.device_id}</span>
                  <span class="text-sm block truncate" title={f.raw_text}>{f.raw_text}</span>
                </td>
                <td
                  :for={{stage, _} <- @stage_labels}
                  data-stage={stage}
                  data-state={Flow.state(f, stage)}
                  class="text-sm align-top"
                >
                  <.stage_cell flow={f} stage={stage} state={Flow.state(f, stage)} />
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <p :if={@pinned && @selected == nil} id="flow-missing">
          The cloud has no events for request {@pinned}.
          <.link patch={~p"/flow"} class="link">Follow the latest request</.link>
        </p>

        <.detail :if={@selected} flow={@selected} pinned={@pinned} />
      </div>
    </Layouts.app>
    """
  end

  attr :flow, :map, required: true
  attr :stage, :atom, required: true
  attr :state, :atom, required: true

  defp stage_cell(%{state: :waiting} = assigns) do
    ~H"""
    <span class="opacity-60 italic">waiting</span>
    """
  end

  defp stage_cell(%{state: :skipped} = assigns) do
    ~H"""
    <span class="opacity-60">not needed</span>
    """
  end

  defp stage_cell(%{stage: :received} = assigns) do
    ~H"""
    <span class="font-semibold">✓ {clock(@flow.received.occurred_at)}</span>
    <span class="block text-xs">{@flow.channel}</span>
    """
  end

  defp stage_cell(%{stage: :extracted} = assigns) do
    ~H"""
    <span class="font-semibold">✓ {people(get_in(@flow.extraction, ["people_count", "value"]))}</span>
    <span class="block text-xs">
      location {get_in(@flow.extraction, ["location", "type"])} ({get_in(@flow.extraction, [
        "location",
        "confidence"
      ])})
    </span>
    """
  end

  defp stage_cell(%{stage: :decided} = assigns) do
    ~H"""
    <.status_badge kind={decision_kind(@flow.decision)}>
      {decision_text(@flow.decision)}
    </.status_badge>
    <span class="block text-xs font-mono">{@flow.decision["rule_id"]}</span>
    """
  end

  defp stage_cell(%{stage: :synced} = assigns) do
    ~H"""
    <span class="font-semibold">✓ {clock(@flow.synced_at)}</span>
    <span :if={@flow.cloud_lag_s} class="block text-xs">+{@flow.cloud_lag_s}s</span>
    """
  end

  defp stage_cell(%{stage: :assigned} = assigns) do
    ~H"""
    <span class="font-semibold">✓ {@flow.crew}</span>
    <span :if={@flow.assign_after_s} class="block text-xs">
      after {duration(@flow.assign_after_s)}
    </span>
    <.status_badge :if={@flow.escalated} kind={:alarm}>escalated</.status_badge>
    """
  end

  defp stage_cell(%{stage: :closed} = assigns) do
    ~H"""
    <span class="font-semibold">✓ {String.replace(@flow.closed.type, "_", " ")}</span>
    <span :if={@flow.closed.payload["rescued_count"]} class="block text-xs">
      {@flow.closed.payload["rescued_count"]} brought out
    </span>
    """
  end

  attr :flow, :map, required: true
  attr :pinned, :string, default: nil

  defp detail(assigns) do
    assigns =
      assigns
      |> assign(:participants, Flow.participants())
      |> assign(:steps, Flow.sequence(assigns.flow))

    ~H"""
    <section
      id="flow-detail"
      data-request={@flow.request_id}
      aria-labelledby="flow-detail-title"
      class="space-y-4"
    >
      <p :if={@pinned == nil} id="flow-following" class="text-sm">
        Following the latest request.
        <.link patch={~p"/flow/#{@flow.request_id}"} class="link">Pin this one</.link>
      </p>
      <p :if={@pinned} class="text-sm">
        Pinned. <.link patch={~p"/flow"} class="link">Follow the latest request</.link>
      </p>
      <h2 id="flow-detail-title" class="text-2xl font-semibold">
        Request <span class="font-mono">{@flow.request_id}</span>
      </h2>
      <p class="text-base">
        From {@flow.device_id}. <span :if={@flow.waiting_for}>Waiting for {@flow.waiting_for}.</span>
        <span :if={@flow.waiting_for == nil}>Nothing left to do.</span>
      </p>
      <blockquote :if={@flow.raw_text} class="border-l-4 border-base-300 pl-3 italic">
        {@flow.raw_text}
      </blockquote>

      <h3 class="text-xl font-semibold">Sequence</h3>
      <div class="overflow-x-auto">
        <div
          id="flow-seq"
          class="grid min-w-[60rem] gap-y-1"
          style={"grid-template-columns: repeat(#{length(@participants)}, minmax(0, 1fr))"}
        >
          <div
            :for={{{_key, label}, col} <- Enum.with_index(@participants, 1)}
            class="text-sm font-semibold text-center rounded-box bg-base-200 p-2 mx-1"
            style={"grid-column: #{col}; grid-row: 1"}
          >
            {label}
          </div>
          <div
            :for={col <- 1..length(@participants)}
            aria-hidden="true"
            class="justify-self-center border-l-2 border-dashed border-base-300"
            style={"grid-column: #{col}; grid-row: 2 / span #{length(@steps)}"}
          />
          <div
            :for={{step, i} <- Enum.with_index(@steps)}
            id={"seq-#{i}"}
            data-state={step.state}
            class={["relative z-10 py-1", step.state != :done && "opacity-50"]}
            style={seq_style(step, i, @participants)}
          >
            <.seq_arrow step={step} span={span(step, @participants)} />
          </div>
        </div>
      </div>
      <p class="text-sm opacity-70">
        The laptop stores received, extracted and decided in one transaction, so the model and
        engine hops share its time. Faded hops have not happened yet.
      </p>

      <div class="grid gap-4 lg:grid-cols-2">
        <section aria-labelledby="flow-model-title">
          <h3 id="flow-model-title" class="text-xl font-semibold">Model's answer</h3>
          <p class="text-sm font-mono break-all">{@flow.model_version}</p>
          <pre
            :if={@flow.extraction}
            id="flow-model-json"
            class="bg-base-200 rounded-box p-3 text-xs overflow-auto max-h-96"
          >{pretty(@flow.extraction)}</pre>
          <p :if={@flow.extraction == nil} class="italic">Not in the cloud yet.</p>
        </section>
        <section aria-labelledby="flow-engine-title">
          <h3 id="flow-engine-title" class="text-xl font-semibold">Engine decision</h3>
          <p class="text-sm font-mono break-all">{@flow.rules_version}</p>
          <p :if={@flow.decision && @flow.decision["follow_up_question"]} class="font-semibold">
            Follow-up: {@flow.decision["follow_up_question"]}
          </p>
          <pre
            :if={@flow.decision}
            id="flow-engine-json"
            class="bg-base-200 rounded-box p-3 text-xs overflow-auto max-h-96"
          >{pretty(@flow.decision)}</pre>
          <p :if={@flow.decision == nil} class="italic">Not in the cloud yet.</p>
        </section>
      </div>
    </section>
    """
  end

  attr :step, :map, required: true
  attr :span, :integer, required: true

  defp seq_arrow(assigns) do
    ~H"""
    <div style={"margin: 0 #{50 / @span}%"}>
      <p class="text-sm text-center font-semibold">
        <span class="sr-only">{@step.from} to {@step.to}:</span>
        {@step.label}
        <span :if={@step.at} class="text-xs font-normal">{clock(@step.at)}</span>
        <span :if={@step.state == :skipped} class="text-xs font-normal">(not needed)</span>
      </p>
      <div class={["flex items-center", leftward?(@step) && "flex-row-reverse"]} aria-hidden="true">
        <span class={[
          "flex-1 border-t-2",
          @step.state == :done && "border-primary",
          @step.state != :done && "border-dashed border-base-content/50"
        ]} />
        <.icon
          name="hero-play-solid"
          class={["size-4", leftward?(@step) && "rotate-180", @step.state == :done && "text-primary"]}
        />
      </div>
      <p :if={@step.detail} class="text-xs text-center truncate" title={@step.detail}>
        {@step.detail}
      </p>
    </div>
    """
  end

  defp col(key, participants), do: Enum.find_index(participants, &(elem(&1, 0) == key)) + 1

  defp span(step, participants),
    do: abs(col(step.from, participants) - col(step.to, participants)) + 1

  defp leftward?(%{from: from, to: to}) do
    keys = Keyword.keys(Flow.participants())
    Enum.find_index(keys, &(&1 == to)) < Enum.find_index(keys, &(&1 == from))
  end

  defp seq_style(step, i, participants) do
    [a, b] = Enum.sort([col(step.from, participants), col(step.to, participants)])
    "grid-column: #{a} / #{b + 1}; grid-row: #{i + 2}"
  end

  defp decision_kind(%{"outcome" => "abstain"}), do: :warn
  defp decision_kind(%{"priority_tier" => "P1"}), do: :alarm
  defp decision_kind(_), do: :neutral

  defp decision_text(%{"outcome" => "prioritized", "priority_tier" => t}), do: t

  defp decision_text(%{"outcome" => "abstain", "provisional_tier" => t}) when is_binary(t),
    do: "check first (#{t}?)"

  defp decision_text(%{"outcome" => "abstain"}), do: "check first"
  defp decision_text(%{"outcome" => "duplicate", "priority_tier" => t}), do: "duplicate #{t}"
  defp decision_text(%{"outcome" => "not_a_request"}), do: "not a request"
  defp decision_text(%{"outcome" => o}), do: o

  defp people(nil), do: "count unknown"
  defp people(1), do: "1 person"
  defp people(n), do: "#{n} people"

  defp short(rid), do: "…" <> String.slice(rid, -8, 8)

  defp clock(%DateTime{} = dt), do: Calendar.strftime(dt, "%H:%M:%S")
  defp clock(_), do: ""

  defp duration(s) when s < 60, do: "#{s}s"
  defp duration(s) when s < 3600, do: "#{div(s, 60)}m #{rem(s, 60)}s"
  defp duration(s), do: "#{div(s, 3600)}h #{div(rem(s, 3600), 60)}m"

  defp pretty(map), do: Jason.encode!(map, pretty: true)
end
