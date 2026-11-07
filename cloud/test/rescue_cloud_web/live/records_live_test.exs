defmodule RescueCloudWeb.RecordsLiveTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import RescueCloud.RecordsFixtures

  @t ~U[2026-11-07 14:00:00Z]

  setup do
    sync!(
      [
        event("A1", 1, "received", "2026-11-07T14:02:11Z",
          raw_text: "3 kids on the roof on Westheimer"
        ),
        event("A1", 2, "extracted", "2026-11-07T14:02:15Z"),
        event("A1", 3, "decided", "2026-11-07T14:02:16Z", tier: "P1", rule_id: "R-P1-01"),
        event("A1", 4, "escalated", "2026-11-07T14:10:00Z"),
        event("B2", 1, "received", "2026-11-07T14:05:00Z", raw_text: "need water on Bellaire"),
        event("B2", 2, "decided", "2026-11-07T14:05:05Z", tier: "P3", rule_id: "R-P3-01"),
        event("C3", 1, "received", "2026-11-07T14:06:00Z",
          raw_text: "where is my 100% dog_house"
        ),
        event("C3", 2, "decided", "2026-11-07T14:06:05Z", outcome: "abstain")
      ],
      @t
    )

    :ok
  end

  defp rows(view) do
    view
    |> render()
    |> LazyHTML.from_fragment()
    |> LazyHTML.query("#records-table tbody tr")
    |> LazyHTML.attribute("id")
  end

  test "lists every request", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/records")

    assert Enum.sort(rows(view)) ==
             Enum.sort(["record-" <> rid("A1"), "record-" <> rid("B2"), "record-" <> rid("C3")])

    assert has_element?(view, "#record-#{rid("A1")} [data-role=tier]", "P1")
    assert has_element?(view, "#record-#{rid("A1")} [data-role=status]", "escalated")
  end

  test "abstain follow-ups rank at their provisional_tier, after ranked rows of it (R1)", %{
    conn: conn
  } do
    sync!(
      [
        event("D4", 1, "received", "2026-11-07T14:07:00Z"),
        event("D4", 2, "decided", "2026-11-07T14:07:05Z",
          outcome: "abstain",
          provisional_tier: "P1"
        ),
        event("E5", 1, "received", "2026-11-07T14:08:00Z"),
        event("E5", 2, "decided", "2026-11-07T14:08:05Z",
          outcome: "abstain",
          provisional_tier: "P3"
        )
      ],
      @t
    )

    {:ok, view, _} = live(conn, ~p"/records")

    # P1 (A1), P1? (D4), P2? (C3, the fixture default), P3 (B2), P3? (E5): a "P1?" abstain
    # ranks with the P1 rows, not after every P3; within a tier ranked rows come first, and the
    # newest abstain (E5) does not jump ahead of an older but more urgent one
    assert rows(view) ==
             Enum.map(~w(A1 D4 C3 B2 E5), &("record-" <> rid(&1)))

    assert has_element?(view, "#record-#{rid("D4")} [data-role=tier]", "P1?")
    assert has_element?(view, "#record-#{rid("E5")} [data-role=tier]", "P3?")

    # The tier filter matches the provisional tier too, so filtering P1 shows the "P1?" abstain.
    {:ok, view, _} = live(conn, ~p"/records?tier=P1")
    assert rows(view) == Enum.map(~w(A1 D4), &("record-" <> rid(&1)))

    {:ok, view, _} = live(conn, ~p"/records?tier=P3")
    assert rows(view) == Enum.map(~w(B2 E5), &("record-" <> rid(&1)))
  end

  test "searches by text (case-insensitive, LIKE wildcards are literal)", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/records")

    view |> form("#records-search", %{"q" => "BELLAIRE"}) |> render_submit()
    assert_patch(view, ~p"/records?q=BELLAIRE")
    assert rows(view) == ["record-" <> rid("B2")]

    view |> form("#records-search", %{"q" => "100%"}) |> render_submit()
    assert rows(view) == ["record-" <> rid("C3")]

    view |> form("#records-search", %{"q" => "_"}) |> render_submit()
    assert rows(view) == ["record-" <> rid("C3")]

    view |> form("#records-search", %{"q" => "nothing matches this"}) |> render_submit()
    assert has_element?(view, "#records-empty")
  end

  test "filters by tier and status, combined with text", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/records?tier=P3")
    assert rows(view) == ["record-" <> rid("B2")]

    {:ok, view, _} = live(conn, ~p"/records")
    view |> form("#records-search", %{"status" => "escalated"}) |> render_submit()
    assert rows(view) == ["record-" <> rid("A1")]

    view |> form("#records-search", %{"status" => "decided", "q" => "dog"}) |> render_submit()
    assert rows(view) == ["record-" <> rid("C3")]

    view |> form("#records-search", %{"tier" => "P1", "status" => "decided"}) |> render_submit()
    assert has_element?(view, "#records-empty")
  end

  test "detail shows rule_id, versions and event history in laptop time order", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/records")
    view |> element("#record-#{rid("A1")} a[data-role=open]") |> render_click()
    assert_patch(view, ~p"/records/#{rid("A1")}")

    assert has_element?(view, "#record-detail [data-role=rule-id]", "R-P1-01")
    assert has_element?(view, "#record-detail [data-role=rules-version]", rules_version())
    assert has_element?(view, "#record-detail [data-role=model-version]", model_version())
    assert has_element?(view, "#record-detail [data-role=tier]", "P1")

    types =
      view
      |> render()
      |> LazyHTML.from_fragment()
      |> LazyHTML.query("#record-events tbody tr [data-role=type]")
      |> Enum.map(&LazyHTML.text/1)
      |> Enum.map(&String.trim/1)

    assert types == ~w(received extracted decided escalated)

    assert has_element?(
             view,
             "#event-#{eid("A1", 1)} [data-role=occurred-at]",
             "2026-11-07 14:02:11 UTC"
           )

    assert has_element?(view, "#event-#{eid("A1", 3)} [data-role=rules-version]", rules_version())
  end

  test "detail keeps the search filters and handles unknown ids", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/records/#{rid("C3")}?status=decided")
    assert has_element?(view, "#record-detail [data-role=rule-id]", "R-ABS-LOC")
    assert has_element?(view, "#record-detail [data-role=outcome]", "abstain")
    assert Enum.sort(rows(view)) == Enum.sort(["record-" <> rid("B2"), "record-" <> rid("C3")])

    {:ok, view, _} = live(conn, ~p"/records/#{rid("ZZ")}")
    assert has_element?(view, "#record-missing")
    refute has_element?(view, "#record-detail")
  end
end
