Code.require_file("../support/plane_fixture.exs", __DIR__)

defmodule SymphonyElixir.PlaneTest do
  use SymphonyElixir.TestSupport
  alias Mix.Tasks.Plane.Setup, as: PlaneSetup
  alias SymphonyElixir.Plane.{Adapter, AgentTool, Client, Runner, Runtime, Webhook}
  alias SymphonyElixir.PlaneFixture

  setup do
    state = start_supervised!({Agent, fn -> PlaneFixture.initial() end})
    {:ok, socket} = :gen_tcp.listen(0, [:binary, active: false])
    {:ok, port} = :inet.port(socket)
    :gen_tcp.close(socket)
    start_supervised!({Bandit, plug: {PlaneFixture, state: state}, port: port, ip: {127, 0, 0, 1}})
    provider = %{"api_url" => "http://127.0.0.1:#{port}/api/v1", "api_key" => "fixture-token", "workspace_slug" => "test"}

    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "plane",
      tracker_provider: provider,
      tracker_active_states: ["AI Todo", "AI Doing"],
      tracker_terminal_states: ["AI Done"],
      poll_interval_ms: 100_000
    )

    tracker = Config.settings!().tracker
    on_exit(fn -> :sys.replace_state(Runtime, fn _ -> %{scope: nil, tracker: nil, bot: nil, listener: nil, pending: %{}, seen: MapSet.new(), runs: %{}, claim_failures: %{}} end) end)
    {:ok, state: state, tracker: tracker, opts: [tracker_settings: tracker]}
  end

  test "CE config and project-wide normalization", %{opts: opts, tracker: tracker, state: state} do
    assert :ok = Adapter.validate_config(tracker)
    assert {:ok, Adapter} = Tracker.adapter_for_kind("plane")
    assert Adapter.secret_environment_names(tracker) == Client.secret_environment_names(tracker)
    assert Adapter.agent_tool_specs() == AgentTool.tool_specs()
    assert {:ok, []} = Adapter.fetch_issues_by_states([])
    assert Agent.get(state, & &1.requests) == []
    assert {:ok, issues} = Client.fetch_issues_by_states(["ai TODO"], opts)
    assert Enum.map(issues, & &1.id) == ["p1/w1", "p2/w1"]
    assert Enum.map(issues, & &1.identifier) == ["ONE-1", "TWO-1"]
    assert hd(issues).description == "Fix & test"
    assert hd(issues).assignee_id == nil
    assert hd(issues).labels == ["code"]
    assert hd(issues).priority == 2
    assert {:ok, [_]} = Client.fetch_issues_by_ids(["p2/w1"], opts)
    assert {:ok, []} = Client.fetch_issues_by_ids(["p1/missing", "other/w1"], opts)
    assert {:error, :invalid_plane_issue_id} = Client.fetch_issues_by_ids(["bad"], opts)
    assert {:ok, _} = Client.set_state("p2", "w1", "ai doing", opts)
    assert Agent.get(state, & &1.items[{"p2", "w1"}]["state"]) == "p2AI Doing"
    assert {:error, :plane_state_not_found} = Client.set_state("p2", "w1", "unknown", opts)
    Agent.update(state, &put_in(&1, [:states, "p2"], [%{"name" => "AI Doing"}, %{"name" => "AI DOING"}]))
    assert {:error, :plane_state_ambiguous} = Client.set_state("p2", "w1", "ai doing", opts)
    invalid = %{tracker | provider: Map.put(tracker.provider, "api_url", "https://plane.test/api/v2")}
    assert {:error, :invalid_plane_ce_api_url} = Adapter.validate_config(invalid)

    for {key, value} <- [{"project_ids", "bad"}, {"webhook_port", -1}, {"webhook_port", 1234}] do
      assert {:error, :invalid_plane_config} = Adapter.validate_config(%{tracker | provider: Map.put(tracker.provider, key, value)})
    end

    assert "CUSTOM_SECRET" in Client.secret_environment_names(%{provider: %{"api_key" => "$CUSTOM_SECRET"}})
  end

  test "pagination, bounded rate-limit retries, and relative URL safety", %{opts: opts} do
    owner = self()

    transport = fn _, path, query, _, _ ->
      send(owner, {:request, path, query})

      case query["cursor"] do
        nil -> {:ok, %{status: 200, body: %{"results" => [%{"id" => 1}], "next_page_results" => true, "next_cursor" => "next"}}}
        "next" -> {:ok, %{status: 200, body: %{"results" => [%{"id" => 2}], "next_page_results" => false}}}
      end
    end

    assert {:ok, [%{"id" => 1}, %{"id" => 2}]} = Client.paginate("/workspaces/test/projects/", %{}, opts ++ [request_fun: transport])
    assert_received {:request, _, %{"per_page" => 100}}
    assert_received {:request, _, %{"cursor" => "next", "per_page" => 100}}

    rate = fn _, _, _, _, _ ->
      send(owner, :rate)
      {:ok, %{status: 429, headers: %{"retry-after" => ["999"]}, body: %{}}}
    end

    rate_opts = opts ++ [request_fun: rate, sleep: &send(owner, {:delay, &1})]
    assert {:ok, %{status: 429}} = Client.request("GET", "/users/me/", %{}, nil, rate_opts)
    for _ <- 1..4, do: assert_received(:rate)
    for _ <- 1..3, do: assert_received({:delay, 60_000})

    for path <- ["https://evil.test", "//evil.test", "/%2e%2e/users", "foo?bar", nil] do
      assert {:error, :invalid_plane_request} = Client.request("GET", path, %{}, nil, opts)
    end

    loop = fn _, _, _, _, _ -> {:ok, %{status: 200, body: %{"results" => [], "next_page_results" => true, "next_cursor" => "same"}}} end
    assert {:error, :plane_cursor_repeated} = Client.paginate("/x", %{}, opts ++ [request_fun: loop])
  end

  test "host tools expose convenience reads, comments, state and raw CE requests", %{opts: opts, state: state} do
    {:ok, [issue | _]} = Client.fetch_issues_by_ids(["p1/w1"], opts)
    bound = opts ++ [issue: issue]

    for {name, args} <- [
          {"get_me", %{}},
          {"get_work_item", %{}},
          {"list_comments", %{}},
          {"list_states", %{}},
          {"search_work_items", %{"query" => "bug"}},
          {"add_comment", %{"markdown" => "<script>\nDone"}},
          {"set_state", %{"state_name" => "AI Doing"}},
          {"plane_request", %{"method" => "GET", "path" => "/users/me/"}}
        ] do
      assert Adapter.execute_agent_tool(name, args, bound)["success"]
    end

    assert hd(Agent.get(state, & &1.comments[{"p1", "w1"}]))["comment_html"] == "<p>&lt;script&gt;<br>Done</p>"
    assert Enum.all?(Agent.get(state, & &1.requests), fn {_m, _p, _q, _b, auth} -> auth == ["fixture-token"] end)

    for {name, args} <- [{"unknown", %{}}, {"add_comment", %{}}, {"plane_request", %{"method" => "GET", "path" => "/x", "query" => []}}, {"get_me", nil}, {"set_state", %{"state_name" => "unknown"}}] do
      refute AgentTool.execute(name, args, bound)["success"]
    end

    refute AgentTool.execute("get_work_item", %{}, opts)["success"]
    mention = %{issue | native_ref: Map.put(issue.native_ref, "trigger_reason", "mention")}
    refute AgentTool.execute("set_state", %{"state_name" => "AI Done"}, opts ++ [issue: mention])["success"]
    refute AgentTool.execute("plane_request", %{"method" => "PATCH", "path" => "/x", "body" => %{"state" => "id"}}, opts ++ [issue: mention])["success"]
    assert AgentTool.execute("plane_request", %{"method" => "PATCH", "path" => "/x", "body" => %{}}, opts)["success"]

    assert AgentTool.execute("plane_request", %{"method" => "POST", "path" => "/workspaces/test/projects/p1/work-items/w1/comments/", "body" => %{"comment_html" => "<p>raw summary</p>"}}, bound)[
             "success"
           ]

    assert Enum.count(AgentTool.tool_specs()) == 8
  end

  test "signed mentions share scheduling, preserve completed state, and suppress bot loops", %{opts: opts, tracker: tracker, state: state} do
    :ok = Runtime.configure(tracker)
    Agent.update(state, &put_in(&1, [:items, {"p1", "w1"}, "state"], "p1AI Done"))
    payload = mention()
    body = Jason.encode!(payload)
    signature = :crypto.mac(:hmac, :sha256, "secret", body) |> Base.encode16(case: :lower)
    assert Webhook.verify_signature("secret", body, signature)
    refute Webhook.verify_signature("secret", body <> " ", signature)
    assert Webhook.parse_event(payload, "bot") == %{id: "p1/w1", comment_id: "mention1"}
    refute Webhook.parse_event(put_in(payload, ["activity", "actor"], %{"id" => "bot"}), "bot")
    conn = Plug.Test.conn(:post, "/webhook", body) |> Plug.Conn.put_req_header("x-plane-signature", signature)
    assert Webhook.call(conn, secret: "secret").status == 200
    assert Webhook.call(Plug.Test.conn(:post, "/webhook", body), secret: "secret").status == 401
    assert Webhook.call(Plug.Test.conn(:get, "/other"), secret: "secret").status == 404
    :ok = Runtime.accept(payload)
    assert Runtime.pending_ids() == ["p1/w1"]
    assert {:ok, issues} = Adapter.fetch_issues_by_states(["AI Doing"])
    assert [%{id: "p1/w1", state: "AI Doing"}] = issues
    {:ok, [issue]} = Adapter.fetch_issues_by_ids(["p1/w1"])
    assert issue.native_ref["actual_state"] == "AI Done"

    assert :ok =
             Runner.run(
               issue,
               self(),
               opts ++
                 [
                   runner_fun: fn prepared, _, run_opts ->
                     assert prepared.description =~ "Plane trigger: mention"
                     assert run_opts[:max_turns] == 1
                     :ok
                   end
                 ]
             )

    assert Runtime.pending_ids() == []
    assert Agent.get(state, & &1.items[{"p1", "w1"}]["state"]) == "p1AI Done"
    assert length(Agent.get(state, & &1.comments[{"p1", "w1"}])) == 1
    :ok = Runtime.accept(payload)
    assert Runtime.pending_ids() == []
  end

  test "coding worker and comment-context failures enter human review", %{opts: opts, state: state} do
    {:ok, [issue]} = Client.fetch_issues_by_ids(["p1/w1"], opts)
    assert :ok = Runner.run(issue, self(), opts ++ [runner_fun: fn _, _, _ -> raise "failure" end])
    assert Agent.get(state, & &1.items[{"p1", "w1"}]["state"]) == "p1Human Review"
    assert length(Agent.get(state, & &1.comments[{"p1", "w1"}])) == 1
    path = "/api/v1/workspaces/test/projects/p1/work-items/w1/comments/"
    Agent.update(state, &put_in(&1, [:items, {"p1", "w1"}, "state"], "p1AI Todo"))
    Agent.update(state, &put_in(&1, [:errors, {"GET", path}], 500))
    assert :ok = Runner.run(issue, self(), opts ++ [runner_fun: fn _, _, _ -> flunk("must not run without context") end])
    assert Agent.get(state, & &1.items[{"p1", "w1"}]["state"]) == "p1Human Review"
  end

  test "Plane does not impose an execution deadline on the upstream worker", %{opts: opts, state: state} do
    {:ok, [issue]} = Client.fetch_issues_by_ids(["p1/w1"], opts)
    tracker = opts[:tracker_settings]
    tracker = %{tracker | provider: Map.put(tracker.provider, "run_timeout_ms", 1)}
    assert :ok = Adapter.validate_config(tracker)
    parent = self()

    {worker, monitor} =
      spawn_monitor(fn ->
        result =
          Runner.run(issue, parent,
            tracker_settings: tracker,
            runner_fun: fn _, _, _ ->
              send(parent, {:coding_started, self()})

              receive do
                :finish_coding -> :ok
              end
            end
          )

        send(parent, {:coding_finished, result})
      end)

    assert_receive {:coding_started, ^worker}
    refute_receive {:DOWN, ^monitor, :process, ^worker, _reason}, 50
    assert Agent.get(state, & &1.items[{"p1", "w1"}]["state"]) == "p1AI Doing"
    send(worker, :finish_coding)
    assert_receive {:coding_finished, :ok}
    assert_receive {:DOWN, ^monitor, :process, ^worker, :normal}
  end

  test "startup orphan recovery is project scoped and idempotent", %{tracker: tracker, state: state} do
    Agent.update(state, &put_in(&1, [:items, {"p2", "w1"}, "state"], "p2AI Doing"))
    assert :ok = Runtime.configure(tracker)
    assert Agent.get(state, & &1.items[{"p2", "w1"}]["state"]) == "p2AI Todo"
    count = Agent.get(state, &length(&1.requests))
    assert :ok = Runtime.configure(tracker)
    assert Agent.get(state, &length(&1.requests)) == count
    assert Runtime.claim_notice?("failed")
    refute Runtime.claim_notice?("failed")
  end

  test "worker success keeps its summary and chosen final state", %{opts: opts, state: state} do
    {:ok, [issue]} = Client.fetch_issues_by_ids(["p1/w1"], opts)

    runner = fn prepared, _, run_opts ->
      assert prepared.description =~ "Fix & test"
      refute Keyword.has_key?(run_opts, :thread_key)
      assert run_opts[:dynamic_tool_binding].tracker_settings.provider["api_key"] == "fixture-token"
      assert AgentTool.execute("add_comment", %{"markdown" => "Implemented; tests passed."}, opts ++ [issue: prepared])["success"]
      assert AgentTool.execute("set_state", %{"state_name" => "AI Done"}, opts ++ [issue: prepared])["success"]
      :ok
    end

    assert :ok = Tracker.run_agent(issue, self(), opts ++ [runner_fun: runner])
    assert length(Agent.get(state, & &1.comments[{"p1", "w1"}])) == 1
    assert Agent.get(state, & &1.items[{"p1", "w1"}]["state"]) == "p1AI Done"
  end

  test "worker termination cancels linked work and emits one fallback", %{opts: opts, state: state} do
    {:ok, [issue]} = Client.fetch_issues_by_ids(["p1/w1"], opts)
    parent = self()

    {worker, monitor} =
      spawn_monitor(fn ->
        Runner.run(
          issue,
          parent,
          opts ++
            [
              runner_fun: fn _, _, _ ->
                Task.start_link(fn ->
                  send(parent, {:linked_work, self()})
                  Process.sleep(:infinity)
                end)

                send(parent, {:running_worker, self()})
                Process.sleep(:infinity)
              end
            ]
        )
      end)

    assert_receive {:running_worker, ^worker}
    assert_receive {:linked_work, child}
    child_monitor = Process.monitor(child)
    Process.exit(worker, :kill)
    assert_receive {:DOWN, ^monitor, :process, ^worker, :killed}
    assert_receive {:DOWN, ^child_monitor, :process, ^child, _reason}
    await(fn -> Agent.get(state, & &1.items[{"p1", "w1"}]["state"]) == "p1Human Review" end)
    assert length(Agent.get(state, & &1.comments[{"p1", "w1"}])) == 1
  end

  test "stale claims cannot revive a completed task, and claim notices have backoff", %{opts: opts, state: state} do
    {:ok, [issue]} = Client.fetch_issues_by_ids(["p1/w1"], opts)
    Agent.update(state, &put_in(&1, [:items, {"p1", "w1"}, "state"], "p1AI Done"))

    for _ <- 1..2 do
      assert_raise RuntimeError, fn -> Runner.run(issue, self(), opts ++ [runner_fun: fn _, _, _ -> flunk("stale task ran") end]) end
    end

    assert Agent.get(state, & &1.items[{"p1", "w1"}]["state"]) == "p1AI Done"
    assert length(Agent.get(state, & &1.comments[{"p1", "w1"}])) == 1
  end

  test "app-server starts a fresh thread for each worker session in the same workspace" do
    root = Path.join(System.tmp_dir!(), "plane-thread-#{System.unique_integer([:positive])}")
    workspace = Path.join(root, "workspaces/ONE-1")
    File.mkdir_p!(workspace)
    script = Path.join(root, "fake_codex.py")
    log = Path.join(root, "protocol.jsonl")

    File.write!(script, """
    import sys,json,uuid
    for line in sys.stdin:
      p=json.loads(line)
      with open(sys.argv[1], 'a') as f: f.write(line)
      if 'id' not in p: continue
      result={}
      if p['method']=='thread/start': result={'thread':{'id':str(uuid.uuid4())}}
      print(json.dumps({'id':p['id'],'result':result}),flush=True)
    """)

    binding = Tracker.bind_agent_tools()
    write_workflow_file!(Workflow.workflow_file_path(), workspace_root: Path.join(root, "workspaces"), codex_command: "python3 #{script} #{log}")
    assert {:ok, first} = AppServer.start_session(workspace, dynamic_tool_binding: binding)
    :ok = AppServer.stop_session(first)
    assert {:ok, second} = AppServer.start_session(workspace, dynamic_tool_binding: binding)
    :ok = AppServer.stop_session(second)
    refute first.thread_id == second.thread_id
    messages = File.read!(log) |> String.split("\n", trim: true) |> Enum.map(&Jason.decode!/1)
    assert Enum.count(messages, &(&1["method"] == "thread/start")) == 2
    refute Enum.any?(messages, &(&1["method"] in ["thread/list", "thread/resume", "thread/name/set"]))
    File.rm_rf!(root)
  end

  test "explicit setup creates and normalizes states without starting coding", %{state: state} do
    workflow = Workflow.workflow_file_path()
    Agent.update(state, &put_in(&1, [:states, "p1"], [%{"id" => "old", "name" => "AI Start"}, %{"id" => "todo", "name" => "AI TODO"}]))
    PlaneSetup.run(["states", "--workflow", workflow, "--project", "ONE"])
    names = Agent.get(state, &Enum.map(&1.states["p1"], fn row -> row["name"] end))
    assert Enum.sort(names) == Enum.sort(["AI Todo", "AI Doing", "Human Review", "AI Done"])
    assert Enum.any?(Agent.get(state, & &1.requests), fn {m, _p, _q, _b, _h} -> m == "POST" end)
    PlaneSetup.run(["states", "--workflow", workflow])
    assert_raise Mix.Error, fn -> PlaneSetup.run(["states", "--workflow", workflow, "--project", "absent"]) end
    Agent.update(state, &update_in(&1, [:states, "p1"], fn rows -> rows ++ [%{"name" => "AI TODO"}] end))
    assert_raise Mix.Error, fn -> PlaneSetup.run(["states", "--workflow", workflow]) end
    assert_raise Mix.Error, fn -> PlaneSetup.run(["bad", "--workflow", workflow]) end
    assert_raise Mix.Error, fn -> PlaneSetup.run(["states", "--bad"]) end
    Agent.update(state, &put_in(&1, [:errors, {"POST", "/api/v1/workspaces/test/projects/p1/states/"}], 500))
    Agent.update(state, &put_in(&1, [:states, "p1"], []))
    assert_raise Mix.Error, fn -> PlaneSetup.run(["states", "--workflow", workflow]) end
    write_workflow_file!(workflow, tracker_kind: "memory")
    assert_raise Mix.Error, fn -> PlaneSetup.run(["states", "--workflow", workflow]) end
  end

  test "the shipped coding workflow renders real normalized Plane context", %{opts: opts} do
    {:ok, [issue]} = Client.fetch_issues_by_ids(["p1/w1"], opts)
    template = File.read!(Path.expand("../../PLANE_WORKFLOW.md", __DIR__))
    tracker = opts[:tracker_settings]
    template = template |> String.replace("$PLANE_BASE_URL", tracker.provider["api_url"]) |> String.replace("$PLANE_API_TOKEN", "fixture-token") |> String.replace("$PLANE_WORKSPACE_SLUG", "test")
    File.write!(Workflow.workflow_file_path(), template)
    WorkflowStore.force_reload()
    issue = %{issue | title: "中文编码任务", description: "修复功能并验证测试"}
    prompt = PromptBuilder.build_prompt(issue)
    assert String.valid?(prompt)
    assert {:ok, _json} = Jason.encode(%{"prompt" => prompt})
    assert prompt =~ "Trigger: state"
    assert prompt =~ "Project ID: p1"
    assert prompt =~ "Work item ID: w1"
    assert prompt =~ "修复功能并验证测试"
  end

  test "the Plane wrapper executes the upstream coding runner through real stdio", %{opts: opts} do
    {:ok, [issue]} = Client.fetch_issues_by_ids(["p1/w1"], opts)
    root = Path.dirname(Workflow.workflow_file_path())
    script = Path.join(root, "codex_turn.py")

    File.write!(script, """
    import sys,json
    for line in sys.stdin:
      p=json.loads(line)
      if 'id' not in p: continue
      result={}
      if p['method']=='thread/start': result={'thread':{'id':'thread1'}}
      if p['method']=='turn/start': result={'turn':{'id':'turn1'}}
      print(json.dumps({'id':p['id'],'result':result}),flush=True)
      if p['method']=='turn/start':
        print(json.dumps({'method':'turn/completed','params':{'turn':{'id':'turn1','status':'completed'}}}),flush=True)
    """)

    prompt = File.read!(Path.expand("../../PLANE_WORKFLOW.md", __DIR__)) |> String.split("---\n", parts: 3) |> List.last()
    tracker = opts[:tracker_settings]

    write_workflow_file!(Workflow.workflow_file_path(),
      tracker_kind: "plane",
      tracker_provider: tracker.provider,
      tracker_active_states: ["AI Todo", "AI Doing"],
      tracker_terminal_states: ["AI Done"],
      workspace_root: Path.join(root, "workspaces"),
      codex_command: "python3 #{script}",
      prompt: prompt
    )

    assert :ok = Runtime.configure(tracker)
    assert :ok = Runner.run(issue, self(), opts ++ [max_turns: 1])
    assert_received {:codex_worker_update, _, %{event: :session_started}}
    assert {:ok, comments} = Client.comments("p1", "w1", opts)
    assert List.last(comments)["comment_html"] =~ "attempt ended"
  end

  test "resolved credentials remain bound and custom token variables stay excluded", %{opts: opts} do
    variable = "SYMPHONY_PLANE_TEST_TOKEN"
    original = System.get_env(variable)
    on_exit(fn -> restore_env(variable, original) end)
    System.put_env(variable, "fixture-token")
    {:ok, [issue]} = Client.fetch_issues_by_ids(["p1/w1"], opts)
    tracker = opts[:tracker_settings]
    tracker = %{tracker | provider: Map.put(tracker.provider, "api_key", "$" <> variable)}

    runner = fn prepared, _, run_opts ->
      binding = run_opts[:dynamic_tool_binding]
      assert variable in binding.secret_environment_names
      System.put_env(variable, "rotated-token")
      assert binding.tracker_settings.provider["api_key"] == "fixture-token"
      assert Tracker.execute_bound_agent_tool(binding, "add_comment", %{"markdown" => "verified"}, issue: prepared)["success"]
      :ok
    end

    assert :ok = Runner.run(issue, self(), tracker_settings: tracker, runner_fun: runner)
  end

  defp await(fun, attempts \\ 100)

  defp await(fun, attempts) when attempts > 0 do
    if fun.() do
      :ok
    else
      Process.sleep(10)
      await(fun, attempts - 1)
    end
  end

  defp await(_fun, 0), do: flunk("observable effect did not arrive")

  defp mention do
    %{
      "event" => "issue_comment",
      "action" => "created",
      "activity" => %{"actor" => "human"},
      "data" => %{"id" => "mention1", "project" => "p1", "work_item" => "w1", "created_by" => "human", "comment_html" => "<mention-component entity_identifier=\"bot\"></mention-component> Fix again"}
    }
  end
end
