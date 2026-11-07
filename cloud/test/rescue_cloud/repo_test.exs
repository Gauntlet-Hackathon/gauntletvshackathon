defmodule RescueCloud.RepoTest do
  use RescueCloud.DataCase, async: true

  test "the Ecto SQL sandbox gives each test a working database connection" do
    assert %{rows: [[1]]} = Repo.query!("select 1")
  end
end
