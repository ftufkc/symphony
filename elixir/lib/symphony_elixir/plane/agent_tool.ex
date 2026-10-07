defmodule SymphonyElixir.Plane.AgentTool do
  @moduledoc "Host-authenticated Plane convenience tools and relative REST escape hatch."
  alias SymphonyElixir.Plane.{Client, Runtime}

  @spec tool_specs() :: [map()]
  def tool_specs do
    identity = %{"project_id" => %{"type" => "string"}, "work_item_id" => %{"type" => "string"}}

    [
      tool("get_work_item", "Read a Plane work item.", identity, []),
      tool("list_comments", "Read all work item comments.", identity, []),
      tool("add_comment", "Post escaped plain text (including Markdown source) as comment HTML.", Map.put(identity, "markdown", %{"type" => "string"}), ["markdown"]),
      tool("set_state", "Resolve a state name per project. Mention runs preserve state.", Map.put(identity, "state_name", %{"type" => "string"}), ["state_name"]),
      tool("list_states", "List project states.", Map.take(identity, ["project_id"]), []),
      tool("search_work_items", "Search the configured workspace.", %{"query" => %{"type" => "string"}}, ["query"]),
      tool("get_me", "Read the authenticated bot identity.", %{}, []),
      tool(
        "plane_request",
        "Call any Plane CE v1 relative endpoint with host authentication and a JSON body. Attachment upload credentials are accessible here.",
        %{
          "method" => %{"type" => "string", "enum" => ["GET", "POST", "PATCH", "PUT", "DELETE"]},
          "path" => %{"type" => "string"},
          "query" => %{"type" => ["object", "null"], "additionalProperties" => true},
          "body" => %{}
        },
        ["method", "path"]
      )
    ]
  end

  @spec execute(String.t(), term(), keyword()) :: map()
  def execute(name, args, opts) when is_map(args) do
    issue = Keyword.get(opts, :issue)
    ref = if issue, do: issue.native_ref || %{}, else: %{}
    pid = args["project_id"] || ref["project_id"]
    wid = args["work_item_id"] || ref["work_item_id"]
    client_opts = Keyword.take(opts, [:tracker_settings, :request_fun, :sleep])
    result = dispatch(name, args, pid, wid, issue, client_opts)
    maybe_record_comment(name, args, pid, wid, issue, result, client_opts)
    response(result)
  end

  def execute(_name, _args, _opts), do: response({:error, :invalid_arguments})

  defp tool(name, description, properties, required) do
    %{"name" => name, "description" => description, "inputSchema" => %{"type" => "object", "additionalProperties" => false, "properties" => properties, "required" => required}}
  end

  defp dispatch("get_me", _args, _pid, _wid, _issue, opts), do: Client.request("GET", "/users/me/", %{}, nil, opts)

  defp dispatch("search_work_items", %{"query" => query}, _pid, _wid, _issue, opts) when is_binary(query) do
    {:ok, cfg} = Client.settings(Keyword.fetch!(opts, :tracker_settings))
    Client.request("GET", "/workspaces/#{cfg.workspace_slug}/work-items/search/", %{"search" => query}, nil, opts)
  end

  defp dispatch("plane_request", %{"method" => method, "path" => path} = args, _pid, _wid, issue, opts) do
    query = args["query"] || %{}
    body = args["body"]

    cond do
      not is_map(query) -> {:error, :invalid_query}
      changes_mention_state?(issue, method, body) -> {:error, :mention_preserves_state}
      true -> Client.request(method, path, query, body, opts)
    end
  end

  defp dispatch("list_states", _args, pid, _wid, _issue, opts) when is_binary(pid), do: Client.states(pid, opts)

  defp dispatch(name, args, pid, wid, issue, opts) when is_binary(pid) and is_binary(wid) do
    case {name, args} do
      {"get_work_item", _} ->
        Client.get_item(pid, wid, opts)

      {"list_comments", _} ->
        Client.comments(pid, wid, opts)

      {"add_comment", %{"markdown" => text}} when is_binary(text) ->
        Client.add_comment(pid, wid, text, opts)

      {"set_state", %{"state_name" => state}} when is_binary(state) ->
        if mention?(issue), do: {:error, :mention_preserves_state}, else: Client.set_state(pid, wid, state, opts)

      _ ->
        {:error, :invalid_plane_tool_arguments}
    end
  end

  defp dispatch(_name, _args, _pid, _wid, _issue, _opts), do: {:error, :invalid_plane_tool_arguments}
  defp mention?(nil), do: false
  defp mention?(issue), do: issue.native_ref["trigger_reason"] == "mention"
  defp maybe_record_comment(_name, _args, _pid, _wid, nil, _result, _opts), do: :ok

  defp maybe_record_comment(name, args, pid, wid, issue, result, opts) do
    path = Client.project_path(issue.native_ref["project_id"], "work-items/#{issue.native_ref["work_item_id"]}/comments/", opts)
    target = pid == issue.native_ref["project_id"] and wid == issue.native_ref["work_item_id"]
    posted = (name == "add_comment" and target) or raw_comment?(name, args, path)

    if posted and successful?(result), do: Runtime.summary_written(issue.id)
    :ok
  end

  defp raw_comment?("plane_request", %{"method" => "POST", "path" => path}, expected) when is_binary(path) do
    "/" <> String.trim_leading(path, "/") == expected
  end

  defp raw_comment?(_name, _args, _expected), do: false

  defp changes_mention_state?(issue, method, body) do
    mention?(issue) and method in ["PATCH", "PUT"] and is_map(body) and Map.has_key?(body, "state")
  end

  defp successful?({:ok, %{status: status}}), do: status in 200..299
  defp successful?({:ok, _result}), do: true
  defp successful?(_result), do: false

  defp response(result) do
    payload =
      case result do
        {:ok, data} -> data
        {:error, reason} -> %{"error" => inspect(reason)}
      end

    output = Jason.encode!(payload)
    %{"success" => successful?(result), "output" => output, "contentItems" => [%{"type" => "inputText", "text" => output}]}
  end
end
