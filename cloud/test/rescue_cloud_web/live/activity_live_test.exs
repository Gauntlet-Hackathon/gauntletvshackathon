defmodule RescueCloudWeb.ActivityLiveTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest

  alias RescueCloud.Events

  @contracts Path.expand("../../../../contracts", __DIR__)

  defp payload(type),
    do:
      @contracts
      |> Path.join("examples/payload-#{type}.valid.json")
      |> File.read!()
      |> Jason.decode!()

  defp ev(n, req, type, at, device, p) do
    %{
      "event_id" => "01JCB00000000000000E" <> String.pad_leading("#{n}", 6, "0"),
      "request_id" => "01JCB000000000000000RQ00" <> req,
      "type" => type,
      "device_id" => device,
      "occurred_at" => at,
      "rules_version" => if(type == "decided", do: "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"),
      "model_version" => if(type in ~w(extracted decided), do: "stub:test"),
      "payload" => p
    }
  end

  test "per-device counts and laptop timestamps", %{conn: conn} do
    abstain =
      payload("decided")
      |> put_in(["engine_output", "outcome"], "abstain")
      |> put_in(["engine_output", "abstain_reason"], "low_confidence_request")
      |> put_in(["engine_output", "follow_up_question"], "Where are you?")
      |> put_in(["engine_output", "priority_tier"], nil)
      |> put_in(["engine_output", "duplicate_of"], nil)

    decided = put_in(payload("decided"), ["engine_output", "duplicate_of"], nil)
    decided = put_in(decided, ["engine_output", "outcome"], "prioritized")

    {:ok, %{rejected: []}} =
      Events.append_batch([
        ev(1, "01", "received", "2026-11-07T09:00:00Z", "field-03", payload("received")),
        ev(2, "01", "decided", "2026-11-07T09:01:00Z", "field-03", decided),
        ev(3, "02", "received", "2026-11-07T09:02:00Z", "field-03", payload("received")),
        ev(4, "02", "decided", "2026-11-07T09:03:00Z", "field-03", abstain),
        ev(5, "02", "escalated", "2026-11-07T09:04:00Z", "field-03", payload("escalated")),
        ev(6, "03", "received", "2026-11-07T08:00:00Z", "field-04", payload("received"))
      ])

    {:ok, view, _} = live(conn, ~p"/activity")

    assert has_element?(view, "#activity-field-03 [data-role=requests]", "2")
    assert has_element?(view, "#activity-field-03 [data-role=decisions]", "2")
    assert has_element?(view, "#activity-field-03 [data-role=abstains]", "1")
    assert has_element?(view, "#activity-field-03 [data-role=escalations]", "1")
    assert has_element?(view, "#activity-field-04 [data-role=requests]", "1")
    assert has_element?(view, "#activity-field-03 [data-role=at]", "2026-11-07 09:04:00 UTC")
  end

  test "empty state", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/activity")
    assert has_element?(view, "#activity-empty")
  end
end
