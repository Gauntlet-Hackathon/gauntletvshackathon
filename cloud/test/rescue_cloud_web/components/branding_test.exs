defmodule RescueCloudWeb.BrandingTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest

  @pages ["/", "/fleet", "/records", "/activity", "/corrections", "/evals"]

  test "every page renders with the CommunityRescue header, icon and favicon", %{conn: conn} do
    for path <- @pages do
      html = html_response(get(conn, path), 200)
      assert html =~ ~s(id="app-name"), path
      assert html =~ "CommunityRescue", path
      assert html =~ "/images/communityrescue-icon.svg", path
      assert html =~ ~s(rel="icon"), path
    end
  end

  test "pages load no external assets", %{conn: conn} do
    for path <- @pages do
      html = html_response(get(conn, path), 200)
      refute html =~ ~r/(src|href)="(https?:)?\/\//, path
    end
  end

  test "the connected header shows the app name", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")
    assert has_element?(view, "header #app-name", "CommunityRescue")
  end

  test "fonts and icons are served locally", %{conn: conn} do
    for path <- [
          "/fonts/atkinson-400.woff2",
          "/fonts/atkinson-700.woff2",
          "/images/communityrescue-icon.svg",
          "/images/favicon-32.png",
          "/favicon.ico"
        ] do
      assert get(conn, path).status == 200, path
    end
  end

  test "app.css uses the local font and saturated semantic colours" do
    css = File.read!(Path.expand("../../../assets/css/app.css", __DIR__))
    assert css =~ ~s{url("/fonts/atkinson-400.woff2")}
    refute css =~ ~r/url\("?https?:/
    refute css =~ ~r/--color-(success|warning|info|error|primary):\s*#(E7F3E8|FFF4E5)/i
  end
end
