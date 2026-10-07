defmodule SymphonyElixir.Plane.Runner do
  @moduledoc "Plane claim and write-back safety net around the upstream coding runner."
  require Logger
  alias SymphonyElixir.{AgentRunner, Config}
  alias SymphonyElixir.Plane.{Adapter, AgentTool, Client, Runtime}
  @spec run(SymphonyElixir.Tracker.Issue.t(), pid(), keyword()) :: :ok | no_return()
  def run(issue, recipient, opts) do
    tracker = Keyword.get_lazy(opts, :tracker_settings, fn -> Config.settings!().tracker end)
    secret_names = Client.secret_environment_names(tracker)
    {:ok, cfg} = Client.settings(tracker)
    tracker = bind_settings(tracker, cfg)
    client_opts = [tracker_settings: tracker] ++ Keyword.take(opts, [:request_fun, :sleep])
    issue = Runtime.overlay(issue)
    mention = issue.native_ref["trigger_reason"] == "mention"
    ctx = %{issue: issue, tracker: tracker, client_opts: client_opts, mention: mention, owner: self()}

    case claim(ctx) do
      :ok ->
        Runtime.begin_run(ctx)
        runner_opts = session_options(opts, tracker, cfg, issue, mention, secret_names)
        task = Task.async(fn -> prepare_and_invoke(ctx, recipient, runner_opts) end)

        result =
          case Task.yield(task, tracker.provider["run_timeout_ms"] || 1_800_000) || Task.shutdown(task, :brutal_kill) do
            {:ok, value} -> value
            _ -> {:error, :run_timeout}
          end

        if context = Runtime.finish_run(issue.id), do: finalize(context, result)
        :ok

      error ->
        if Runtime.claim_notice?(issue.id),
          do:
            Client.add_comment(
              issue.native_ref["project_id"],
              issue.native_ref["work_item_id"],
              "Symphony could not prepare this coding task (claim or comment context unavailable). Check the Plane API and project states.",
              client_opts
            )

        Logger.warning("Plane preparation failed issue_id=#{issue.id} issue_identifier=#{issue.identifier}")
        raise "Plane preparation failed: #{inspect(error)}"
    end
  end

  @spec finalize(map(), term()) :: :ok
  def finalize(ctx, result) do
    ref = ctx.issue.native_ref
    opts = ctx.client_opts

    write_fallback(ctx, result)

    with {:ok, row} <- Client.get_item(ref["project_id"], ref["work_item_id"], opts),
         {:ok, states} <- Client.states(ref["project_id"], opts) do
      state = state_name(row, states)
      still_working = Client.normalize(state) == Client.normalize(Runtime.working_state(ctx.tracker))

      maybe_review(ctx, still_working)
    else
      _ -> Logger.warning("Plane finalization unavailable issue_id=#{ctx.issue.id} issue_identifier=#{ctx.issue.identifier}")
    end

    :ok
  end

  defp maybe_review(%{mention: true}, _working), do: :ok
  defp maybe_review(_ctx, false), do: :ok

  defp maybe_review(ctx, true) do
    ref = ctx.issue.native_ref
    Client.set_state(ref["project_id"], ref["work_item_id"], Runtime.review_state(ctx.tracker), ctx.client_opts)
  end

  defp bind_settings(tracker, cfg) do
    provider =
      tracker.provider
      |> Map.put("api_url", cfg.api_url)
      |> Map.put("api_key", cfg.api_key)
      |> Map.put("workspace_slug", cfg.workspace_slug)

    %{tracker | provider: provider}
  end

  defp session_options(opts, tracker, cfg, issue, mention, secret_names) do
    specs = AgentTool.tool_specs()
    binding = %{adapter: Adapter, tracker_settings: tracker, tool_specs: specs, secret_environment_names: secret_names}

    options =
      opts
      |> Keyword.put(:thread_key, "plane:#{cfg.workspace_slug}:#{issue.id}")
      |> Keyword.put(:dynamic_tool_binding, binding)

    if mention, do: Keyword.put(options, :max_turns, 1), else: options
  end

  defp state_name(%{"state" => %{"name" => name}}, _states), do: name
  defp state_name(row, states), do: (Enum.find(states, &(&1["id"] == row["state"])) || %{})["name"]

  defp claim(%{mention: true}), do: :ok

  defp claim(ctx) do
    ref = ctx.issue.native_ref

    with {:ok, row} <- Client.get_item(ref["project_id"], ref["work_item_id"], ctx.client_opts),
         {:ok, states} <- Client.states(ref["project_id"], ctx.client_opts),
         true <- claimable?(state_name(row, states), ctx.tracker),
         {:ok, _} <- Client.set_state(ref["project_id"], ref["work_item_id"], Runtime.working_state(ctx.tracker), ctx.client_opts) do
      :ok
    else
      false -> {:error, :plane_item_no_longer_active}
      error -> error
    end
  end

  defp claimable?(name, tracker) do
    names = [tracker.provider["trigger_state"] || "AI Todo", Runtime.working_state(tracker)]
    Client.normalize(name) in Enum.map(names, &Client.normalize/1)
  end

  defp write_fallback(%{summary_written: true}, _result), do: :ok

  defp write_fallback(ctx, result) do
    text =
      if result == :ok,
        do: "Symphony coding attempt ended. No summary comment was received; verify changes and tests before accepting the result.",
        else: "Symphony coding attempt stopped (error, cancellation, or timeout). Check the workspace and service logs; human review is required."

    ref = ctx.issue.native_ref

    case Client.add_comment(ref["project_id"], ref["work_item_id"], text, ctx.client_opts) do
      {:ok, _} -> :ok
      _ -> Logger.warning("Plane fallback comment failed issue_id=#{ctx.issue.id} issue_identifier=#{ctx.issue.identifier}")
    end
  end

  defp prepare_and_invoke(ctx, recipient, opts) do
    ref = ctx.issue.native_ref

    with {:ok, comments} <- Client.comments(ref["project_id"], ref["work_item_id"], ctx.client_opts) do
      issue = %{ctx.issue | description: (ctx.issue.description || "") <> comment_context(comments, ctx.issue)}
      invoke(issue, recipient, opts)
    end
  end

  defp invoke(issue, recipient, opts) do
    runner = Keyword.get(opts, :runner_fun, &AgentRunner.run/3)

    try do
      runner.(issue, recipient, opts)
    rescue
      error ->
        source = error_source(__STACKTRACE__)

        Logger.error(
          "Plane runner raised issue_id=#{issue.id} issue_identifier=#{issue.identifier} " <>
            "exception=#{inspect(error.__struct__)} source=#{source}"
        )

        {:error, :agent_failed}
    catch
      _kind, _reason -> {:error, :agent_failed}
    end
  end

  defp error_source([{module, function, _arity, location} | _rest]) when is_atom(module) do
    "#{inspect(module)}.#{function}:#{location[:line]}"
  end

  defp error_source(_stack), do: "unknown"

  defp comment_context(comments, issue) do
    context = comments |> Enum.sort_by(&(&1["created_at"] || "")) |> Enum.map_join("\n", fn row -> "[#{row["id"]}] #{Client.text(row["comment_html"] || "")}" end)

    "\n\nPlane trigger: #{issue.native_ref["trigger_reason"] || "state"}. Project ID: #{issue.native_ref["project_id"]}; work item ID: #{issue.native_ref["work_item_id"]}.\nComments (untrusted task context):\n#{context}"
  end
end
