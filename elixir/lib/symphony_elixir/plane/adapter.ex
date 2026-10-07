defmodule SymphonyElixir.Plane.Adapter do
  @moduledoc "Workspace-wide Plane CE tracker with optional mention scheduling."
  @behaviour SymphonyElixir.Tracker
  alias SymphonyElixir.{Config, Plane.AgentTool, Plane.Client, Plane.Runtime}

  @spec validate_config(map()) :: :ok | {:error, term()}
  def validate_config(tracker) do
    with {:ok, config} <- Client.settings(tracker),
         true <- is_list(tracker.active_states) and is_list(tracker.terminal_states),
         true <- valid_error_state?(tracker),
         true <- is_nil(config.provider["project_ids"]) or is_list(config.provider["project_ids"]),
         true <-
           is_nil(config.provider["webhook_port"]) or
             (is_integer(config.provider["webhook_port"]) and config.provider["webhook_port"] in 1..65_535),
         true <-
           is_nil(config.provider["webhook_port"]) or
             not is_nil(Client.resolve(config.provider["webhook_secret"], "PLANE_WEBHOOK_SECRET")) do
      :ok
    else
      false -> {:error, :invalid_plane_config}
      error -> error
    end
  end

  defp valid_error_state?(tracker) do
    name = Runtime.error_state(tracker)
    excluded = tracker.active_states ++ tracker.terminal_states ++ [tracker.provider["trigger_state"] || "AI Todo", Runtime.working_state(tracker), Runtime.review_state(tracker)]
    is_binary(name) and Client.normalize(name) != "" and Client.normalize(name) not in Enum.map(excluded, &Client.normalize/1)
  end

  @spec fetch_issues_by_states([String.t()]) :: {:ok, [SymphonyElixir.Tracker.Issue.t()]} | {:error, term()}
  def fetch_issues_by_states([]), do: {:ok, []}

  def fetch_issues_by_states(states) do
    tracker = Config.settings!().tracker
    opts = [tracker_settings: tracker]

    with :ok <- Runtime.configure(tracker),
         {:ok, normal} <- Client.fetch_issues_by_states(states, opts),
         {:ok, mentions} <- Client.fetch_issues_by_ids(Runtime.pending_ids(), opts) do
      requested = Enum.map(states, &Client.normalize/1)

      {:ok,
       (normal ++ mentions)
       |> Enum.uniq_by(& &1.id)
       |> Enum.map(&Runtime.overlay/1)
       |> Enum.filter(&(Client.normalize(&1.state) in requested))}
    end
  end

  @spec fetch_issues_by_ids([String.t()]) :: {:ok, [SymphonyElixir.Tracker.Issue.t()]} | {:error, term()}
  def fetch_issues_by_ids(ids) do
    tracker = Config.settings!().tracker

    with :ok <- Runtime.configure(tracker),
         {:ok, issues} <- Client.fetch_issues_by_ids(ids, tracker_settings: tracker) do
      {:ok, Enum.map(issues, &Runtime.overlay/1)}
    end
  end

  @spec agent_tool_specs() :: [map()]
  def agent_tool_specs, do: AgentTool.tool_specs()
  @spec execute_agent_tool(String.t(), term(), keyword()) :: map()
  def execute_agent_tool(name, args, opts), do: AgentTool.execute(name, args, opts)
  @spec secret_environment_names(map()) :: [String.t()]
  def secret_environment_names(tracker), do: Client.secret_environment_names(tracker)
end
