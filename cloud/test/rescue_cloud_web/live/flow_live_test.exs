defmodule RescueCloudWeb.FlowLiveTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import RescueCloud.RecordsFixtures

  defp intake(tag, synced \\ ~U[2026-11-07 14:02:16Z]) do
    sync!(
      [
        event(tag, 1, "received", "2026-11-07T14:02:11Z", raw_text: "water rising #{tag}"),
        event(tag, 2, "extracted", "2026-11-07T14:02:11Z"),
        event(tag, 3, "decided", "2026-11-07T14:02:11Z", tier: "P1")
      ],
      synced
    )
  end

  test "lists requests with the stage each one has reached", %{conn: conn} do
    intake("A1")
    {:ok, view, _} = live(conn, ~p"/flow")

    row = "#flow-row-#{rid("A1")}"
    assert has_element?(view, row, "water rising A1")
    assert has_element?(view, "#{row} [data-stage=received][data-state=done]")
    assert has_element?(view, "#{row} [data-stage=extracted][data-state=done]", "3 people")
    assert has_element?(view, "#{row} [data-stage=decided][data-state=done]", "P1")
    assert has_element?(view, "#{row} [data-stage=synced][data-state=done]", "+5s")
    assert has_element?(view, "#{row} [data-stage=assigned][data-state=waiting]")
    assert has_element?(view, "#flow-count-synced", "1")
  end

  test "follows the latest request and moves it on as events arrive", %{conn: conn} do
    intake("A1")
    {:ok, view, _} = live(conn, ~p"/flow")

    assert has_element?(view, "#flow-detail[data-request='#{rid("A1")}']")
    assert has_element?(view, "#flow-following")
    assert has_element?(view, "#seq-7[data-state=pending]")

    [assigned] = sync!([event("A1", 4, "assigned", "2026-11-07T14:06:11Z")])
    send(view.pid, {:event, assigned})

    row = "#flow-row-#{rid("A1")}"
    assert has_element?(view, "#{row} [data-stage=assigned][data-state=done]", "boat-crew-7")
    assert has_element?(view, "#{row}[data-fresh]")
    assert has_element?(view, "#seq-7[data-state=done]", "boat-crew-7")
    assert has_element?(view, "#flow-count-assigned", "1")
  end

  test "a new request shows up at the top", %{conn: conn} do
    intake("A1")
    {:ok, view, _} = live(conn, ~p"/flow")

    [received | _] = intake("B2", ~U[2026-11-07 14:03:00Z])
    send(view.pid, {:event, received})

    ids =
      view
      |> render()
      |> LazyHTML.from_fragment()
      |> LazyHTML.query("#flow-requests tbody tr")
      |> LazyHTML.attribute("id")

    assert ids == ["flow-row-#{rid("B2")}", "flow-row-#{rid("A1")}"]
  end

  test "a pinned request shows its sequence diagram and the model's answer", %{conn: conn} do
    intake("A1")
    intake("B2", ~U[2026-11-07 14:03:00Z])
    {:ok, view, _} = live(conn, ~p"/flow/#{rid("A1")}")

    assert has_element?(view, "#flow-detail[data-request='#{rid("A1")}']")
    refute has_element?(view, "#flow-following")
    assert has_element?(view, "#seq-0[data-state=done]", "POST /requests")
    assert has_element?(view, "#seq-2[data-state=done]", "extracted")
    assert has_element?(view, "#seq-4[data-state=done]", "prioritized P1")
    assert has_element?(view, "#flow-model-json", "people_count")
    assert has_element?(view, "#flow-engine-json", "R-P1-01")

    # Pinned: an update to another request does not take the detail away.
    [b] = sync!([event("B2", 4, "assigned", "2026-11-07T14:06:11Z")])
    send(view.pid, {:event, b})
    assert has_element?(view, "#flow-detail[data-request='#{rid("A1")}']")
  end

  test "an unknown request id says so", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/flow/#{rid("ZZ")}")
    assert has_element?(view, "#flow-missing")
  end

  test "empty state", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/flow")
    assert has_element?(view, "#flow-empty")
  end

  test "accessibility: nav link, live region, captioned tables", %{conn: conn} do
    intake("A1")
    {:ok, view, html} = live(conn, ~p"/flow")
    assert html =~ ~s(aria-current="page")
    assert has_element?(view, "nav a[href='/flow']")
    assert has_element?(view, "#flow-live[aria-live=polite]")

    doc = view |> render() |> LazyHTML.from_fragment()

    for t <- doc |> LazyHTML.query("table") |> Enum.to_list() do
      assert t |> LazyHTML.query("caption") |> Enum.count() > 0
      ths = t |> LazyHTML.query("thead th") |> Enum.to_list()
      assert Enum.all?(ths, &(LazyHTML.attribute(&1, "scope") == ["col"]))
    end
  end
end
