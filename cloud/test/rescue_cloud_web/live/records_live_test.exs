defmodule RescueCloudWeb.RecordsLiveTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest

  alias RescueCloud.{Events, Projection}

  @contracts Path.expand("../../../../contracts", __DIR__)
  @rules "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
  @model "hf:rescue-org/rescue-tagger@4d5e6f7a8b"

  defp payload(type),
    do:
      @contracts
      |> Path.join("examples/payload-#{type}.valid.json")
      |> File.read!()
      |> Jason.decode!()

  defp rid(tag), do: "01JCB000000000000000RQ00" <> tag
  defp eid(tag, n), do: "01JCB00000000000000E" <> tag <> String.pad_leading("#{n}", 4, "0")

  defp ev(req, n, type, at, device, p) do
    %{
      "event_id" => eid(String.slice(req, -2, 2), n),
      "request_id" => req,
      "type" => type,
      "device_id" => device,
      "occurred_at" => at,
      "rules_version" => if(type == "decided", do: @rules),
      "model_version" => if(type in ~w(extracted decided), do: @model),
      "payload" => p
    }
  end

  defp seed(req, device, text, tier, outcome, rule, at) do
    rec = %{payload("received") | "raw_text" => text}
    dec = put_in(payload("decided"), ["engine_output", "priority_tier"], tier)
    dec = put_in(dec, ["engine_output", "outcome"], outcome)
    dec = put_in(dec, ["engine_output", "rule_id"], rule)
    dec = put_in(dec, ["engine_output", "duplicate_of"], nil)

    events = [
      ev(req, 1, "received", at, device, rec),
      ev(req, 2, "extracted", at, device, payload("extracted")),
      ev(req, 3, "decided", at, device, dec)
    ]

    {:ok, %{inserted: ins, rejected: rej}} = Events.append_batch(events)
    assert rej == []
    {:ok, _} = Projection.apply_events(ins)
  end

  setup do
    seed(
      rid("01"),
      "field-03",
      "Family stuck on roof near bayou",
      "P1",
      "prioritized",
      "R-P1-01",
      "2026-11-07T10:00:00Z"
    )

    seed(
      rid("02"),
      "field-04",
      "Need diapers at the shelter",
      "P3",
      "prioritized",
      "R-P3-01",
      "2026-11-07T11:00:00Z"
    )

    :ok
  end

  test "lists and searches by text, tier and status", %{conn: conn} do
    {:ok, view, html} = live(conn, ~p"/records")
    assert html =~ rid("01") and html =~ rid("02")

    view |> form("#search-form", %{q: "roof"}) |> render_change()
    html = render(view)
    assert html =~ rid("01")
    refute html =~ rid("02")

    {:ok, view, _} = live(conn, ~p"/records")
    view |> form("#search-form", %{tier: "P3"}) |> render_change()
    assert has_element?(view, "#record-#{rid("02")}")
    refute has_element?(view, "#record-#{rid("01")}")

    view |> form("#search-form", %{tier: "", status: "decided"}) |> render_change()
    assert has_element?(view, "#record-#{rid("01")}")

    view |> element("#search-form") |> render_change(%{status: "rescued"})
    assert has_element?(view, "#records-empty")
  end

  test "detail shows event history, rule_id and versions", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/records")
    view |> element("#record-#{rid("01")} a") |> render_click()

    assert has_element?(view, "#record-detail [data-role=rule-id]", "R-P1-01")
    assert has_element?(view, "#record-detail [data-role=rules-version]", @rules)
    assert has_element?(view, "#record-detail [data-role=model-version]", @model)

    for t <- ~w(received extracted decided) do
      assert has_element?(view, "#event-history [data-role=type]", t)
    end
  end

  test "unknown id redirects back to the list", %{conn: conn} do
    assert {:error, {:live_redirect, %{to: "/records"}}} = live(conn, ~p"/records/nope")
  end
end
