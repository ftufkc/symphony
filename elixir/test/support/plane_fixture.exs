defmodule SymphonyElixir.PlaneFixture do
  @behaviour Plug
  import Plug.Conn
  def init(opts), do: opts

  def call(conn, opts) do
    agent = Keyword.fetch!(opts, :state)
    {:ok, body, conn} = read_body(conn)
    data = if body == "", do: nil, else: Jason.decode!(body)
    conn = fetch_query_params(conn)

    {status, result} =
      Agent.get_and_update(agent, fn state ->
        request = {conn.method, conn.request_path, conn.query_params, data, get_req_header(conn, "x-api-key")}
        state = %{state | requests: state.requests ++ [request]}

        case state.errors[{conn.method, conn.request_path}] do
          nil -> handle(conn.method, String.split(conn.request_path, "/", trim: true), conn.query_params, data, state)
          code -> {{code, %{}}, state}
        end
      end)

    conn |> put_resp_content_type("application/json") |> send_resp(status, Jason.encode!(result))
  end

  def initial do
    projects = [%{"id" => "p1", "identifier" => "ONE"}, %{"id" => "p2", "identifier" => "TWO"}]

    states =
      for pid <- ["p1", "p2"], into: %{} do
        {pid, Enum.map(["AI Todo", "AI Doing", "AI Error", "Human Review", "AI Done"], &%{"id" => pid <> &1, "name" => &1})}
      end

    items =
      for pid <- ["p1", "p2"], into: %{} do
        {{pid, "w1"},
         %{
           "id" => "w1",
           "name" => "Fix bug",
           "sequence_id" => 1,
           "state" => pid <> "AI Todo",
           "description_html" => "<p>Fix &amp; test</p>",
           "assignees" => nil,
           "labels" => [%{"name" => "Code"}],
           "priority" => "high"
         }}
      end

    %{projects: projects, states: states, items: items, comments: %{}, requests: [], errors: %{}}
  end

  defp page(rows), do: %{"results" => rows, "next_page_results" => false, "next_cursor" => ""}
  defp handle("GET", ["api", "v1", "users", "me"], _q, _body, state), do: {{200, %{"id" => "bot"}}, state}
  defp handle("GET", ["api", "v1", "workspaces", "test", "projects"], _q, _body, state), do: {{200, page(state.projects)}, state}
  defp handle("GET", ["api", "v1", "workspaces", "test", "projects", pid, "states"], _q, _body, state), do: {{200, page(state.states[pid] || [])}, state}

  defp handle("POST", ["api", "v1", "workspaces", "test", "projects", pid, "states"], _q, body, state) do
    row = Map.put(body, "id", pid <> body["name"])
    {{201, row}, %{state | states: Map.update!(state.states, pid, &(&1 ++ [row]))}}
  end

  defp handle("PATCH", ["api", "v1", "workspaces", "test", "projects", pid, "states", sid], _q, body, state) do
    states = Enum.map(state.states[pid], fn row -> if row["id"] == sid, do: Map.merge(row, body), else: row end)
    {{200, %{}}, %{state | states: Map.put(state.states, pid, states)}}
  end

  defp handle("GET", ["api", "v1", "workspaces", "test", "projects", pid, "work-items"], _q, _body, state) do
    rows = for {{p, _w}, row} <- state.items, p == pid, do: row
    {{200, page(rows)}, state}
  end

  defp handle("GET", ["api", "v1", "workspaces", "test", "projects", pid, "work-items", wid], _q, _body, state) do
    case state.items[{pid, wid}] do
      nil -> {{404, %{}}, state}
      row -> {{200, row}, state}
    end
  end

  defp handle("PATCH", ["api", "v1", "workspaces", "test", "projects", pid, "work-items", wid], _q, body, state) do
    row = Map.merge(state.items[{pid, wid}], body)
    {{200, row}, %{state | items: Map.put(state.items, {pid, wid}, row)}}
  end

  defp handle("GET", ["api", "v1", "workspaces", "test", "projects", pid, "work-items", wid, "comments"], _q, _body, state), do: {{200, page(state.comments[{pid, wid}] || [])}, state}

  defp handle("POST", ["api", "v1", "workspaces", "test", "projects", pid, "work-items", wid, "comments"], _q, body, state) do
    comments = state.comments[{pid, wid}] || []
    row = Map.merge(body, %{"id" => "c#{length(comments)}", "created_by" => "bot"})
    {{201, row}, %{state | comments: Map.put(state.comments, {pid, wid}, comments ++ [row])}}
  end

  defp handle(_method, _path, _q, _body, state), do: {{200, %{}}, state}
end
