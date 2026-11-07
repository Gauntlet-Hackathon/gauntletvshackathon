defmodule RescueCloud.Normalize do
  @moduledoc """
  Elixir port of the shared logic in `contracts/logic.md` (Python: `shared/rescue_shared/logic.py`):
  `location/1`, `contact/1`, `duplicate_candidates/2` and `duplicate_groups/2`.
  Both implementations must pass `contracts/vectors/*.json`.

  Unicode digits, whitespace and combining marks are classified with Python's tables
  (`RescueCloud.Normalize.Unicode`), so the two implementations agree on every input, not only
  on ASCII: `"٣٠.٠٤٤٤, ٣١.٢٣٥٧"` is `"geo:30.0444,31.2357"` and `"٧١٣-٥٥٥-٠١٠٠"` keeps its digits,
  as in Python. These functions never raise on a binary.
  """

  import Bitwise

  alias RescueCloud.Normalize.Unicode

  @non_alnum ~r/[^a-z0-9]+/

  # Smallest people count that matches on its own, without a shared contact (contracts/logic.md).
  @count_match_min 3

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
    case coords(text) do
      {lat, lon} -> "geo:#{four_dp(lat)},#{four_dp(lon)}"
      nil -> text |> tokens() |> Enum.sort() |> Enum.join(" ")
    end
  end

  @doc """
  Digits only (every character Python's `str.isdigit` accepts, kept as written); an 11-digit
  number starting with an ASCII 1 loses the 1. `""` means no contact.
  """
  @spec contact(String.t() | nil) :: String.t()
  def contact(nil), do: ""

  def contact(text) when is_binary(text) do
    digits = for <<cp::utf8 <- text>>, Unicode.digit?(cp), do: cp

    case digits do
      [?1 | rest] when length(rest) == 10 -> List.to_string(rest)
      _ -> List.to_string(digits)
    end
  end

  @doc """
  For every request (string-keyed maps with `request_id`, `received_at`, `location_text`,
  `people_count`, `contact`, `priority_tier`), the earlier requests that are direct duplicate
  candidates of it, oldest first, in the `engine_input.context.duplicate_candidates` shape.
  A count-only match needs equal known counts of at least #{@count_match_min}.
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
    count = a["people_count"]

    {a_contact != "" and a_contact == b_contact,
     is_integer(count) and count >= @count_match_min and count == b["people_count"]}
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

  # Python: _COORDS_RE = r"^\s*(-?\d{1,3}\.\d+)\s*[,;\s]\s*(-?\d{1,3}\.\d+)\s*$" with `re.match`,
  # where \d is any Unicode decimal digit and \s is `str.isspace`. Hand-written so that both
  # classes are Python's (`Unicode`), not this PCRE's. The numbers come back as ASCII strings.
  defp coords(text) do
    with {:ok, cps} <- codepoints(text),
         {:ok, lat, rest} <- number(skip_space(cps)),
         {:ok, rest} <- separator(rest),
         {:ok, lon, rest} <- number(rest),
         [] <- skip_space(rest) do
      {lat, lon}
    else
      _ -> nil
    end
  end

  defp codepoints(text) do
    case :unicode.characters_to_list(text) do
      list when is_list(list) -> {:ok, list}
      _ -> :error
    end
  end

  defp skip_space([cp | rest] = cps), do: if(Unicode.space?(cp), do: skip_space(rest), else: cps)
  defp skip_space([]), do: []

  # [,;\s] between two runs of \s*: whitespace only, or one comma/semicolon in it.
  defp separator(cps) do
    rest = skip_space(cps)

    case rest do
      [sep | after_sep] when sep in [?,, ?;] -> {:ok, skip_space(after_sep)}
      _ when rest != cps -> {:ok, rest}
      _ -> :error
    end
  end

  defp number([?- | rest]) do
    with {:ok, n, rest} <- unsigned(rest), do: {:ok, "-" <> n, rest}
  end

  defp number(cps), do: unsigned(cps)

  defp unsigned(cps) do
    {int, rest} = take_digits(cps, [])

    with true <- length(int) in 1..3,
         [?. | rest] <- rest,
         {[_ | _] = frac, rest} <- take_digits(rest, []) do
      {:ok, "#{int}.#{frac}", rest}
    else
      _ -> :error
    end
  end

  defp take_digits([cp | rest] = cps, acc) do
    case Unicode.decimal_value(cp) do
      nil -> {Enum.reverse(acc), cps}
      d -> take_digits(rest, [?0 + d | acc])
    end
  end

  defp take_digits([], acc), do: {Enum.reverse(acc), []}

  # Python: f"{float(number):.4f}". The number is ASCII `-?[0-9]{1,3}.[0-9]+`, which Float.parse
  # always reads in full. Python rounds the exact value of the double half to even ("840.90625"
  # gives 840.9062), where :erlang.float_to_binary/2 rounds half away from zero, so round here.
  defp four_dp(number) do
    {f, ""} = Float.parse(number)
    <<sign::1, exp::11, frac::52>> = <<f::float>>
    # f = mantissa * 2^-shift exactly (normal and subnormal doubles; no inf/nan from 3 digits).
    {mantissa, shift} = if exp == 0, do: {frac, 1074}, else: {frac + (1 <<< 52), 1075 - exp}
    scaled = mantissa * 10_000

    {q, r} =
      if shift > 0,
        do: {scaled >>> shift, scaled &&& (1 <<< shift) - 1},
        else: {scaled <<< -shift, 0}

    half = if shift > 0, do: 1 <<< (shift - 1), else: 1
    q = if r > half or (r == half and rem(q, 2) == 1), do: q + 1, else: q
    sign = if sign == 1, do: "-", else: ""

    "#{sign}#{div(q, 10_000)}.#{q |> rem(10_000) |> Integer.to_string() |> String.pad_leading(4, "0")}"
  end

  defp tokens(text) do
    folded =
      text
      |> :unicode.characters_to_nfkd_binary()
      |> drop_combining()
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

  # Python drops `unicodedata.combining(c) != 0` (canonical combining class), not category Mn.
  defp drop_combining(text) when is_binary(text) do
    for <<cp::utf8 <- text>>, not Unicode.combining?(cp), into: "", do: <<cp::utf8>>
  end

  defp drop_combining(_invalid), do: ""
end
