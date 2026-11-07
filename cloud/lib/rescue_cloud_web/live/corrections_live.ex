defmodule RescueCloudWeb.CorrectionsLive do
  @moduledoc """
  Corrections page at `/corrections`: a form to correct a field of a request, the review queue
  (approve / reject), and the export of approved corrections to JSONL for the next training run.
  """
  use RescueCloudWeb, :live_view

  alias RescueCloud.Corrections

  @impl true
  def mount(_params, _session, socket) do
    {:ok,
     socket
     |> assign(:page_title, "Corrections")
     |> assign(:filter, "pending")
     |> assign(
       :form,
       to_form(
         %{
           "request_id" => "",
           "field" => "",
           "new_value" => "",
           "corrected_by" => "",
           "note" => ""
         },
         as: :correction
       )
     )
     |> assign(:reviewer, "")
     |> assign(:export_result, nil)
     |> load()}
  end

  defp load(socket), do: assign(socket, :corrections, Corrections.list(socket.assigns.filter))

  @impl true
  def handle_event("submit", %{"correction" => params}, socket) do
    attrs = Map.put(params, "new_value", parse_value(params["new_value"]))

    case Corrections.create(String.trim(params["request_id"] || ""), attrs) do
      {:ok, _event} ->
        {:noreply,
         socket
         |> put_flash(:info, "Correction recorded; it is pending review.")
         |> assign(
           :form,
           to_form(
             Map.take(params, ["corrected_by"])
             |> Map.merge(%{"request_id" => "", "field" => "", "new_value" => "", "note" => ""}),
             as: :correction
           )
         )
         |> load()}

      {:error, :not_found} ->
        {:noreply,
         socket
         |> assign(:form, to_form(params, as: :correction))
         |> put_flash(:error, "Unknown request id.")}

      {:error, _} ->
        {:noreply,
         socket
         |> assign(:form, to_form(params, as: :correction))
         |> put_flash(:error, "Field and your name are required.")}
    end
  end

  def handle_event("filter", %{"status" => status}, socket) do
    {:noreply, socket |> assign(:filter, status) |> load()}
  end

  def handle_event("reviewer", %{"reviewer" => name}, socket) do
    {:noreply, assign(socket, :reviewer, name)}
  end

  def handle_event("review", %{"id" => id, "decision" => decision}, socket) do
    decision = if decision == "approve", do: :approve, else: :reject

    case Corrections.review(id, decision, socket.assigns.reviewer) do
      {:ok, _} ->
        {:noreply, load(socket)}

      {:error, :invalid} ->
        {:noreply, put_flash(socket, :error, "Enter your name as reviewer first.")}

      {:error, :not_found} ->
        {:noreply, put_flash(socket, :error, "Correction not found.")}
    end
  end

  def handle_event("export", _params, socket) do
    case Corrections.export() do
      {:ok, result} -> {:noreply, assign(socket, :export_result, result)}
      {:error, _} -> {:noreply, put_flash(socket, :error, "Export failed.")}
    end
  end

  # "4" -> 4, "true" -> true, "\"x\"" -> "x"; anything that is not JSON stays a string.
  defp parse_value(raw) do
    case Jason.decode(raw || "") do
      {:ok, v} -> v
      _ -> raw
    end
  end

  @impl true
  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash}>
      <h1 id="corrections-title" class="text-2xl font-semibold">Corrections</h1>

      <.form for={@form} id="correction-form" phx-submit="submit" class="mt-4 max-w-xl">
        <.input field={@form[:request_id]} label="Request id" />
        <.input field={@form[:field]} label="Field (dotted path, e.g. people_count.value)" />
        <.input field={@form[:new_value]} label="New value (JSON or plain text)" />
        <.input field={@form[:corrected_by]} label="Your name" />
        <.input field={@form[:note]} label="Note" />
        <button id="correction-submit" type="submit" class="btn btn-primary">Submit correction</button>
      </.form>

      <h2 class="text-xl font-semibold mt-8">Review queue</h2>
      <form id="reviewer-form" phx-change="reviewer" class="mt-2">
        <input
          type="text"
          name="reviewer"
          value={@reviewer}
          placeholder="Reviewer name"
          class="input"
        />
      </form>
      <form id="filter-form" phx-change="filter" class="mt-2">
        <select name="status" id="filter-status" class="select">
          <option
            :for={s <- ["pending", "approved", "rejected", ""]}
            value={s}
            selected={s == @filter}
          >
            {if s == "", do: "all", else: s}
          </option>
        </select>
      </form>
      <p :if={@corrections == []} id="corrections-empty">No corrections.</p>
      <table :if={@corrections != []} id="corrections-table" class="table mt-2">
        <thead>
          <tr>
            <th>Request</th><th>Field</th><th>Old</th><th>New</th><th>By</th><th>Status</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr :for={c <- @corrections} id={"correction-#{c.event.event_id}"}>
            <td>{c.event.request_id}</td>
            <td>{c.event.payload["field"]}</td>
            <td>{inspect(c.event.payload["old_value"])}</td>
            <td>{inspect(c.event.payload["new_value"])}</td>
            <td>{c.event.payload["corrected_by"]}</td>
            <td data-role="status">{c.status}</td>
            <td>
              <button
                phx-click="review"
                phx-value-id={c.event.event_id}
                phx-value-decision="approve"
                class="btn btn-sm"
                data-role="approve"
              >Approve</button>
              <button
                phx-click="review"
                phx-value-id={c.event.event_id}
                phx-value-decision="reject"
                class="btn btn-sm"
                data-role="reject"
              >Reject</button>
            </td>
          </tr>
        </tbody>
      </table>

      <h2 class="text-xl font-semibold mt-8">Export</h2>
      <p class="text-sm">Approved corrections go to a training dataset, never to the eval sets.</p>
      <button id="export-button" phx-click="export" class="btn mt-2">Export approved corrections</button>
      <p :if={@export_result} id="export-result">
        Wrote {@export_result.count} corrections to {@export_result.path}
      </p>
    </Layouts.app>
    """
  end
end
