defmodule RescueCloudWeb.ErrorJSONTest do
  use RescueCloudWeb.ConnCase, async: true

  test "renders 404" do
    assert RescueCloudWeb.ErrorJSON.render("404.json", %{}) == %{errors: %{detail: "Not Found"}}
  end

  test "renders 500" do
    assert RescueCloudWeb.ErrorJSON.render("500.json", %{}) ==
             %{errors: %{detail: "Internal Server Error"}}
  end

  test "sync routes use the sync error shape" do
    conn = Phoenix.ConnTest.build_conn(:post, "/sync/events")
    assert RescueCloudWeb.ErrorJSON.render("400.json", %{conn: conn}) == %{error: "bad_request"}
    assert RescueCloudWeb.ErrorJSON.render("500.json", %{conn: conn}) == %{error: "server_error"}
  end
end
