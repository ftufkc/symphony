defmodule SymphonyElixir.Plane.Client do
  @moduledoc "Plane Community Edition v1 work-items API. Credentials remain host-side."

  alias SymphonyElixir.{Config, Tracker.Issue}

  @spec settings(map()) :: {:ok, map()} | {:error, atom()}
  def settings(tracker) do
    provider = Map.get(tracker, :provider, %{})
    api_url = resolve(provider["api_url"], "PLANE_BASE_URL")
    token = resolve(provider["api_key"], "PLANE_API_TOKEN")
    workspace = resolve(provider["workspace_slug"], "PLANE_WORKSPACE_SLUG")
    uri = URI.parse(api_url || "")

    cond do
      not valid_api_uri?(uri) -> {:error, :invalid_plane_ce_api_url}
      is_nil(token) -> {:error, :missing_plane_api_key}
      not valid_workspace?(workspace) -> {:error, :invalid_plane_workspace_slug}
      true -> settings_result(api_url, token, workspace, provider)
    end
  end

  defp settings_result(url, token, workspace, provider) do
    {:ok, %{api_url: String.trim_trailing(url, "/"), api_key: token, workspace_slug: workspace, provider: provider}}
  end

  defp valid_api_uri?(uri) do
    uri.scheme in ["http", "https"] and is_binary(uri.host) and uri.userinfo == nil and
      uri.query == nil and uri.fragment == nil and String.trim_trailing(uri.path || "", "/") == "/api/v1"
  end

  defp valid_workspace?(value) when is_binary(value), do: Regex.match?(~r/^[A-Za-z0-9_-]+$/, value)
  defp valid_workspace?(_value), do: false

  @spec secret_environment_names(map()) :: [String.t()]
  def secret_environment_names(tracker) do
    references =
      tracker
      |> Map.get(:provider, %{})
      |> Map.take(["api_key", "webhook_secret"])
      |> Map.values()
      |> Enum.flat_map(fn
        "$" <> name -> [name]
        _ -> []
      end)

    Enum.uniq(["PLANE_API_TOKEN", "PLANE_API_KEY", "PLANE_WEBHOOK_SECRET", "WEBHOOK_SECRET" | references])
  end

  @spec resolve(term(), String.t()) :: String.t() | nil
  def resolve(value, fallback) do
    result =
      case value do
        "$" <> name -> System.get_env(name)
        nil -> System.get_env(fallback)
        literal -> literal
      end

    if is_binary(result) and String.trim(result) != "", do: String.trim(result), else: nil
  end

  @spec request(String.t(), String.t(), map(), term(), keyword()) :: {:ok, map()} | {:error, term()}
  def request(method, path, query \\ %{}, body \\ nil, opts \\ []) do
    tracker = Keyword.get_lazy(opts, :tracker_settings, fn -> Config.settings!().tracker end)

    with {:ok, config} <- settings(tracker),
         true <- method in ["GET", "POST", "PATCH", "PUT", "DELETE"],
         true <- safe_path?(path) do
      transport = Keyword.get(opts, :request_fun, &perform_request/5)
      retry_request(transport, method, "/" <> String.trim_leading(path, "/"), query, body, config, opts, 0)
    else
      false -> {:error, :invalid_plane_request}
      error -> error
    end
  end

  @spec paginate(String.t(), map(), keyword()) :: {:ok, [map()]} | {:error, term()}
  def paginate(path, query \\ %{}, opts \\ []) do
    paginate_pages(path, Map.put_new(query, "per_page", 100), opts, [], [])
  end

  @spec projects(keyword()) :: {:ok, [map()]} | {:error, term()}
  def projects(opts \\ []) do
    with {:ok, config} <- config(opts),
         {:ok, projects} <- paginate("/workspaces/#{config.workspace_slug}/projects/", %{}, opts) do
      selected = config.provider["project_ids"]
      {:ok, Enum.filter(projects, &(is_nil(selected) or &1["id"] in selected))}
    end
  end

  @spec project_path(String.t(), String.t(), keyword()) :: String.t()
  def project_path(project_id, suffix, opts \\ []) do
    {:ok, config} = config(opts)
    "/workspaces/#{config.workspace_slug}/projects/#{URI.encode_www_form(project_id)}/" <> suffix
  end

  @spec states(String.t(), keyword()) :: {:ok, [map()]} | {:error, term()}
  def states(project_id, opts \\ []), do: paginate(project_path(project_id, "states/", opts), %{}, opts)

  @spec comments(String.t(), String.t(), keyword()) :: {:ok, [map()]} | {:error, term()}
  def comments(project_id, work_item_id, opts \\ []) do
    paginate(project_path(project_id, "work-items/#{URI.encode_www_form(work_item_id)}/comments/", opts), %{}, opts)
  end

  @spec get_item(String.t(), String.t(), keyword()) :: {:ok, map()} | {:error, term()}
  def get_item(project_id, work_item_id, opts \\ []) do
    json_request("GET", project_path(project_id, "work-items/#{URI.encode_www_form(work_item_id)}/", opts), %{"expand" => "state,labels,assignees"}, nil, opts)
  end

  @spec set_state(String.t(), String.t(), String.t(), keyword()) :: {:ok, map()} | {:error, term()}
  def set_state(project_id, work_item_id, name, opts \\ []) do
    with {:ok, available} <- states(project_id, opts),
         [state] <- Enum.filter(available, &(normalize(&1["name"]) == normalize(name))) do
      json_request("PATCH", project_path(project_id, "work-items/#{URI.encode_www_form(work_item_id)}/", opts), %{}, %{"state" => state["id"]}, opts)
    else
      [] -> {:error, :plane_state_not_found}
      states when is_list(states) -> {:error, :plane_state_ambiguous}
      error -> error
    end
  end

  @spec add_comment(String.t(), String.t(), String.t(), keyword()) :: {:ok, map()} | {:error, term()}
  def add_comment(project_id, work_item_id, text, opts \\ []) do
    body = %{"comment_html" => "<p>" <> (text |> html_escape() |> String.replace("\n", "<br>")) <> "</p>"}
    json_request("POST", project_path(project_id, "work-items/#{URI.encode_www_form(work_item_id)}/comments/", opts), %{}, body, opts)
  end

  @spec fetch_issues_by_states([String.t()], keyword()) :: {:ok, [Issue.t()]} | {:error, term()}
  def fetch_issues_by_states([], _opts), do: {:ok, []}

  def fetch_issues_by_states(names, opts) do
    with {:ok, projects} <- projects(opts) do
      collect(projects, fn project -> project_issues(project, names, opts) end)
    end
  end

  defp project_issues(project, names, opts) do
    with {:ok, states} <- states(project["id"], opts),
         {:ok, rows} <- paginate(project_path(project["id"], "work-items/", opts), %{"expand" => "state,labels,assignees"}, opts) do
      requested = Enum.map(names, &normalize/1)
      issues = rows |> Enum.map(&normalize_issue(&1, project, states, opts)) |> Enum.reject(&is_nil/1)
      {:ok, Enum.filter(issues, &(normalize(&1.state) in requested))}
    end
  end

  @spec fetch_issues_by_ids([String.t()], keyword()) :: {:ok, [Issue.t()]} | {:error, term()}
  def fetch_issues_by_ids([], _opts), do: {:ok, []}

  def fetch_issues_by_ids(ids, opts) do
    with {:ok, projects} <- projects(opts) do
      collect(Enum.uniq(ids), &fetch_id(&1, projects, opts))
    end
  end

  defp fetch_id(id, projects, opts) do
    case String.split(id, "/", parts: 2) do
      [pid, wid] -> fetch_project_item(Enum.find(projects, &(&1["id"] == pid)), wid, opts)
      _ -> {:error, :invalid_plane_issue_id}
    end
  end

  defp fetch_project_item(nil, _wid, _opts), do: {:ok, []}

  defp fetch_project_item(project, wid, opts) do
    with {:ok, row} <- get_item(project["id"], wid, opts),
         {:ok, states} <- states(project["id"], opts) do
      case normalize_issue(row, project, states, opts) do
        nil -> {:error, :plane_unknown_payload}
        issue -> {:ok, [issue]}
      end
    else
      {:error, {:plane_api_status, 404}} -> {:ok, []}
      error -> error
    end
  end

  defp collect(values, fun) do
    Enum.reduce_while(values, {:ok, []}, fn value, {:ok, result} ->
      case fun.(value) do
        {:ok, items} -> {:cont, {:ok, result ++ items}}
        error -> {:halt, error}
      end
    end)
  end

  @spec normalize_issue(map(), map(), [map()], keyword()) :: Issue.t() | nil
  def normalize_issue(row, project, states, opts \\ []) do
    state = if is_map(row["state"]), do: row["state"], else: Enum.find(states, &(&1["id"] == row["state"]))
    pid = project["id"]
    wid = row["id"]

    if is_binary(wid) and is_binary(pid) and is_binary(row["name"]) and is_map(state) do
      {:ok, config} = config(opts)
      identifier = "#{project["identifier"] || pid}-#{row["sequence_id"] || wid}"
      description = row["description_html"] || ""

      %Issue{
        id: pid <> "/" <> wid,
        identifier: identifier,
        native_ref: %{
          "project_id" => pid,
          "work_item_id" => wid,
          "workspace_slug" => config.workspace_slug,
          "actual_state" => state["name"],
          "trigger_reason" => "state",
          "description_html" => description
        },
        title: row["name"],
        description: text(description),
        priority: %{"urgent" => 1, "high" => 2, "medium" => 3, "low" => 4}[row["priority"]],
        state: state["name"],
        url: row["url"],
        assignee_id: first_assignee(row),
        labels: label_names(row),
        dispatchable: true,
        created_at: datetime(row["created_at"]),
        updated_at: datetime(row["updated_at"])
      }
    end
  end

  @spec text(String.t()) :: String.t()
  def text(html) do
    html
    |> String.replace(~r/<[^>]+>/, " ")
    |> String.replace("&nbsp;", " ")
    |> String.replace("&lt;", "<")
    |> String.replace("&gt;", ">")
    |> String.replace("&amp;", "&")
    |> String.replace(~r/\s+/, " ")
    |> String.trim()
  end

  @spec normalize(term()) :: String.t()
  def normalize(value) when is_binary(value), do: value |> String.trim() |> String.downcase()
  def normalize(_value), do: ""

  defp config(opts), do: settings(Keyword.get_lazy(opts, :tracker_settings, fn -> Config.settings!().tracker end))
  defp first_assignee(row), do: (row["assignees"] || []) |> List.first() |> entity_id()
  defp label_names(row), do: Enum.map(row["labels"] || [], &label_name/1)
  defp label_name(%{"name" => name}), do: normalize(name)
  defp label_name(value), do: normalize(value)
  defp entity_id(%{"id" => id}), do: id
  defp entity_id(id) when is_binary(id), do: id
  defp entity_id(_value), do: nil

  defp datetime(value) when is_binary(value) do
    case DateTime.from_iso8601(value) do
      {:ok, date, _offset} -> date
      _ -> nil
    end
  end

  defp datetime(_value), do: nil
  defp html_escape(text), do: text |> String.replace("&", "&amp;") |> String.replace("<", "&lt;") |> String.replace(">", "&gt;") |> String.replace("\"", "&quot;")

  defp safe_path?(path) when is_binary(path) do
    not String.contains?(path, [":", "?", "#", "\\", "\n", "\r", <<0>>]) and
      not String.starts_with?(path, "//") and ".." not in String.split(URI.decode(path), "/")
  end

  defp safe_path?(_path), do: false

  defp json_request(method, path, query, body, opts) do
    case request(method, path, query, body, opts) do
      {:ok, %{status: status, body: result}} when status in 200..299 -> {:ok, result}
      {:ok, %{status: status}} -> {:error, {:plane_api_status, status}}
      error -> error
    end
  end

  @spec paginate_pages(String.t(), map(), keyword(), [map()], [String.t()]) :: {:ok, [map()]} | {:error, term()}
  defp paginate_pages(path, query, opts, result, seen) do
    with {:ok, payload} <- json_request("GET", path, query, nil, opts) do
      next_page(payload, path, query, opts, result, seen)
    end
  end

  @spec next_page(term(), String.t(), map(), keyword(), [map()], [String.t()]) :: {:ok, [map()]} | {:error, term()}
  defp next_page(rows, _path, _query, _opts, result, _seen) when is_list(rows), do: {:ok, result ++ rows}

  defp next_page(%{"results" => rows} = payload, path, query, opts, result, seen) when is_list(rows) do
    cursor = payload["next_cursor"]

    cond do
      payload["next_page_results"] != true or not is_binary(cursor) -> {:ok, result ++ rows}
      cursor in seen -> {:error, :plane_cursor_repeated}
      true -> paginate_pages(path, Map.put(query, "cursor", cursor), opts, result ++ rows, [cursor | seen])
    end
  end

  defp next_page(_payload, _path, _query, _opts, _result, _seen), do: {:error, :plane_unknown_payload}

  defp retry_request(transport, method, path, query, body, config, opts, attempt) do
    case transport.(method, path, query, body, config) do
      {:ok, %{status: 429} = response} when attempt < 3 ->
        header = response |> Map.get(:headers, %{}) |> Map.get("retry-after", ["0.5"])
        value = if is_list(header), do: List.first(header), else: header

        delay =
          case Float.parse(to_string(value)) do
            {seconds, ""} when seconds >= 0 -> min(round(seconds * 1000), 60_000)
            _ -> trunc(500 * :math.pow(2, attempt))
          end

        Keyword.get(opts, :sleep, &Process.sleep/1).(delay)
        retry_request(transport, method, path, query, body, config, opts, attempt + 1)

      result ->
        result
    end
  end

  defp perform_request(method, path, query, body, config) do
    options = [
      method: String.downcase(method) |> String.to_existing_atom(),
      url: config.api_url <> path,
      headers: [{"x-api-key", config.api_key}],
      params: query,
      retry: false,
      redirect: false,
      receive_timeout: 30_000
    ]

    options = if is_nil(body), do: options, else: Keyword.put(options, :json, body)

    case Req.request(options) do
      {:ok, response} -> {:ok, %{status: response.status, body: response.body, headers: response.headers}}
      {:error, _reason} -> {:error, :plane_transport_error}
    end
  end
end
