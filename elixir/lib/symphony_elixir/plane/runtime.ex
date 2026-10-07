defmodule SymphonyElixir.Plane.Runtime do
  @moduledoc "Plane mention inbox and attempt ownership; the upstream orchestrator remains the scheduler."
  use GenServer
  require Logger
  alias SymphonyElixir.Plane.{Client, Runner, Webhook}

  @spec start_link(keyword()) :: GenServer.on_start()
  def start_link(opts), do: GenServer.start_link(__MODULE__, opts, name: __MODULE__)
  @spec configure(map()) :: :ok | {:error, term()}
  def configure(tracker), do: GenServer.call(__MODULE__, {:configure, tracker}, :infinity)
  @spec pending_ids() :: [String.t()]
  def pending_ids, do: GenServer.call(__MODULE__, :pending_ids)
  @spec overlay(SymphonyElixir.Tracker.Issue.t()) :: SymphonyElixir.Tracker.Issue.t()
  def overlay(issue), do: GenServer.call(__MODULE__, {:overlay, issue})
  @spec accept(map()) :: :ok | {:error, term()}
  def accept(payload), do: GenServer.call(__MODULE__, {:accept, payload}, :infinity)
  @spec begin_run(map()) :: :ok
  def begin_run(context), do: GenServer.call(__MODULE__, {:begin, context})
  @spec finish_run(String.t()) :: map() | nil
  def finish_run(id), do: GenServer.call(__MODULE__, {:finish, id})
  @spec run_context(String.t()) :: map() | nil
  def run_context(id), do: GenServer.call(__MODULE__, {:run_context, id})
  @spec summary_written(String.t()) :: :ok
  def summary_written(id), do: GenServer.call(__MODULE__, {:summary, id})
  @spec claim_notice?(String.t()) :: boolean()
  def claim_notice?(id), do: GenServer.call(__MODULE__, {:claim_notice, id})

  @impl true
  def init(_opts), do: {:ok, %{scope: nil, tracker: nil, bot: nil, listener: nil, pending: %{}, seen: MapSet.new(), runs: %{}, claim_failures: %{}}}

  @impl true
  def handle_call({:configure, tracker}, _from, state) do
    case Client.settings(tracker) do
      {:ok, cfg} -> configure_scope(tracker, cfg, state)
      error -> {:reply, error, state}
    end
  end

  def handle_call(:pending_ids, _from, state), do: {:reply, Map.keys(state.pending), state}

  def handle_call({:overlay, issue}, _from, state) do
    issue =
      case state.pending[issue.id] do
        nil -> issue
        comment -> %{issue | state: working_state(state.tracker), native_ref: Map.merge(issue.native_ref, %{"trigger_reason" => "mention", "mention_comment_id" => comment})}
      end

    issue = if state.runs[issue.id] && state.runs[issue.id][:finalizing], do: %{issue | dispatchable: false}, else: issue
    {:reply, issue, state}
  end

  def handle_call({:accept, _payload}, _from, %{bot: nil} = state), do: {:reply, {:error, :plane_not_configured}, state}

  def handle_call({:accept, payload}, _from, state) do
    accept_event(Webhook.parse_event(payload, state.bot), state)
  end

  def handle_call({:begin, ctx}, _from, state) do
    ctx = Map.merge(ctx, %{monitor: Process.monitor(ctx.owner), summary_written: false})
    {:reply, :ok, %{state | runs: Map.put(state.runs, ctx.issue.id, ctx)}}
  end

  def handle_call({:summary, id}, _from, state) do
    runs =
      case state.runs[id] do
        nil -> state.runs
        ctx -> Map.put(state.runs, id, %{ctx | summary_written: true})
      end

    {:reply, :ok, %{state | runs: runs}}
  end

  def handle_call({:finish, id}, _from, state) do
    {ctx, runs} = Map.pop(state.runs, id)
    if ctx, do: Process.demonitor(ctx.monitor, [:flush])
    {:reply, ctx, %{state | runs: runs, pending: Map.delete(state.pending, id)}}
  end

  def handle_call({:run_context, id}, _from, state), do: {:reply, state.runs[id], state}

  def handle_call({:claim_notice, id}, _from, state) do
    now = System.monotonic_time(:millisecond)
    allowed = not Map.has_key?(state.claim_failures, id) or now - state.claim_failures[id] >= 300_000
    failures = if allowed, do: Map.put(state.claim_failures, id, now), else: state.claim_failures
    {:reply, allowed, %{state | claim_failures: failures}}
  end

  @impl true
  def handle_info({:DOWN, monitor, :process, _pid, _reason}, state) do
    case Enum.find(state.runs, fn {_id, ctx} -> ctx.monitor == monitor end) do
      {id, ctx} ->
        owner = self()

        Task.start(fn ->
          try do
            Runner.finalize(ctx, {:error, :worker_interrupted})
          after
            send(owner, {:finalized, id, monitor})
          end
        end)

        {:noreply, %{state | runs: Map.put(state.runs, id, Map.put(ctx, :finalizing, true))}}

      nil ->
        {:noreply, state}
    end
  end

  def handle_info({:finalized, id, monitor}, state) do
    case state.runs[id] do
      %{monitor: ^monitor} ->
        {:noreply, %{state | runs: Map.delete(state.runs, id), pending: Map.delete(state.pending, id)}}

      _ ->
        {:noreply, state}
    end
  end

  @spec working_state(map()) :: String.t()
  def working_state(tracker), do: tracker.provider["working_state"] || "AI Doing"
  @spec review_state(map()) :: String.t()
  def review_state(tracker), do: tracker.provider["review_state"] || "Human Review"
  @spec error_state(map()) :: String.t()
  def error_state(tracker), do: tracker.provider["error_state"] || "AI Error"

  defp configure_scope(tracker, cfg, state) do
    scope = {cfg.api_url, cfg.workspace_slug, cfg.provider["project_ids"], :crypto.hash(:sha256, cfg.api_key), cfg.provider["webhook_port"]}

    cond do
      state.scope == scope -> {:reply, :ok, %{state | tracker: tracker}}
      map_size(state.runs) > 0 -> {:reply, {:error, :plane_scope_changed_while_running}, state}
      true -> initialize_reply(tracker, scope, state)
    end
  end

  defp initialize_reply(tracker, scope, state) do
    case initialize_scope(tracker, state.listener) do
      {:ok, bot, listener} ->
        next = %{state | scope: scope, tracker: tracker, bot: bot, listener: listener, pending: %{}, seen: MapSet.new()}
        {:reply, :ok, next}

      error ->
        {:reply, error, state}
    end
  end

  defp accept_event(%{id: id, comment_id: comment}, state) do
    duplicate = MapSet.member?(state.seen, comment) or Map.has_key?(state.runs, id) or Map.has_key?(state.pending, id)
    if duplicate, do: {:reply, :ok, state}, else: enqueue_mention(id, comment, state)
  end

  defp accept_event(_event, state), do: {:reply, :ok, state}

  defp enqueue_mention(id, comment, state) do
    case Client.fetch_issues_by_ids([id], tracker_settings: state.tracker) do
      {:ok, [_issue]} ->
        next = %{state | pending: Map.put(state.pending, id, comment), seen: MapSet.put(state.seen, comment)}
        {:reply, :ok, next}

      {:ok, []} ->
        {:reply, :ok, state}

      error ->
        {:reply, error, state}
    end
  end

  defp initialize_scope(tracker, listener) do
    opts = [tracker_settings: tracker]

    with {:ok, %{status: 200, body: %{"id" => bot}}} <- Client.request("GET", "/users/me/", %{}, nil, opts),
         :ok <- recover_orphans(tracker),
         {:ok, new_listener} <- start_webhook(tracker, listener) do
      {:ok, bot, new_listener}
    else
      {:ok, _response} -> {:error, :plane_identity_unavailable}
      error -> error
    end
  end

  defp recover_orphans(tracker) do
    opts = [tracker_settings: tracker]

    with {:ok, issues} <- Client.fetch_issues_by_states([working_state(tracker)], opts) do
      Enum.reduce_while(issues, :ok, &recover_item(&1, &2, tracker, opts))
    end
  end

  defp recover_item(issue, :ok, tracker, opts) do
    ref = issue.native_ref
    name = tracker.provider["trigger_state"] || "AI Todo"

    case Client.set_state(ref["project_id"], ref["work_item_id"], name, opts) do
      {:ok, _} ->
        Logger.info("Recovered Plane work item issue_id=#{issue.id} issue_identifier=#{issue.identifier}")
        {:cont, :ok}

      error ->
        {:halt, error}
    end
  end

  defp start_webhook(tracker, listener) do
    if listener && Process.alive?(listener), do: GenServer.stop(listener)

    if port = tracker.provider["webhook_port"] do
      secret = Client.resolve(tracker.provider["webhook_secret"], "PLANE_WEBHOOK_SECRET")
      Bandit.start_link(plug: {Webhook, secret: secret}, port: port, ip: {0, 0, 0, 0})
    else
      {:ok, nil}
    end
  end
end
