defmodule Mix.Tasks.Plane.Setup do
  use Mix.Task
  @moduledoc "Explicit CE state setup: mix plane.setup states --workflow FILE [--project ID]."
  alias SymphonyElixir.{Config, Workflow}
  alias SymphonyElixir.Plane.{Adapter, Client, Runtime}
  @shortdoc "Explicitly set up Plane project states (never starts the poller)"
  @states [{"AI Todo", "unstarted", "#a855f7"}, {"AI Doing", "started", "#3b82f6"}, {"Human Review", "started", "#f59e0b"}, {"AI Done", "completed", "#22c55e"}]

  @impl Mix.Task
  def run(args) do
    {opts, commands, invalid} =
      OptionParser.parse(args,
        strict: [workflow: :string, project: :string]
      )

    if invalid != [], do: Mix.raise("Invalid Plane setup options")
    Mix.Task.run("app.config")
    Application.ensure_all_started(:req)
    Workflow.set_workflow_file_path(opts[:workflow] || "PLANE_WORKFLOW.md")
    tracker = Config.settings!().tracker
    if tracker.kind != "plane", do: Mix.raise("This command requires a Plane workflow")
    if Adapter.validate_config(tracker) != :ok, do: Mix.raise("Invalid Plane setup configuration")
    client_opts = [tracker_settings: tracker]

    case commands do
      ["states"] -> setup_states(opts[:project], tracker, client_opts)
      _ -> Mix.raise("Usage: mix plane.setup states --workflow PLANE_WORKFLOW.md [--project ID]")
    end
  end

  defp setup_states(target, tracker, opts) do
    {:ok, projects} = Client.projects(opts)
    selected = Enum.filter(projects, &(is_nil(target) or target in [&1["id"], &1["identifier"]]))
    if selected == [], do: Mix.raise("No matching Plane projects")
    desired = @states ++ [{Runtime.error_state(tracker), "started", "#ef4444"}]
    Enum.each(selected, &ensure_project_states(&1, desired, opts))
  end

  defp ensure_project_states(project, desired, opts) do
    {:ok, states} = Client.states(project["id"], opts)
    grouped = Enum.group_by(states, &Client.normalize(&1["name"]))
    if Enum.any?(grouped, fn {_name, rows} -> length(rows) > 1 end), do: Mix.raise("Ambiguous Plane state names")

    Enum.each(desired, fn {name, group, color} ->
      existing = List.first(grouped[Client.normalize(name)] || [])
      existing = existing || legacy_working_state(name, grouped)
      path = Client.project_path(project["id"], "states/", opts)
      ensure_state(existing, path, %{"name" => name, "group" => group, "color" => color}, opts)
    end)

    Mix.shell().info("Plane states ready: #{project["identifier"] || project["id"]}")
  end

  defp legacy_working_state("AI Doing", grouped), do: List.first(grouped["ai start"] || [])
  defp legacy_working_state(_name, _grouped), do: nil
  defp ensure_state(nil, path, desired, opts), do: successful!(Client.request("POST", path, %{}, desired, opts))

  defp ensure_state(existing, path, desired, opts) do
    if existing["name"] != desired["name"] do
      successful!(Client.request("PATCH", path <> existing["id"] <> "/", %{}, %{"name" => desired["name"]}, opts))
    end
  end

  defp successful!({:ok, %{status: status} = result}) when status in 200..299, do: result
  defp successful!(_result), do: Mix.raise("Plane setup request failed")
end
