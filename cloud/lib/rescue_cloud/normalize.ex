defmodule RescueCloud.Normalize do
  @moduledoc """
  Elixir port of the shared logic in `contracts/logic.md` (Python: `shared/rescue_shared/logic.py`):
  `location/1`, `contact/1`, `duplicate_candidates/2` and `duplicate_groups/2`.
  Both implementations must pass `contracts/vectors/*.json`.
  """

  @coords ~r/^\s*(-?\d{1,3}\.\d+)\s*[,;\s]\s*(-?\d{1,3}\.\d+)\s*$/u
  @non_alnum ~r/[^a-z0-9]+/

  @token_map %{
    "st" => "street",
    "ave" => "avenue",
    "av" => "avenue",
    "blvd" => "boulevard",
    "rd" => "road",
    "dr" => "drive",
    "ln" => "lane",
    "hwy" => "highway",
    "fwy" => "freeway",
    "pkwy" => "parkway",
    "ct" => "court",
    "cir" => "circle",
    "pl" => "place",
    "trl" => "trail",
    "ter" => "terrace",
    "n" => "north",
    "s" => "south",
    "e" => "east",
    "w" => "west",
    "ne" => "northeast",
    "nw" => "northwest",
    "se" => "southeast",
    "sw" => "southwest"
  }

  @stopwords MapSet.new(~w(the a an at near by behind in on off next to across from of around
                           outside inside front beside between just past over under close))

  @doc ~S"""
  Canonical key for a location text; equal keys mean "same place", `""` means no usable key.

      iex> RescueCloud.Normalize.location("behind the Kroger on Westheimer")
      "kroger westheimer"
  """
  @spec location(String.t() | nil) :: String.t()
  def location(nil), do: ""

  def location(text) when is_binary(text) do
    case Regex.run(@coords, text, capture: :all_but_first) do
      [lat, lon] -> "geo:#{four_dp(lat)},#{four_dp(lon)}"
      nil -> text |> tokens() |> Enum.sort() |> Enum.join(" ")
    end
  end

  @doc "Digits only; an 11-digit number starting with 1 loses the 1. `\"\"` means no contact."
  @spec contact(String.t() | nil) :: String.t()
  def contact(nil), do: ""

  def contact(text) when is_binary(text) do
    case String.replace(text, ~r/[^0-9]/, "") do
      "1" <> rest = digits when byte_size(digits) == 11 -> rest
      digits -> digits
    end
  end

  @doc """
  For every request (string-keyed maps with `request_id`, `received_at`, `location_text`,
  `people_count`, `contact`, `priority_tier`), the earlier requests that are direct duplicate
  candidates of it, oldest first, in the `engine_input.context.duplicate_candidates` shape.
  """
  @spec duplicate_candidates([map()], number()) :: %{String.t() => [map()]}
  def duplicate_candidates(requests, window_hours) do
    keyed =
      Enum.map(requests, fn r ->
        {r, location(r["location_text"]), contact(r["contact"]), seconds(r["received_at"])}
      end)

    Map.new(keyed, fn {a, a_loc, a_contact, a_secs} ->
      found =
        for {b, b_loc, b_contact, b_secs} <- keyed,
            a_loc != "",
            a_loc == b_loc,
            order(b) < order(a),
            a_secs - b_secs <= window_hours * 3600,
            {contact_match, same_count} = match_kind(a, b, a_contact, b_contact),
            contact_match or same_count do
          {b, contact_match}
        end

      candidates =
        found
        |> Enum.sort_by(fn {b, _} -> order(b) end)
        |> Enum.map(fn {b, contact_match} ->
          %{
            "request_id" => b["request_id"],
            "received_at" => b["received_at"],
            "people_count" => b["people_count"],
            "contact_match" => contact_match,
            "priority_tier" => b["priority_tier"]
          }
        end)

      {a["request_id"], candidates}
    end)
  end

  @doc """
  Connected components (2+ members) of the candidate relation, as request ids. Members are sorted
  oldest first by `(received_at, request_id)`; groups are sorted by their oldest member.
  """
  @spec duplicate_groups([map()], number()) :: [[String.t()]]
  def duplicate_groups(requests, window_hours) do
    by_id = Map.new(requests, &{&1["request_id"], &1})

    edges =
      for {rid, cands} <- duplicate_candidates(requests, window_hours),
          c <- cands,
          do: {rid, c["request_id"]}

    adjacency =
      Enum.reduce(edges, %{}, fn {a, b}, acc ->
        acc |> Map.update(a, [b], &[b | &1]) |> Map.update(b, [a], &[a | &1])
      end)

    {groups, _seen} =
      Enum.reduce(Map.keys(adjacency), {[], MapSet.new()}, fn rid, {groups, seen} ->
        if MapSet.member?(seen, rid) do
          {groups, seen}
        else
          component = component(adjacency, [rid], MapSet.new([rid]))
          {[MapSet.to_list(component) | groups], MapSet.union(seen, component)}
        end
      end)

    groups
    |> Enum.map(fn g -> Enum.sort_by(g, &order(by_id[&1])) end)
    |> Enum.sort_by(fn [oldest | _] -> order(by_id[oldest]) end)
  end

  # Returned as a tuple so the binding in the comprehension above is always truthy.
  defp match_kind(a, b, a_contact, b_contact) do
    {a_contact != "" and a_contact == b_contact,
     a["people_count"] != nil and a["people_count"] == b["people_count"]}
  end

  defp component(_adjacency, [], seen), do: seen

  defp component(adjacency, [x | rest], seen) do
    new = adjacency |> Map.get(x, []) |> Enum.reject(&MapSet.member?(seen, &1))
    component(adjacency, new ++ rest, Enum.into(new, seen))
  end

  # received_at strings are the strict contract format, so string order is time order.
  defp order(r), do: {r["received_at"], r["request_id"]}

  defp seconds(ts) do
    {:ok, dt, 0} = DateTime.from_iso8601(ts)
    DateTime.to_unix(dt)
  end

  defp four_dp(number) do
    {f, ""} = Float.parse(number)
    :erlang.float_to_binary(f, decimals: 4)
  end

  defp tokens(text) do
    folded =
      text
      |> :unicode.characters_to_nfkd_binary()
      |> String.replace(~r/\p{Mn}/u, "")
      |> String.downcase()
      |> String.replace("ß", "ss")
      |> String.replace(["'", "’"], "")
      |> String.replace("&", " and ")

    @non_alnum
    |> Regex.replace(folded, " ")
    |> String.split(" ", trim: true)
    |> Enum.map(&Map.get(@token_map, &1, &1))
    |> Enum.reject(&MapSet.member?(@stopwords, &1))
  end
end
